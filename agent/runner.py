"""
runner.py — The agent loop, with AgentOps instrumentation and guardrails.

For every run it:
  - registers the run with the gateway and tags each LLM call with X-Run-Id
    (so the gateway attributes tokens and cost to the run);
  - opens an OpenTelemetry span for the run and one per tool call, and sends
    the trace context to the gateway (its LLM spans join the same trace);
  - stops the run on max steps, on a tool-call loop, or when the gateway
    refuses a call (budget, per-run cost limit, agent charter or guardrail).
"""

import json
import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass

import httpx
from opentelemetry import trace
from opentelemetry.propagate import inject

from agent import config
from agent.llm import GatewayRejected, ProviderChoice, ToolOutcome, new_conversation
from agent.tools import ToolError, run_tool

log = logging.getLogger("agent.runner")
tracer = trace.get_tracer("research-agent")


class LoopDetector:
    """Flags a run that keeps calling the same tool with the same arguments."""

    def __init__(self, limit: int = config.LOOP_REPEAT_LIMIT):
        self.limit = limit
        self.seen: Counter = Counter()

    def check(self, name: str, args: dict) -> bool:
        key = (name, json.dumps(args, sort_keys=True))
        self.seen[key] += 1
        return self.seen[key] >= self.limit


@dataclass
class RunResult:
    run_id: str
    status: str  # success | failed | killed | error
    stop_reason: str
    answer: str
    steps: int
    tool_calls: int
    tool_errors: int
    loop_detected: bool
    cost_usd: float = 0.0
    total_tokens: int = 0
    trace_id: str | None = None


class Reporter:
    """Sends run telemetry to the gateway's AgentOps API. Best effort: a
    reporting failure is logged, never fatal for the run."""

    def __init__(self, base_url: str = config.GATEWAY_URL):
        self.http = httpx.Client(base_url=base_url, timeout=10)

    def _post(self, path: str, payload: dict) -> dict:
        try:
            resp = self.http.post(path, json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            log.warning("could not report %s: %s", path, exc)
            return {}

    def providers(self) -> dict:
        return self.http.get("/version").raise_for_status().json().get("providers", {})

    def start(self, **run) -> None:
        self._post("/api/runs", run)

    def step(self, run_id: str, **step) -> None:
        self._post(f"/api/runs/{run_id}/steps", step)

    def finish(self, run_id: str, **result) -> dict:
        return self._post(f"/api/runs/{run_id}/finish", result)

    def eval_result(self, **result) -> None:
        self._post("/api/evals", result)


def run_task(question: str, choice: ProviderChoice, reporter: Reporter,
             task_id: str | None = None, conversation=None) -> RunResult:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    conversation = conversation or new_conversation(choice, question)
    loops = LoopDetector()
    steps = tool_calls = tool_errors = 0
    status, stop_reason, answer, loop_detected = "failed", "max_steps", "", False

    with tracer.start_as_current_span(f"invoke_agent {config.AGENT_NAME}", attributes={
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": config.AGENT_NAME,
        "gen_ai.request.model": choice.model,
        "llmops.run_id": run_id,
        "llmops.task_id": task_id or "",
    }) as span:
        trace_id = format(span.get_span_context().trace_id, "032x")
        reporter.start(run_id=run_id, agent=config.AGENT_NAME, project=config.AGENT_PROJECT,
                       task_id=task_id, model=choice.model, trace_id=trace_id, question=question)

        for _ in range(config.MAX_STEPS):
            headers = {"X-Run-Id": run_id}
            inject(headers)  # traceparent -> the gateway's span joins this trace
            steps += 1
            started = time.perf_counter()
            try:
                turn = conversation.step(headers)
            except GatewayRejected as exc:
                reporter.step(run_id, step=steps, kind="llm", name=choice.model, ok=False,
                              latency_ms=int((time.perf_counter() - started) * 1000), detail=str(exc)[:500])
                status, stop_reason = "killed", exc.kind
                break
            except Exception as exc:
                reporter.step(run_id, step=steps, kind="llm", name=choice.model, ok=False,
                              latency_ms=int((time.perf_counter() - started) * 1000), detail=str(exc)[:500])
                status, stop_reason = "error", type(exc).__name__
                span.record_exception(exc)
                break
            reporter.step(run_id, step=steps, kind="llm", name=turn.model or choice.model, ok=True,
                          latency_ms=int((time.perf_counter() - started) * 1000), detail=turn.stop_reason)

            if turn.stop_reason == "pause_turn":
                continue
            if turn.stop_reason != "tool_use" or not turn.tool_calls:
                answer = turn.text.strip()
                status = "success" if turn.stop_reason == "end_turn" and answer else "failed"
                stop_reason = turn.stop_reason or "no_answer"
                break

            outcomes = []
            for call in turn.tool_calls:
                tool_calls += 1
                if loops.check(call.name, call.args):
                    loop_detected = True
                with tracer.start_as_current_span(f"execute_tool {call.name}", attributes={
                    "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": call.name,
                }) as tool_span:
                    started = time.perf_counter()
                    try:
                        outcomes.append(ToolOutcome(call, run_tool(call.name, call.args)))
                        ok, detail = True, None
                    except ToolError as exc:
                        outcomes.append(ToolOutcome(call, str(exc), is_error=True))
                        tool_errors += 1
                        ok, detail = False, str(exc)
                        tool_span.set_status(trace.Status(trace.StatusCode.ERROR, detail))
                reporter.step(run_id, step=steps, kind="tool", name=call.name, ok=ok,
                              latency_ms=int((time.perf_counter() - started) * 1000),
                              detail=detail or json.dumps(call.args)[:500], output=outcomes[-1].output[:4000])
            if loop_detected:
                status, stop_reason = "killed", "loop_detected"
                break
            conversation.add_results(outcomes)

        span.set_attributes({"llmops.status": status, "llmops.stop_reason": stop_reason,
                             "llmops.steps": steps, "llmops.tool_calls": tool_calls})
        totals = reporter.finish(run_id, status=status, stop_reason=stop_reason, steps=steps,
                                 tool_calls=tool_calls, tool_errors=tool_errors, loop_detected=loop_detected,
                                 answer=answer)

    return RunResult(run_id, status, stop_reason, answer, steps, tool_calls, tool_errors, loop_detected,
                     float(totals.get("cost_usd", 0.0)), int(totals.get("total_tokens", 0)), trace_id)
