/**
 * evaluate.ts — Runs the task suite against a deployed Worker and gates on it.
 *
 *   npm run evaluate -- --url https://llmops-platform.<subdomain>.workers.dev [--strict]
 *
 * Each task goes to /api/ask (source "eval", so the runs are tagged in the
 * dashboard), is graded deterministically (src/evals.ts) and compared with
 * evaluation/baseline.json. Writes evaluation/results.md (and the GitHub job
 * summary). With --strict the exit code is 1 when the gate fails. When the
 * free daily AI allocation is used up it warns and exits 0.
 */

import { appendFileSync, readFileSync, writeFileSync } from "node:fs";

import { type Baseline, gate, grade, type Task } from "../src/evals";

const args = process.argv.slice(2);
const url = args[args.indexOf("--url") + 1]?.replace(/\/$/, "");
const strict = args.includes("--strict");
if (!url || args.indexOf("--url") < 0) {
  console.error("usage: npm run evaluate -- --url <worker url> [--strict]");
  process.exit(2);
}

const tasks: Task[] = JSON.parse(readFileSync(new URL("./tasks.json", import.meta.url), "utf-8"));
const baseline: Baseline = JSON.parse(readFileSync(new URL("./baseline.json", import.meta.url), "utf-8"));
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type Answer = { status?: string; stop_reason?: string; answer?: string | null; cost_usd?: number; steps?: number;
  run_id?: string; error?: { code: string } };

const rows: string[] = [];
let passed = 0;
let cost = 0;
for (const [i, task] of tasks.entries()) {
  if (i) await sleep(7000); // stay under the 10 questions/minute rate limit
  const resp = await fetch(`${url}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: task.question, task_id: task.id, source: "eval" }),
  });
  const body = (await resp.json()) as Answer;
  if (body.stop_reason === "quota_exceeded" || body.error?.code === "quota_exceeded") {
    console.warn("::warning title=Evaluation skipped::The free daily Workers AI allocation is used up.");
    process.exit(0);
  }
  const result = grade(task, body.answer ?? null);
  passed += Number(result.passed);
  cost += body.cost_usd ?? 0;
  const mark = result.passed ? "✅" : "❌";
  console.log(`${mark} ${task.id} ${body.status ?? resp.status} steps=${body.steps ?? "-"} $${(body.cost_usd ?? 0).toFixed(5)}`);
  if (!result.passed) console.log(`   answer: ${(body.answer ?? `(none: ${body.stop_reason ?? body.error?.code})`).slice(0, 200)}`);
  rows.push(`| ${task.id} | ${mark} | ${body.status ?? resp.status} | ${body.stop_reason ?? ""} | ${body.steps ?? ""} | $${(body.cost_usd ?? 0).toFixed(5)} | ${body.run_id ?? ""} |`);
}

const summary = { passRate: passed / tasks.length, avgCostUsd: cost / tasks.length };
const failures = gate(summary, baseline);
const report = [
  `### Live evaluation — ${url}`,
  "",
  `**Pass rate ${(summary.passRate * 100).toFixed(0)}%** (min ${(baseline.min_pass_rate * 100).toFixed(0)}%) · ` +
    `avg $${summary.avgCostUsd.toFixed(5)}/task (max $${baseline.max_avg_cost_usd})`,
  "",
  "| Task | Result | Status | Stop reason | Steps | Cost | Run |",
  "|---|---|---|---|---|---|---|",
  ...rows,
  "",
  failures.length ? failures.map((f) => `- ❌ ${f}`).join("\n") : "Gate passed.",
].join("\n");
writeFileSync(new URL("./results.md", import.meta.url), report);
if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${report}\n`);
console.log(`\n${failures.length ? failures.join("\n") : "Gate passed."}`);
process.exit(strict && failures.length ? 1 : 0);
