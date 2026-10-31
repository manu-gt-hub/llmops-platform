"""
run_tasks.py — Run the agent over the task suite: traffic generator and eval.

  python -m agent.run_tasks                  # whole suite once, graded
  python -m agent.run_tasks --task task-01   # a single task
  python -m agent.run_tasks --ask "Which plan has SSO?"
  python -m agent.run_tasks --loop 300       # keep generating traffic every 5 min
  python -m agent.run_tasks --gate           # fail (exit 1) below evals/baseline.json

Results go to the gateway (dashboards) and results/eval_summary.json.
"""

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime

from agent import config
from agent.evals import gate, grade
from agent.llm import choose_provider
from agent.runner import Reporter, run_task
from gateway.telemetry import setup_tracing


def run_suite(tasks: list[dict], reporter: Reporter, suite_id: str) -> dict:
    choice = choose_provider(reporter.providers())
    print(f"Provider: {choice.provider} ({choice.model}) via {config.GATEWAY_URL}\n")
    rows = []
    for task in tasks:
        result = run_task(task["question"], choice, reporter, task_id=task["id"])
        passed, score = grade(task, result.answer)
        reporter.eval_result(suite_id=suite_id, task_id=task["id"], run_id=result.run_id,
                             model=choice.model, passed=passed, score=score, cost_usd=result.cost_usd)
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {task['id']}  {result.status:<7}  steps={result.steps}  "
              f"tools={result.tool_calls}  ${result.cost_usd:.4f}  {result.total_tokens} tok")
        if not passed:
            print(f"        answer: {result.answer[:200] or '(none)'}  [{result.stop_reason}]")
        rows.append({"task_id": task["id"], "run_id": result.run_id, "passed": passed, "score": score,
                     "status": result.status, "stop_reason": result.stop_reason, "steps": result.steps,
                     "cost_usd": result.cost_usd, "total_tokens": result.total_tokens,
                     "trace_id": result.trace_id})
    n = len(rows) or 1
    return {
        "suite_id": suite_id,
        "provider": choice.provider,
        "model": choice.model,
        "tasks": len(rows),
        "pass_rate": round(sum(r["passed"] for r in rows) / n, 4),
        "avg_cost_usd": round(sum(r["cost_usd"] for r in rows) / n, 6),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "avg_steps": round(sum(r["steps"] for r in rows) / n, 2),
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the research agent over the task suite")
    parser.add_argument("--task", action="append", help="task id to run (repeatable)")
    parser.add_argument("--ask", help="ask a free-form question instead of the suite")
    parser.add_argument("--loop", type=int, metavar="SECONDS", help="repeat the suite every N seconds")
    parser.add_argument("--gate", action="store_true", help="exit 1 if the results fall below the baseline")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    setup_tracing(config.AGENT_NAME, "agent", config.OTEL_EXPORTER_OTLP_ENDPOINT)
    reporter = Reporter()

    if args.ask:
        choice = choose_provider(reporter.providers())
        result = run_task(args.ask, choice, reporter)
        print(result.answer or f"(no answer: {result.status}, {result.stop_reason})")
        print(f"\nrun {result.run_id}: {result.steps} steps, ${result.cost_usd:.4f}, trace {result.trace_id}")
        return 0

    tasks = json.loads(config.TASKS_FILE.read_text(encoding="utf-8"))
    if args.task:
        tasks = [t for t in tasks if t["id"] in set(args.task)]

    while True:
        suite_id = f"manual-{datetime.now(UTC):%Y%m%d-%H%M%S}"
        summary = run_suite(tasks, reporter, suite_id)
        print(f"\nPass rate {summary['pass_rate']:.0%} | avg ${summary['avg_cost_usd']:.4f}/task | "
              f"total ${summary['total_cost_usd']:.4f} | avg {summary['avg_steps']} steps")
        try:
            config.RESULTS_DIR.mkdir(exist_ok=True)
            (config.RESULTS_DIR / "eval_summary.json").write_text(json.dumps(summary, indent=2))
        except OSError as exc:
            print(f"(could not write {config.RESULTS_DIR / 'eval_summary.json'}: {exc})")
        if not args.loop:
            break
        time.sleep(args.loop)

    if args.gate:
        baseline = json.loads(config.BASELINE_FILE.read_text(encoding="utf-8"))
        baseline = baseline.get(summary["model"], baseline.get("default", {}))
        failures = gate(summary, baseline)
        for failure in failures:
            print(f"GATE FAILED: {failure}")
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
