"""
runs.py — AgentOps API: agents report their runs and steps here.

The LLM calls of a run are already recorded by the proxy (tagged with
X-Run-Id); this API adds what the gateway cannot see on its own: tool calls,
the outcome of the run and eval scores. When a run finishes, it may be sent
to the judge (judge.py). It also exposes the governance switches that the
console uses, so they can be automated too.
"""

import random
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from gateway import config, db, governance, judge, metrics

router = APIRouter(prefix="/api", tags=["agentops"])


class RunStart(BaseModel):
    run_id: str
    agent: str
    project: str
    task_id: str | None = None
    model: str | None = None
    trace_id: str | None = None
    question: str | None = None


class Step(BaseModel):
    step: int
    kind: str  # llm | tool
    name: str
    ok: bool = True
    latency_ms: int = 0
    detail: str | None = None
    output: str | None = None  # tool output (truncated), what the judge checks grounding against


class RunFinish(BaseModel):
    status: str  # success | failed | killed | error
    stop_reason: str | None = None
    steps: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    loop_detected: bool = False
    score: float | None = None
    answer: str | None = None


class EvalResult(BaseModel):
    suite_id: str
    task_id: str
    run_id: str | None = None
    model: str | None = None
    passed: bool
    score: float
    cost_usd: float = 0.0


@router.post("/runs", status_code=201)
async def start_run(run: RunStart) -> dict:
    await db.execute(
        "INSERT INTO agent_runs (run_id, agent, project, task_id, model, trace_id, question, release) "
        "VALUES (%(run_id)s, %(agent)s, %(project)s, %(task_id)s, %(model)s, %(trace_id)s, %(question)s, "
        "%(release)s) "
        "ON CONFLICT (run_id) DO NOTHING",
        {**run.model_dump(), "release": config.APP_VERSION},
    )
    return {"run_id": run.run_id}


@router.post("/runs/{run_id}/steps", status_code=201)
async def add_step(run_id: str, step: Step) -> dict:
    await db.execute(
        "INSERT INTO agent_steps (run_id, step, kind, name, ok, latency_ms, detail, output) "
        "VALUES (%(run_id)s, %(step)s, %(kind)s, %(name)s, %(ok)s, %(latency_ms)s, %(detail)s, %(output)s)",
        {**step.model_dump(), "run_id": run_id, "output": (step.output or None) and step.output[:4000]},
    )
    return {"ok": True}


@router.post("/runs/{run_id}/finish")
async def finish_run(run_id: str, result: RunFinish, background: BackgroundTasks) -> dict:
    # Cost and tokens come from what the proxy recorded for this run.
    await db.execute(
        """
        UPDATE agent_runs r SET
            finished_at = now(), status = %(status)s, stop_reason = %(stop_reason)s,
            steps = %(steps)s, tool_calls = %(tool_calls)s, tool_errors = %(tool_errors)s,
            loop_detected = %(loop_detected)s, score = %(score)s, answer = %(answer)s,
            cost_usd = t.cost, total_tokens = t.tokens
        FROM (
            SELECT COALESCE(SUM(cost_usd), 0) AS cost,
                   COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens), 0) AS tokens
            FROM llm_requests WHERE run_id = %(run_id)s
        ) t
        WHERE r.run_id = %(run_id)s
        """,
        {**result.model_dump(), "run_id": run_id},
    )
    rows = await db.fetch_all("SELECT agent, cost_usd, total_tokens FROM agent_runs WHERE run_id = %s", (run_id,))
    if rows:
        metrics.AGENT_RUNS.labels(rows[0]["agent"], result.status).inc()
        agent = await governance.get_agent(rows[0]["agent"])
        if result.answer and agent and random.random() < agent.judge_sample_rate:
            background.add_task(judge.judge_run, run_id)  # after the response is sent
        return {"run_id": run_id, "cost_usd": float(rows[0]["cost_usd"]), "total_tokens": rows[0]["total_tokens"]}
    return {"run_id": run_id, "cost_usd": 0.0, "total_tokens": 0}


@router.post("/evals", status_code=201)
async def add_eval(result: EvalResult) -> dict:
    await db.execute(
        "INSERT INTO eval_results (suite_id, task_id, run_id, model, passed, score, cost_usd, release) "
        "VALUES (%(suite_id)s, %(task_id)s, %(run_id)s, %(model)s, %(passed)s, %(score)s, %(cost_usd)s, %(release)s)",
        {**result.model_dump(), "release": config.APP_VERSION},
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Governance switches and human review (used by the console)
# ---------------------------------------------------------------------------
class Switch(BaseModel):
    value: str
    by: str = "api"


class Label(BaseModel):
    label: Literal["good", "bad"]


@router.get("/agents", tags=["governance"])
async def list_agents() -> list[dict]:
    return await db.fetch_all("SELECT * FROM agents ORDER BY name")


@router.post("/agents/{name}/status", tags=["governance"])
async def set_status(name: str, switch: Switch) -> dict:
    return await _set(name, "status", switch)


@router.post("/agents/{name}/guardrail-mode", tags=["governance"])
async def set_guardrail_mode(name: str, switch: Switch) -> dict:
    return await _set(name, "guardrail_mode", switch)


async def _set(name: str, column: str, switch: Switch) -> dict:
    if await governance.get_agent(name) is None:
        raise HTTPException(404, f"agent '{name}' is not registered")
    try:
        await governance.set_switch(name, column, switch.value, switch.by)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"agent": name, column: switch.value}


@router.post("/runs/{run_id}/label", tags=["governance"])
async def label_run(run_id: str, label: Label) -> dict:
    """A human's verdict on a judged run; measures judge/human agreement."""
    await judge.set_label(run_id, label.label)
    return {"run_id": run_id, "label": label.label}
