"""
console.py — Web console to govern the agents (http://localhost:8090/console).

Server-rendered pages (Jinja templates in gateway/templates/), plain HTML
forms, no JavaScript. Grafana stays the place for aggregated metrics; the
console is where you operate:

  /console                  agents: purpose, health, pause / resume, guardrail mode
  /console/agents/<name>    one agent: charter, recent runs, guardrail events
  /console/runs/<id>        one run: question, steps, answer, judge grades, label
  /console/review           runs the judge or a guardrail flagged, to label
  /console/chat             ask an agent a question and inspect the run

No authentication: this is a local tool. In production it would sit behind
the company's SSO, and switches would record the real user.
"""

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from gateway import db, governance, judge

router = APIRouter(prefix="/console", include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))
templates.env.globals["PASS_SCORE"] = judge.PASS_SCORE


def _page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


def _back(request: Request, fallback: str) -> RedirectResponse:
    return RedirectResponse(request.headers.get("referer") or fallback, status_code=303)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
AGENT_STATS = """
SELECT a.name, a.purpose, a.owner, a.models, a.tools, a.status, a.guardrail_mode,
       a.judge_sample_rate, a.updated_at, a.updated_by,
       r.runs, r.success_rate, r.cost_usd, j.judge_score, g.guardrail_hits
FROM agents a
LEFT JOIN (SELECT agent, COUNT(*) AS runs, AVG((status = 'success')::int) AS success_rate,
                  SUM(cost_usd) AS cost_usd
           FROM agent_runs WHERE started_at >= now() - interval '7 days' AND status <> 'running'
           GROUP BY agent) r ON r.agent = a.name
LEFT JOIN (SELECT agent, AVG(score) AS judge_score FROM judgements
           WHERE ts >= now() - interval '7 days' GROUP BY agent) j ON j.agent = a.name
LEFT JOIN (SELECT agent, COUNT(*) AS guardrail_hits FROM guardrail_events
           WHERE ts >= now() - interval '7 days' GROUP BY agent) g ON g.agent = a.name
"""


@router.get("", response_class=HTMLResponse)
async def agents(request: Request):
    rows = await db.fetch_all(AGENT_STATS + " ORDER BY a.name")
    return _page(request, "agents.html", agents=rows)


@router.get("/agents/{name}", response_class=HTMLResponse)
async def agent_detail(request: Request, name: str):
    rows = await db.fetch_all(AGENT_STATS + " WHERE a.name = %s", (name,))
    if not rows:
        raise HTTPException(404, "agent not found")
    runs = await db.fetch_all(
        """SELECT r.run_id, r.started_at, r.task_id, r.status, r.stop_reason, r.steps, r.cost_usd,
                  r.synthetic, j.score AS judge_score
           FROM agent_runs r LEFT JOIN judgements j USING (run_id)
           WHERE r.agent = %s ORDER BY r.started_at DESC LIMIT 30""", (name,))
    events = await db.fetch_all(
        "SELECT ts, run_id, stage, rule, action, detail, synthetic FROM guardrail_events "
        "WHERE agent = %s ORDER BY ts DESC LIMIT 30", (name,))
    return _page(request, "agent.html", agent=rows[0], runs=runs, events=events)


@router.post("/agents/{name}/status")
async def set_status(request: Request, name: str, value: str = Form(...)):
    await governance.set_switch(name, "status", value, by="console")
    return _back(request, f"/console/agents/{name}")


@router.post("/agents/{name}/guardrail-mode")
async def set_mode(request: Request, name: str, value: str = Form(...)):
    await governance.set_switch(name, "guardrail_mode", value, by="console")
    return _back(request, f"/console/agents/{name}")


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(request: Request, run_id: str):
    runs = await db.fetch_all("SELECT * FROM agent_runs WHERE run_id = %s", (run_id,))
    if not runs:
        raise HTTPException(404, "run not found")
    steps = await db.fetch_all("SELECT * FROM agent_steps WHERE run_id = %s ORDER BY id", (run_id,))
    judgement = await db.fetch_all("SELECT * FROM judgements WHERE run_id = %s", (run_id,))
    events = await db.fetch_all("SELECT * FROM guardrail_events WHERE run_id = %s ORDER BY ts", (run_id,))
    return _page(request, "run.html", run=runs[0], steps=steps, events=events,
                 judgement=judgement[0] if judgement else None)


@router.post("/runs/{run_id}/label")
async def label(request: Request, run_id: str, value: str = Form(...)):
    try:
        await judge.set_label(run_id, value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _back(request, f"/console/runs/{run_id}")


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------
@router.get("/review", response_class=HTMLResponse)
async def review(request: Request):
    queue = await db.fetch_all(
        """SELECT r.run_id, r.agent, r.started_at, r.question, r.status, j.score, j.reasoning, r.synthetic,
                  (SELECT COUNT(*) FROM guardrail_events g WHERE g.run_id = r.run_id) AS guardrail_hits
           FROM judgements j JOIN agent_runs r USING (run_id)
           WHERE j.human_label IS NULL
             AND (j.score < %s OR EXISTS (SELECT 1 FROM guardrail_events g WHERE g.run_id = r.run_id))
           ORDER BY r.started_at DESC LIMIT 50""", (judge.PASS_SCORE,))
    # Agreement: the judge says pass (score >= PASS_SCORE) and the human says good, or both say no.
    agreement = await db.fetch_all(
        """SELECT COUNT(*) AS labeled,
                  AVG(((score >= %s) = (human_label = 'good'))::int) AS agreement
           FROM judgements WHERE human_label IS NOT NULL""", (judge.PASS_SCORE,))
    return _page(request, "review.html", queue=queue, stats=agreement[0])


# ---------------------------------------------------------------------------
# Chat: run an agent from the browser
# ---------------------------------------------------------------------------
@router.get("/chat", response_class=HTMLResponse)
async def chat(request: Request):
    return _page(request, "chat.html", error=None, question="")


@router.post("/chat", response_class=HTMLResponse)
async def chat_ask(request: Request, question: str = Form(...)):
    # The demo agent runs inside the gateway process and calls the gateway
    # over HTTP like any other client, so every rule applies to it.
    from agent.llm import choose_provider
    from agent.runner import Reporter, run_task

    def ask():
        reporter = Reporter()
        return run_task(question, choose_provider(reporter.providers()), reporter)

    try:
        result = await run_in_threadpool(ask)
    except Exception as exc:  # e.g. no provider key configured
        return _page(request, "chat.html", error=str(exc), question=question)
    return RedirectResponse(f"/console/runs/{result.run_id}", status_code=303)
