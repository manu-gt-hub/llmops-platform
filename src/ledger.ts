/**
 * ledger.ts — Reads and writes of the D1 tables (migrations/0001_init.sql).
 */

import type { Env } from "./env";
import { CHARTERS, type Finding, type Switches } from "./governance";

export async function getSwitches(env: Env, agent: string): Promise<Switches> {
  const row = await env.DB.prepare("SELECT status, guardrail_mode FROM agent_switches WHERE agent = ?")
    .bind(agent)
    .first<Switches>();
  return row ?? { status: "active", guardrail_mode: CHARTERS[agent]?.guardrail_mode ?? "monitor" };
}

export async function setSwitches(env: Env, agent: string, sw: Switches): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO agent_switches (agent, status, guardrail_mode) VALUES (?, ?, ?)
     ON CONFLICT (agent) DO UPDATE SET status = excluded.status, guardrail_mode = excluded.guardrail_mode,
       updated_at = datetime('now')`,
  )
    .bind(agent, sw.status, sw.guardrail_mode)
    .run();
}

/** Spend of today (UTC) for an agent, and of one run. */
export async function spent(env: Env, agent: string, runId: string): Promise<{ today: number; run: number }> {
  const row = await env.DB.prepare(
    `SELECT COALESCE(SUM(CASE WHEN ts >= date('now') THEN cost_usd END), 0) AS today,
            COALESCE(SUM(CASE WHEN run_id = ? THEN cost_usd END), 0) AS run
     FROM llm_calls WHERE agent = ? AND (ts >= date('now') OR run_id = ?)`,
  )
    .bind(runId, agent, runId)
    .first<{ today: number; run: number }>();
  return row ?? { today: 0, run: 0 };
}

export interface CallRow {
  agent: string;
  run_id: string;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  neurons: number;
  cost_usd: number;
  latency_ms: number;
  ok: number;
  error: string | null;
  gateway_log_id: string | null;
}

export async function recordCall(env: Env, c: CallRow): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO llm_calls (agent, run_id, provider, model, input_tokens, output_tokens, neurons, cost_usd,
       latency_ms, ok, error, gateway_log_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(c.agent, c.run_id, c.provider, c.model, c.input_tokens, c.output_tokens, c.neurons, c.cost_usd,
      c.latency_ms, c.ok, c.error, c.gateway_log_id)
    .run();
}

export async function recordEvents(env: Env, agent: string, runId: string | null, findings: Finding[], action: string) {
  if (!findings.length) return;
  await env.DB.batch(
    findings.map((f) =>
      env.DB.prepare("INSERT INTO guardrail_events (agent, run_id, stage, rule, action, detail) VALUES (?, ?, ?, ?, ?, ?)")
        .bind(agent, runId, f.stage, f.rule, action, f.detail.slice(0, 300)),
    ),
  );
}

export async function startRun(env: Env, r: { runId: string; agent: string; source: string; taskId: string | null;
  model: string; question: string }) {
  await env.DB.prepare(
    "INSERT INTO runs (run_id, agent, source, task_id, model, question, version) VALUES (?, ?, ?, ?, ?, ?, ?)",
  )
    .bind(r.runId, r.agent, r.source, r.taskId, r.model, r.question, env.APP_VERSION)
    .run();
}

export async function addStep(env: Env, s: { runId: string; step: number; kind: string; name: string; ok: boolean;
  latencyMs: number; detail?: string | null; output?: string | null }) {
  await env.DB.prepare(
    "INSERT INTO steps (run_id, step, kind, name, ok, latency_ms, detail, output) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
  )
    .bind(s.runId, s.step, s.kind, s.name, s.ok ? 1 : 0, s.latencyMs, s.detail?.slice(0, 500) ?? null,
      s.output?.slice(0, 4000) ?? null)
    .run();
}

export async function finishRun(env: Env, r: { runId: string; status: string; stopReason: string; answer: string | null;
  steps: number; toolCalls: number }): Promise<number> {
  await env.DB.prepare(
    `UPDATE runs SET status = ?, stop_reason = ?, answer = ?, steps = ?, tool_calls = ?,
       cost_usd = (SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE run_id = ?)
     WHERE run_id = ?`,
  )
    .bind(r.status, r.stopReason, r.answer, r.steps, r.toolCalls, r.runId, r.runId)
    .run();
  const row = await env.DB.prepare("SELECT cost_usd FROM runs WHERE run_id = ?").bind(r.runId).first<{ cost_usd: number }>();
  return row?.cost_usd ?? 0;
}
