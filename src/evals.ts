/**
 * evals.ts — Deterministic grading of the agent's answers (evaluation/tasks.json).
 *
 * Each task has an `expected_number` (any number in the answer within 0.01
 * passes) and/or `expected_keywords` / `forbidden_keywords`. No LLM judge:
 * grading is free and reproducible, so CI can gate a deploy on it.
 */

export interface Task {
  id: string;
  question: string;
  expected_number?: number;
  expected_keywords?: string[];
  forbidden_keywords?: string[];
}

export function numbersIn(text: string): number[] {
  return (text.match(/-?\d[\d,]*(?:\.\d+)?/g) ?? []).map((n) => Number(n.replace(/,/g, ""))).filter(Number.isFinite);
}

export function grade(task: Task, answer: string | null): { passed: boolean; score: number } {
  if (!answer) return { passed: false, score: 0 };
  const lowered = answer.toLowerCase();
  const checks: boolean[] = [];
  if (task.expected_number !== undefined) {
    checks.push(numbersIn(answer).some((n) => Math.abs(n - task.expected_number!) < 0.01));
  }
  for (const k of task.expected_keywords ?? []) checks.push(lowered.includes(k.toLowerCase()));
  for (const k of task.forbidden_keywords ?? []) checks.push(!lowered.includes(k.toLowerCase()));
  if (!checks.length) return { passed: false, score: 0 };
  const score = checks.filter(Boolean).length / checks.length;
  return { passed: score === 1, score: Math.round(score * 10000) / 10000 };
}

export interface Baseline {
  min_pass_rate: number;
  max_avg_cost_usd: number;
}

/** Compare an eval run with the committed baseline; returns the failures. */
export function gate(summary: { passRate: number; avgCostUsd: number }, baseline: Baseline): string[] {
  const failures: string[] = [];
  if (summary.passRate < baseline.min_pass_rate) {
    failures.push(`pass rate ${(summary.passRate * 100).toFixed(0)}% is below ${(baseline.min_pass_rate * 100).toFixed(0)}%`);
  }
  if (summary.avgCostUsd > baseline.max_avg_cost_usd) {
    failures.push(`average cost per task $${summary.avgCostUsd.toFixed(5)} is above $${baseline.max_avg_cost_usd}`);
  }
  return failures;
}
