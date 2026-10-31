"""
seed.py — Synthetic demo data so the dashboards tell a story out of the box.

Generates N days of traffic for three projects, with a release history that
explains what the charts show:

  v0.1.0  gateway + cost tracking
  v0.2.0  agent runs API and guardrails
  v0.2.1  agent tool-retry change -> loops, killed runs, cost spike
  v0.2.2  hotfix for the retry loop
  v0.3.0  prompt caching for the agent  -> cache reads up, cost per run down
  v0.4.0  support-bot routes simple questions to Claude Haiku 5.5

It also fills the governance tables: judge grades (some labeled by a human)
and guardrail events, so the console has something to show.

Every row is flagged synthetic=true: Grafana can hide it, and it never counts
towards the budgets enforced by the gateway. Deterministic (fixed seed).

Run manually with:  python -m gateway.seed [--days 30] [--force]
"""

import argparse
import asyncio
import json
import logging
import random
from datetime import UTC, datetime, time, timedelta

from gateway import config, db
from gateway.judge import PASS_SCORE, parse_verdict
from gateway.pricing import PriceList
from gateway.usage import Usage

log = logging.getLogger("gateway.seed")

RELEASES = [  # (days before now, version, notes)
    (30, "0.1.0", "LLM gateway with token and cost tracking"),
    (23, "0.2.0", "Agent runs API, budgets and per-run cost guardrail"),
    (16, "0.2.1", "Agent retries failed tool calls"),
    (14, "0.2.2", "Hotfix: cap tool retries (loop introduced in 0.2.1)"),
    (8, "0.3.0", "Prompt caching for the agent's system prompt and tools"),
    (3, "0.4.0", "support-bot routes simple questions to Claude Haiku 5.5"),
]
TOOLS = ["search_docs", "read_doc", "calculator"]
TASKS = {t["id"]: t for t in json.loads((config.PROJECT_ROOT / "agent" / "tasks.json").read_text(encoding="utf-8"))}
EVAL_TASKS = sorted(TASKS)
JUDGE_SAMPLE = 0.4   # share of demo runs with a judge grade
HUMAN_LABELED = 0.3  # share of graded runs a human labeled

REQUEST_COLUMNS = (
    "ts", "provider", "model", "project", "agent", "run_id", "endpoint", "streamed",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "cost_usd", "cache_savings_usd", "priced", "latency_ms", "status_code", "error",
    "release", "synthetic",
)
RUN_COLUMNS = (
    "run_id", "agent", "project", "task_id", "model", "started_at", "finished_at", "status",
    "stop_reason", "steps", "tool_calls", "tool_errors", "loop_detected", "score", "cost_usd",
    "total_tokens", "release", "synthetic", "question", "answer",
)
STEP_COLUMNS = ("run_id", "step", "ts", "kind", "name", "ok", "latency_ms", "detail", "synthetic")
EVAL_COLUMNS = ("ts", "suite_id", "task_id", "run_id", "model", "passed", "score", "cost_usd", "release", "synthetic")
JUDGEMENT_COLUMNS = ("ts", "run_id", "agent", "judge_model", "on_purpose", "grounded", "helpful", "score",
                     "reasoning", "human_label", "labeled_at", "synthetic")
EVENT_COLUMNS = ("ts", "agent", "project", "run_id", "stage", "rule", "action", "detail", "synthetic")


class Generator:
    def __init__(self, days: int, now: datetime, prices: PriceList, rng: random.Random):
        self.days = days
        self.now = now
        self.prices = prices
        self.rng = rng
        self.releases = [(now - timedelta(days=d, hours=-10), v, n) for d, v, n in RELEASES if d <= days]
        self.requests: list[tuple] = []
        self.runs: list[tuple] = []
        self.steps: list[tuple] = []
        self.evals: list[tuple] = []
        self.judgements: list[tuple] = []
        self.events: list[tuple] = []

    def release_at(self, ts: datetime) -> str:
        current = self.releases[0][1] if self.releases else "0.1.0"
        for when, version, _ in self.releases:
            if ts >= when:
                current = version
        return current

    def day_volume(self, day: datetime, base: int) -> int:
        weekday = 1.0 if day.weekday() < 5 else 0.45
        growth = 1 + 0.6 * (1 - (self.now - day).days / max(self.days, 1))
        return max(1, int(base * weekday * growth * self.rng.uniform(0.85, 1.15)))

    def timestamp(self, day: datetime) -> datetime | None:
        # Office-hours shaped traffic; nothing in the future.
        hour = min(23, max(0, int(self.rng.gauss(13, 3.5))))
        ts = datetime.combine(day.date(), time(hour, self.rng.randrange(60), self.rng.randrange(60)), UTC)
        return ts if ts <= self.now else None

    def add_request(self, ts, provider, model, project, usage: Usage, agent=None, run_id=None,
                    endpoint="v1/messages", latency_ms=None, status=200):
        if status != 200:
            usage = Usage()
        cost = self.prices.cost(model, usage)
        latency = latency_ms or int(400 + usage.output_tokens * self.rng.uniform(8, 18))
        self.requests.append((
            ts, provider, model, project, agent, run_id, endpoint, False,
            usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens,
            cost.usd, cost.cache_savings_usd, True, latency, status,
            None if status == 200 else "upstream overloaded", self.release_at(ts), True,
        ))
        return cost.usd, usage.total_tokens

    # -- research-agent: tool-using agent with runs, steps and evals --------
    def agent_run(self, ts: datetime, run_id: str, task_id: str, release: str) -> tuple[float, bool]:
        rng = self.rng
        buggy = release == "0.2.1"
        cached = release >= "0.3.0"
        loop = buggy and rng.random() < 0.35
        n_llm = rng.randint(9, 12) if loop else rng.randint(2, 5)
        success = not loop and rng.random() < (0.88 if release >= "0.2.2" else 0.8)
        cost = tokens = 0.0
        step = tool_calls = tool_errors = 0
        t = ts
        prefix = 2600  # system prompt + tool definitions
        for i in range(n_llm):
            step += 1
            history = 300 + i * rng.randint(250, 600)
            if cached:
                usage = Usage(
                    input_tokens=history,
                    output_tokens=rng.randint(150, 650),
                    cache_read_tokens=prefix if i else 0,
                    cache_write_tokens=0 if i else prefix,
                )
            else:
                usage = Usage(input_tokens=prefix + history, output_tokens=rng.randint(150, 650))
            c, tk = self.add_request(t, "anthropic", "claude-opus-5-5", "research-agent", usage,
                                     agent="research-agent", run_id=run_id)
            cost, tokens = cost + c, tokens + tk
            latency = int(900 + usage.output_tokens * rng.uniform(10, 20))
            self.steps.append((run_id, step, t, "llm", "claude-opus-5-5", True, latency, None, True))
            t += timedelta(milliseconds=latency)
            if i == n_llm - 1:
                break
            step += 1
            tool = "search_docs" if loop else rng.choice(TOOLS)
            ok = not loop and rng.random() > 0.06
            tool_calls += 1
            tool_errors += not ok
            tool_latency = rng.randint(5, 120)
            self.steps.append((run_id, step, t, "tool", tool, ok, tool_latency,
                               "same call repeated" if loop else None, True))
            t += timedelta(milliseconds=tool_latency)
        status = "killed" if loop else ("success" if success else "failed")
        stop = "loop_detected" if loop else ("end_turn" if success else "wrong_answer")
        score = 0.0 if loop else (round(rng.uniform(0.8, 1.0), 3) if success else round(rng.uniform(0.0, 0.5), 3))
        task = TASKS[task_id]
        expected = task.get("expected_number", " ".join(task.get("expected_keywords", [])))
        answer = None if loop else (f"According to the handbook, the answer is {expected}." if success
                                    else "I could not find a definitive answer in the handbook.")
        self.runs.append((
            run_id, "research-agent", "research-agent", task_id, "claude-opus-5-5", ts, t, status, stop,
            step, tool_calls, tool_errors, loop, score, round(cost, 8), int(tokens), release, True,
            task["question"], answer,
        ))
        if answer and rng.random() < JUDGE_SAMPLE:
            self.judge(t, run_id, success)
        return cost, success

    def judge(self, ts: datetime, run_id: str, success: bool) -> None:
        rng = self.rng
        good = (4, 5) if success else (1, 3)
        grades = parse_verdict({"on_purpose": 5, "grounded": rng.randint(*good), "helpful": rng.randint(*good),
                                "reasoning": "Answer matches the handbook." if success
                                else "The answer does not state the figure the handbook supports."})
        label = labeled_at = None
        if rng.random() < HUMAN_LABELED:
            agrees = rng.random() < 0.88  # the demo judge agrees with humans ~88% of the time
            label = "good" if (grades["score"] >= PASS_SCORE) == agrees else "bad"
            labeled_at = ts + timedelta(hours=rng.randint(1, 20))
        self.judgements.append((ts + timedelta(seconds=5), run_id, "research-agent", "claude-sonnet-5-5",
                                grades["on_purpose"], grades["grounded"], grades["helpful"], grades["score"],
                                grades["reasoning"], label, labeled_at, True))

    def generate(self) -> None:
        rng = self.rng
        for offset in range(self.days, -1, -1):
            day = self.now - timedelta(days=offset)

            for i in range(self.day_volume(day, 16)):
                ts = self.timestamp(day)
                if ts:
                    self.agent_run(ts, f"demo-{day:%Y%m%d}-{i:03d}", rng.choice(EVAL_TASKS), self.release_at(ts))

            # Nightly eval suite (02:00 UTC) on the release of the day.
            ts = datetime.combine(day.date(), time(2, 0), UTC)
            if ts <= self.now:
                release = self.release_at(ts)
                suite = f"nightly-{day:%Y%m%d}"
                for task in EVAL_TASKS:
                    run_id = f"eval-{day:%Y%m%d}-{task}"
                    cost, passed = self.agent_run(ts, run_id, task, release)
                    score = 1.0 if passed else 0.0
                    self.evals.append((ts, suite, task, run_id, "claude-opus-5-5", passed, score,
                                       round(cost, 8), release, True))
                    ts += timedelta(seconds=40)

            # Guardrail events on the research agent's inputs.
            for rule, chance, detail in (("prompt_injection", 0.5, "ignore previous instructions"),
                                         ("pii_email", 0.35, "…com"), ("model_not_allowed", 0.1, "gpt-4o")):
                ts = self.timestamp(day)
                if ts and rng.random() < chance:
                    stage = "charter" if rule == "model_not_allowed" else "input"
                    action = "flagged" if self.release_at(ts) < "0.2.0" and stage == "input" else "blocked"
                    self.events.append((ts, "research-agent", "research-agent", None, stage, rule, action,
                                        detail, True))

            # support-bot: single-turn chat; Haiku routing from 0.4.0.
            for _ in range(self.day_volume(day, 110)):
                ts = self.timestamp(day)
                if not ts:
                    continue
                model = "claude-sonnet-5-5"
                if self.release_at(ts) >= "0.4.0" and rng.random() < 0.7:
                    model = "claude-haiku-5-5"
                status = 529 if rng.random() < 0.008 else 200
                self.add_request(ts, "anthropic", model, "support-bot",
                                 Usage(rng.randint(800, 2400), rng.randint(120, 500)), status=status)

            # rag-system: OpenAI chat + query embeddings.
            for _ in range(self.day_volume(day, 70)):
                ts = self.timestamp(day)
                if not ts:
                    continue
                prompt = rng.randint(1500, 3200)
                cached = rng.randint(0, 1024) if rng.random() < 0.4 else 0
                self.add_request(ts, "openai", "gpt-4o-mini", "rag-system",
                                 Usage(prompt - cached, rng.randint(150, 450), cache_read_tokens=cached),
                                 endpoint="v1/chat/completions")
                self.add_request(ts, "openai", "text-embedding-3-small", "rag-system",
                                 Usage(rng.randint(8, 40), 0), endpoint="v1/embeddings",
                                 latency_ms=rng.randint(80, 250))


async def _copy(conn, table: str, columns: tuple, rows: list[tuple]) -> None:
    async with conn.cursor().copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in rows:
            await copy.write_row(row)


async def seed(days: int) -> dict:
    gen = Generator(days, datetime.now(UTC), PriceList.load(config.PRICING_FILE), random.Random(42))
    gen.generate()
    async with db.pool.connection() as conn:
        for when, version, notes in gen.releases:
            await conn.execute(
                "INSERT INTO deployments (ts, service, version, git_commit, build_date, environment, notes, synthetic) "
                "VALUES (%s, %s, %s, %s, %s, 'demo', %s, true)",
                (when, config.SERVICE_NAME, version, f"demo{version.replace('.', '')}", when.date().isoformat(), notes),
            )
        await _copy(conn, "llm_requests", REQUEST_COLUMNS, gen.requests)
        await _copy(conn, "agent_runs", RUN_COLUMNS, gen.runs)
        await _copy(conn, "agent_steps", STEP_COLUMNS, gen.steps)
        await _copy(conn, "eval_results", EVAL_COLUMNS, gen.evals)
        await _copy(conn, "judgements", JUDGEMENT_COLUMNS, gen.judgements)
        await _copy(conn, "guardrail_events", EVENT_COLUMNS, gen.events)
    counts = {"requests": len(gen.requests), "runs": len(gen.runs), "evals": len(gen.evals),
              "judgements": len(gen.judgements), "guardrail_events": len(gen.events)}
    log.info("seeded %s days of demo data: %s", days, counts)
    return counts


async def clear() -> None:
    async with db.pool.connection() as conn:
        for table in ("judgements", "guardrail_events", "agent_steps", "eval_results", "agent_runs",
                      "llm_requests", "deployments"):
            await conn.execute(f"DELETE FROM {table} WHERE synthetic")


async def seed_if_empty(days: int) -> None:
    rows = await db.fetch_all("SELECT 1 FROM llm_requests WHERE synthetic LIMIT 1")
    if not rows:
        await seed(days)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Load synthetic demo data")
    parser.add_argument("--days", type=int, default=config.SEED_DAYS)
    parser.add_argument("--force", action="store_true", help="replace existing demo data")
    parser.add_argument("--clear", action="store_true", help="only delete the demo data")
    args = parser.parse_args()
    await db.open_pool()
    await db.apply_schema()
    if args.clear or args.force:
        await clear()
    if not args.clear:
        await seed(args.days)
    await db.close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
