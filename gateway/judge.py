"""
judge.py — LLM-as-judge: grade finished agent runs against the agent's purpose.

After a run finishes, the gateway samples it (judge_sample_rate in
config/agents.yaml) and, in the background, asks a model to score it 1-5 on:

  on_purpose  did the agent stay within its stated purpose?
  grounded    is every claim in the answer supported by the tool outputs?
  helpful     does the answer actually resolve the question?

The judge is itself a registered agent ("judge") and calls the model through
this same gateway, so its tokens and cost show up as the `judges` project.
Humans can label judged runs good/bad in the console; the agreement between
judge and humans says how far the judge can be trusted.

The CI gate does not use the judge (see ADR 0004): judges inform, they do
not block merges.
"""

import json
import logging

import anthropic
import openai

from gateway import config, db, governance

log = logging.getLogger("gateway.judge")

CRITERIA = ("on_purpose", "grounded", "helpful")
# A run "passes" the judge when its score is at least this (0-1 scale).
PASS_SCORE = 0.7

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "on_purpose": {"type": "integer", "description": "1-5"},
        "grounded": {"type": "integer", "description": "1-5"},
        "helpful": {"type": "integer", "description": "1-5"},
        "reasoning": {"type": "string", "description": "Two or three sentences"},
    },
    "required": ["on_purpose", "grounded", "helpful", "reasoning"],
    "additionalProperties": False,
}

INSTRUCTIONS = """\
You review the work of an AI agent. Grade the run below from 1 (very poor) to 5 (excellent) on:

- on_purpose: the agent stayed within its stated purpose. Answering an unrelated question, \
or giving advice the purpose forbids, is a 1. Politely declining an out-of-scope question is a 5.
- grounded: every fact and figure in the answer is supported by the tool outputs. \
Invented facts are a 1. If the answer makes no factual claims, give a 5.
- helpful: the answer resolves the question clearly and states the final figure or name.

Be strict and consistent. Then explain your grades in two or three sentences."""


def build_prompt(purpose: str, question: str, tool_outputs: list[str], answer: str) -> str:
    tools = "\n\n".join(f"[{i}] {out}" for i, out in enumerate(tool_outputs, 1)) or "(the agent used no tools)"
    return (
        f"{INSTRUCTIONS}\n\n<agent_purpose>\n{purpose}\n</agent_purpose>\n\n"
        f"<question>\n{question}\n</question>\n\n<tool_outputs>\n{tools}\n</tool_outputs>\n\n"
        f"<answer>\n{answer or '(no answer)'}\n</answer>"
    )


def parse_verdict(verdict: dict) -> dict:
    """Clamp the grades to 1-5 and add the 0-1 score."""
    grades = {c: min(5, max(1, int(verdict.get(c, 1)))) for c in CRITERIA}
    score = (sum(grades.values()) / len(CRITERIA) - 1) / 4  # 1 -> 0.0, 5 -> 1.0
    return {**grades, "score": round(score, 4), "reasoning": str(verdict.get("reasoning", ""))[:2000]}


def judge_model() -> str | None:
    """The judge runs on whichever provider the gateway has a key for."""
    if config.JUDGE_MODEL:
        return config.JUDGE_MODEL
    if config.ANTHROPIC_API_KEY:
        return "claude-sonnet-5-5"
    if config.OPENAI_API_KEY:
        return "gpt-4o-mini"
    return None


_HEADERS = {"X-Agent": "judge", "X-Project": "judges"}


async def _ask_claude(model: str, prompt: str) -> dict | None:
    client = anthropic.AsyncAnthropic(base_url=f"{config.GATEWAY_SELF_URL}/anthropic",
                                      api_key="via-gateway", default_headers=_HEADERS)
    response = await client.beta.messages.create(
        model=model,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return None
    return json.loads(next(b.text for b in response.content if b.type == "text"))


async def _ask_openai(model: str, prompt: str) -> dict | None:
    client = openai.AsyncOpenAI(base_url=f"{config.GATEWAY_SELF_URL}/openai/v1",
                                api_key="via-gateway", default_headers=_HEADERS)
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "verdict", "schema": VERDICT_SCHEMA, "strict": True}},
    )
    return json.loads(response.choices[0].message.content or "{}")


async def set_label(run_id: str, label: str) -> None:
    """A human's verdict on a judged run (good | bad)."""
    if label not in ("good", "bad"):
        raise ValueError("label must be good or bad")
    await db.execute("UPDATE judgements SET human_label = %s, labeled_at = now() WHERE run_id = %s", (label, run_id))


async def judge_run(run_id: str) -> None:
    """Grade one run and store the result. Runs as a background task; any
    failure is logged and the run simply stays unjudged."""
    model = judge_model()
    if model is None:
        return
    try:
        runs = await db.fetch_all("SELECT agent, question, answer FROM agent_runs WHERE run_id = %s", (run_id,))
        if not runs or not runs[0]["question"]:
            return
        run = runs[0]
        agent = await governance.get_agent(run["agent"])
        steps = await db.fetch_all(
            "SELECT output FROM agent_steps WHERE run_id = %s AND kind = 'tool' AND output IS NOT NULL ORDER BY id",
            (run_id,))
        prompt = build_prompt(agent.purpose if agent else "(unknown)", run["question"],
                              [s["output"] for s in steps], run["answer"] or "")
        ask = _ask_claude if model.startswith("claude") else _ask_openai
        verdict = await ask(model, prompt)
        if verdict is None:
            return
        grades = parse_verdict(verdict)
        await db.execute(
            """
            INSERT INTO judgements (run_id, agent, judge_model, on_purpose, grounded, helpful, score, reasoning)
            VALUES (%(run_id)s, %(agent)s, %(model)s, %(on_purpose)s, %(grounded)s, %(helpful)s,
                    %(score)s, %(reasoning)s)
            ON CONFLICT (run_id) DO NOTHING
            """,
            {**grades, "run_id": run_id, "agent": run["agent"], "model": model},
        )
    except Exception:
        log.exception("could not judge run %s", run_id)
