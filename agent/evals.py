"""
evals.py — Deterministic grading of the agent's answers.

Each task in tasks.json has an `expected_number` (any number in the answer
within 0.01 passes) and/or `expected_keywords` / `forbidden_keywords`.
No LLM judge: grading is free, reproducible and safe to gate CI on.
"""

import re

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    values = []
    for match in _NUMBER.findall(text):
        try:
            values.append(float(match.replace(",", "")))
        except ValueError:
            continue
    return values


def grade(task: dict, answer: str) -> tuple[bool, float]:
    """Return (passed, score in [0, 1])."""
    checks = []
    lowered = answer.lower()
    if "expected_number" in task:
        expected = float(task["expected_number"])
        checks.append(any(abs(n - expected) < 0.01 for n in numbers_in(answer)))
    for keyword in task.get("expected_keywords", []):
        checks.append(keyword.lower() in lowered)
    for keyword in task.get("forbidden_keywords", []):
        checks.append(keyword.lower() not in lowered)
    if not checks or not answer:
        return False, 0.0
    score = sum(checks) / len(checks)
    return score == 1.0, round(score, 4)


def gate(summary: dict, baseline: dict) -> list[str]:
    """Compare an eval summary with the committed baseline; return failures."""
    failures = []
    min_pass = baseline.get("min_pass_rate", 0.0)
    if summary["pass_rate"] < min_pass:
        failures.append(f"pass rate {summary['pass_rate']:.0%} is below the baseline minimum {min_pass:.0%}")
    max_cost = baseline.get("avg_cost_usd", 0.0) * (1 + baseline.get("cost_tolerance", 0.25))
    if max_cost and summary["avg_cost_usd"] > max_cost:
        failures.append(
            f"average cost per task ${summary['avg_cost_usd']:.4f} exceeds the baseline "
            f"${baseline['avg_cost_usd']:.4f} + {baseline.get('cost_tolerance', 0.25):.0%} tolerance"
        )
    return failures
