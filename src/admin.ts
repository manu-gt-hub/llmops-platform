/**
 * admin.ts — The admin dashboard (/admin), protected by ADMIN_TOKEN.
 *
 *   GET  /admin                  dashboard: spend, free allocation, calls and
 *                                errors, judge scores, guardrails, agents
 *                                (pause / guardrail mode), review queue, releases
 *   GET  /admin/runs/<id>        one run: question, steps, answer, judge, events
 *   POST /admin/agents/<name>    pause | resume | monitor | enforce
 *   POST /admin/runs/<id>/label  good | bad
 *   GET  /admin/api/summary      JSON health summary (used by the monitor workflow)
 *
 * Server-rendered HTML with inline SVG charts: no JavaScript, no CDN, so the
 * page works under a strict Content-Security-Policy. Requests, tokens, cost
 * and logs per call are also in the Cloudflare dashboard (AI Gateway).
 */

import type { Env } from "./env";
import { CHARTERS } from "./governance";
import { PASS_SCORE, setLabel } from "./judge";
import { getSwitches, setSwitches } from "./ledger";
import { FREE_NEURONS_PER_DAY } from "./pricing";

type Row = Record<string, unknown>;

const all = async (env: Env, sql: string, ...args: unknown[]) =>
  (await env.DB.prepare(sql).bind(...args).all<Row>()).results;
const first = async (env: Env, sql: string, ...args: unknown[]): Promise<Row> =>
  (await env.DB.prepare(sql).bind(...args).first<Row>()) ?? {};

export function esc(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}
const usd = (v: unknown, digits = 4) => `$${Number(v ?? 0).toFixed(digits)}`;
const pct = (v: unknown) => (v === null || v === undefined ? "—" : `${Math.round(Number(v) * 100)}%`);

/** Bar chart as inline SVG. */
export function bars(points: { label: string; value: number }[], format: (v: number) => string, color = "#3274d9"): string {
  if (!points.length) return '<p class="muted">No data yet.</p>';
  const max = Math.max(...points.map((p) => p.value), 1e-9);
  const w = 24;
  const h = 110;
  const body = points.map((p, i) => {
    const bh = Math.round((p.value / max) * h);
    return `<g><title>${esc(p.label)}: ${esc(format(p.value))}</title>` +
      `<rect x="${i * (w + 6)}" y="${h - bh}" width="${w}" height="${bh}" fill="${color}" rx="2"/>` +
      `<text x="${i * (w + 6) + w / 2}" y="${h + 14}" font-size="9" text-anchor="middle">${esc(p.label.slice(5))}</text></g>`;
  });
  return `<svg viewBox="0 0 ${points.length * (w + 6)} ${h + 18}" width="100%" height="140" role="img">${body.join("")}</svg>` +
    `<p class="small muted">max ${esc(format(max))}</p>`;
}

function page(title: string, body: string): Response {
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(title)}</title><style>
:root{--bg:#fff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--card:#f6f8fa;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8d96a0;--line:#30363d;--card:#161b22;--ok:#3fb950;--warn:#d29922;--bad:#f85149}}
body{margin:0;font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:1100px;margin:0 auto;padding:1rem}a{color:#4493f8}
h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:2rem}.muted{color:var(--muted)}.small{font-size:.8rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.75rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.8rem}
.stat{font-size:1.4rem;font-weight:600}.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.75rem;text-transform:uppercase;color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
button{font:inherit;padding:.15rem .6rem;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--fg);cursor:pointer}
form{display:inline}pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:.85rem}svg text{fill:var(--muted)}
</style></head><body><main>${body}</main></body></html>`, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

const statusClass = (s: unknown) =>
  ({ success: "ok", active: "ok", failed: "warn", paused: "warn", flagged: "warn", killed: "bad", error: "bad", blocked: "bad" })[String(s)] ?? "muted";
const pill = (s: unknown) => `<span class="${statusClass(s)}">${esc(s)}</span>`;
const score = (v: unknown) =>
  v === null || v === undefined ? '<span class="muted">—</span>' : `<span class="${Number(v) >= PASS_SCORE ? "ok" : "bad"}">${Number(v).toFixed(2)}</span>`;

/** Numbers the monitor workflow alerts on. */
export async function summary(env: Env) {
  const today = await first(env, `SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd),0) AS cost_usd,
    COALESCE(SUM(neurons),0) AS neurons, COALESCE(AVG(1 - ok), 0) AS error_rate FROM llm_calls WHERE ts >= date('now')`);
  const judge = await first(env, "SELECT AVG(score) AS judge_score, COUNT(*) AS judged FROM judgements WHERE ts >= datetime('now', '-1 day')");
  const agents = await Promise.all(Object.entries(CHARTERS).map(async ([name, c]) => {
    const spentToday = await first(env, "SELECT COALESCE(SUM(cost_usd),0) AS v FROM llm_calls WHERE agent = ? AND ts >= date('now')", name);
    return { name, ...(await getSwitches(env, name)), budget_used: Number(spentToday.v) / c.daily_budget_usd };
  }));
  return {
    version: env.APP_VERSION, commit: env.GIT_COMMIT,
    today: { ...today, free_neurons_used: Number(today.neurons) / FREE_NEURONS_PER_DAY } as Row,
    judge_24h: judge,
    agents,
  };
}

async function dashboard(env: Env): Promise<Response> {
  const s = await summary(env);
  const days = await all(env, `SELECT date(ts) AS day, SUM(cost_usd) AS cost, SUM(neurons) AS neurons, COUNT(*) AS calls,
    SUM(1 - ok) AS errors FROM llm_calls WHERE ts >= date('now', '-13 days') GROUP BY 1 ORDER BY 1`);
  const runs7 = await first(env, `SELECT COUNT(*) AS runs, AVG(status = 'success') AS success_rate,
    AVG(cost_usd) AS avg_cost FROM runs WHERE ts >= datetime('now', '-7 days') AND status <> 'running'`);
  const judge7 = await first(env, `SELECT AVG(score) AS avg, AVG(CASE WHEN human_label IS NOT NULL
    THEN ((score >= ${PASS_SCORE}) = (human_label = 'good')) END) AS agreement, COUNT(human_label) AS labeled
    FROM judgements WHERE ts >= datetime('now', '-7 days')`);
  const judgeDays = await all(env, `SELECT date(ts) AS day, AVG(score) AS v FROM judgements
    WHERE ts >= date('now', '-13 days') GROUP BY 1 ORDER BY 1`);
  const rules = await all(env, `SELECT rule, action, COUNT(*) AS n FROM guardrail_events
    WHERE ts >= datetime('now', '-7 days') GROUP BY 1, 2 ORDER BY 3 DESC`);
  const recent = await all(env, `SELECT r.run_id, r.ts, r.source, r.status, r.stop_reason, r.steps, r.cost_usd,
    substr(r.question, 1, 90) AS question, j.score FROM runs r LEFT JOIN judgements j USING (run_id) ORDER BY r.ts DESC LIMIT 20`);
  const review = await all(env, `SELECT r.run_id, substr(r.question, 1, 90) AS question, j.score, substr(j.reasoning, 1, 140) AS reasoning
    FROM judgements j JOIN runs r USING (run_id) WHERE j.human_label IS NULL AND j.score < ${PASS_SCORE}
    ORDER BY j.ts DESC LIMIT 20`);
  const releases = await all(env, "SELECT ts, version, commit_sha FROM deployments ORDER BY id DESC LIMIT 5");

  const agentRows = s.agents.map((a) => {
    const actions = [a.status === "active" ? "pause" : "resume", a.guardrail_mode === "enforce" ? "monitor" : "enforce"];
    return `<tr><td>${esc(a.name)}</td><td>${pill(a.status)}</td><td>${esc(a.guardrail_mode)}</td>
      <td>${pct(a.budget_used)} of ${usd(CHARTERS[a.name].daily_budget_usd, 2)}</td>
      <td>${actions.map((x) => `<form method="post" action="/admin/agents/${esc(a.name)}"><button name="action" value="${x}">${x}</button></form>`).join(" ")}</td>
      <td class="small muted">${esc(CHARTERS[a.name].purpose.slice(0, 120))}…</td></tr>`;
  });

  return page("LLMOps admin", `
<h1>LLMOps admin <span class="small muted">v${esc(s.version)} · ${esc(s.commit)}</span></h1>
<p class="small muted">Per-request logs, tokens and cost are also in the Cloudflare dashboard → AI → AI Gateway → ${esc(env.AI_GATEWAY_ID)};
Worker errors and latency in Workers → Observability.</p>

<h2>Today (UTC)</h2>
<div class="grid">
  <div class="card"><div class="small muted">LLM calls</div><div class="stat">${esc(s.today.calls)}</div></div>
  <div class="card"><div class="small muted">Cost (beyond free tier)</div><div class="stat">${usd(s.today.cost_usd)}</div></div>
  <div class="card"><div class="small muted">Free Workers AI allocation used</div><div class="stat">${pct(s.today.free_neurons_used)}</div>
    <div class="small muted">${Math.round(Number(s.today.neurons))} of ${FREE_NEURONS_PER_DAY} neurons</div></div>
  <div class="card"><div class="small muted">Error rate</div><div class="stat">${pct(s.today.error_rate)}</div></div>
</div>

<h2>Last 7 days</h2>
<div class="grid">
  <div class="card"><div class="small muted">Runs</div><div class="stat">${esc(runs7.runs)}</div></div>
  <div class="card"><div class="small muted">Success rate</div><div class="stat">${pct(runs7.success_rate)}</div></div>
  <div class="card"><div class="small muted">Avg cost per run</div><div class="stat">${usd(runs7.avg_cost, 5)}</div></div>
  <div class="card"><div class="small muted">Judge score</div><div class="stat">${score(judge7.avg)}</div></div>
  <div class="card"><div class="small muted">Judge / human agreement</div><div class="stat">${pct(judge7.agreement)}</div>
    <div class="small muted">${esc(judge7.labeled)} labeled</div></div>
</div>

<div class="grid">
  <div class="card"><h2>Cost per day</h2>${bars(days.map((d) => ({ label: String(d.day), value: Number(d.cost) })), (v) => usd(v))}</div>
  <div class="card"><h2>Calls per day</h2>${bars(days.map((d) => ({ label: String(d.day), value: Number(d.calls) })), String, "#56a64b")}</div>
  <div class="card"><h2>Errors per day</h2>${bars(days.map((d) => ({ label: String(d.day), value: Number(d.errors) })), String, "#cf222e")}</div>
  <div class="card"><h2>Judge score per day</h2>${bars(judgeDays.map((d) => ({ label: String(d.day), value: Number(d.v) })), (v) => v.toFixed(2), "#a352cc")}</div>
</div>

<h2>Agents</h2>
<div class="wrap"><table><tr><th>Agent</th><th>Status</th><th>Guardrails</th><th>Budget used today</th><th>Actions</th><th>Purpose</th></tr>
${agentRows.join("")}</table></div>

<h2>Guardrails (7 days)</h2>
<div class="wrap"><table><tr><th>Rule</th><th>Action</th><th>Count</th></tr>
${rules.map((r) => `<tr><td>${esc(r.rule)}</td><td>${pill(r.action)}</td><td>${esc(r.n)}</td></tr>`).join("") || '<tr><td colspan="3" class="muted">None.</td></tr>'}
</table></div>

<h2>Review queue <span class="small muted">judged below ${PASS_SCORE}, not labeled yet</span></h2>
<div class="wrap"><table><tr><th>Run</th><th>Question</th><th>Judge</th><th>Label</th></tr>
${review.map((r) => `<tr><td><a href="/admin/runs/${esc(r.run_id)}">${esc(r.run_id)}</a></td><td>${esc(r.question)}<div class="small muted">${esc(r.reasoning)}</div></td>
  <td>${score(r.score)}</td><td>${labelForm(String(r.run_id))}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">Nothing to review.</td></tr>'}
</table></div>

<h2>Recent runs</h2>
<div class="wrap"><table><tr><th>When</th><th>Run</th><th>Source</th><th>Question</th><th>Status</th><th>Steps</th><th>Cost</th><th>Judge</th></tr>
${recent.map((r) => `<tr><td class="small">${esc(r.ts)}</td><td><a href="/admin/runs/${esc(r.run_id)}">${esc(r.run_id)}</a></td><td>${esc(r.source)}</td>
  <td>${esc(r.question)}</td><td>${pill(r.status)} <span class="small muted">${esc(r.stop_reason)}</span></td><td>${esc(r.steps)}</td>
  <td>${usd(r.cost_usd, 5)}</td><td>${score(r.score)}</td></tr>`).join("") || '<tr><td colspan="8" class="muted">No runs yet.</td></tr>'}
</table></div>

<h2>Releases</h2>
<div class="wrap"><table><tr><th>Deployed (UTC)</th><th>Version</th><th>Commit</th></tr>
${releases.map((r) => `<tr><td>${esc(r.ts)}</td><td>${esc(r.version)}</td><td><code>${esc(r.commit_sha)}</code></td></tr>`).join("") || '<tr><td colspan="3" class="muted">No deployments recorded.</td></tr>'}
</table></div>`);
}

const labelForm = (runId: string) =>
  `<form method="post" action="/admin/runs/${esc(runId)}/label"><button name="label" value="good">👍</button>
   <button name="label" value="bad">👎</button></form>`;

async function runPage(env: Env, runId: string): Promise<Response> {
  const run = await first(env, "SELECT * FROM runs WHERE run_id = ?", runId);
  if (!run.run_id) return new Response("Run not found", { status: 404 });
  const steps = await all(env, "SELECT * FROM steps WHERE run_id = ? ORDER BY id", runId);
  const calls = await all(env, "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY id", runId);
  const events = await all(env, "SELECT * FROM guardrail_events WHERE run_id = ? ORDER BY id", runId);
  const j = await first(env, "SELECT * FROM judgements WHERE run_id = ?", runId);
  return page(`Run ${runId}`, `
<p class="small"><a href="/admin">← Admin</a></p>
<h1>Run ${esc(runId)} ${pill(run.status)} <span class="small muted">${esc(run.stop_reason)}</span></h1>
<p class="small muted">${esc(run.ts)} UTC · ${esc(run.source)} · ${esc(run.model)} · ${esc(run.steps)} steps · ${usd(run.cost_usd, 5)} · v${esc(run.version)}</p>
<div class="card"><p><strong>Question</strong></p><pre>${esc(run.question)}</pre><p><strong>Answer</strong></p><pre>${esc(run.answer ?? "(no answer)")}</pre></div>
<h2>Judge</h2>
${j.run_id ? `<div class="card">Score ${score(j.score)} · on purpose ${esc(j.on_purpose)}/5 · grounded ${esc(j.grounded)}/5 · helpful ${esc(j.helpful)}/5
  <p>${esc(j.reasoning)}</p><p class="small muted">${esc(j.judge_model)}${j.human_label ? ` · human label: <strong>${esc(j.human_label)}</strong>` : ""}</p>
  ${labelForm(runId)}</div>` : '<p class="muted">Not judged (not sampled, no answer, or the judge is still running).</p>'}
${events.length ? `<h2>Guardrail events</h2><div class="wrap"><table><tr><th>Stage</th><th>Rule</th><th>Action</th><th>Detail</th></tr>
  ${events.map((e) => `<tr><td>${esc(e.stage)}</td><td>${esc(e.rule)}</td><td>${pill(e.action)}</td><td>${esc(e.detail)}</td></tr>`).join("")}</table></div>` : ""}
<h2>Steps</h2><div class="wrap"><table><tr><th>#</th><th>Kind</th><th>Name</th><th>OK</th><th>ms</th><th>Detail / output</th></tr>
${steps.map((s) => `<tr><td>${esc(s.step)}</td><td>${esc(s.kind)}</td><td>${esc(s.name)}</td><td>${s.ok ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>'}</td>
  <td>${esc(s.latency_ms)}</td><td><pre>${esc(s.detail)}</pre>${s.output ? `<details><summary class="small">output</summary><pre>${esc(s.output)}</pre></details>` : ""}</td></tr>`).join("")}
</table></div>
<h2>LLM calls</h2><div class="wrap"><table><tr><th>Agent</th><th>Model</th><th>In</th><th>Out</th><th>Neurons</th><th>Cost</th><th>ms</th><th>AI Gateway log</th></tr>
${calls.map((c) => `<tr><td>${esc(c.agent)}</td><td>${esc(c.model)}</td><td>${esc(c.input_tokens)}</td><td>${esc(c.output_tokens)}</td>
  <td>${Number(c.neurons).toFixed(1)}</td><td>${usd(c.cost_usd, 6)}</td><td>${esc(c.latency_ms)}</td><td class="small"><code>${esc(c.gateway_log_id ?? "")}</code></td></tr>`).join("")}
</table></div>`);
}

export async function handleAdmin(req: Request, env: Env, path: string): Promise<Response> {
  if (req.method === "GET" && path === "/admin") return dashboard(env);
  if (req.method === "GET" && path === "/admin/api/summary") return Response.json(await summary(env));
  const run = /^\/admin\/runs\/([\w-]+)$/.exec(path);
  if (req.method === "GET" && run) return runPage(env, run[1]);

  if (req.method === "POST") {
    const form = await req.formData();
    const agent = /^\/admin\/agents\/([\w-]+)$/.exec(path);
    if (agent && CHARTERS[agent[1]]) {
      const sw = await getSwitches(env, agent[1]);
      const action = String(form.get("action"));
      if (action === "pause" || action === "resume") sw.status = action === "pause" ? "paused" : "active";
      else if (action === "monitor" || action === "enforce") sw.guardrail_mode = action;
      else return new Response("Unknown action", { status: 400 });
      await setSwitches(env, agent[1], sw);
      return Response.redirect(new URL("/admin", req.url).toString(), 303);
    }
    const label = /^\/admin\/runs\/([\w-]+)\/label$/.exec(path);
    const value = String(form.get("label"));
    if (label && (value === "good" || value === "bad")) {
      await setLabel(env, label[1], value);
      // Back to the page the label was given on; only the path, never another site
      const back = new URL(req.headers.get("Referer") ?? "/admin", req.url).pathname;
      return Response.redirect(new URL(back.startsWith("/admin") ? back : "/admin", req.url).toString(), 303);
    }
  }
  return new Response("Not found", { status: 404 });
}
