/**
 * admin.ts — The dashboards (English / Spanish, see i18n.ts).
 *
 *   GET  /dashboard              public, read-only: aggregates (usage, billing,
 *                                calls, errors, judge, guardrails), agents and
 *                                live evaluation by release. Nothing typed by users.
 *
 * Admin, protected by ADMIN_TOKEN:
 *
 *   GET  /admin                  the same, plus agent switches (pause / guardrail
 *                                mode), the review queue and recent runs
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
import { TASKS } from "./evals";
import { CHARTERS } from "./governance";
import { type Lang, langSwitch, translator } from "./i18n";
import { PASS_SCORE, setLabel } from "./judge";
import { evaluationRuns, getSwitches, setSwitches, summary } from "./ledger";
import { FREE_NEURONS_PER_DAY } from "./pricing";

const REPO_URL = "https://github.com/manu-gt-hub/llmops-platform";

type Row = Record<string, unknown>;
type T = ReturnType<typeof translator>;

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
export function bars(points: { label: string; value: number }[], format: (v: number) => string, t: T,
  color = "#3274d9"): string {
  if (!points.length) return `<p class="muted">${t("noData")}</p>`;
  const top = Math.max(...points.map((p) => p.value));
  const max = top || 1; // all zero: flat bars
  const w = 24;
  const h = 110;
  const body = points.map((p, i) => {
    const bh = Math.round((p.value / max) * h);
    return `<g><title>${esc(p.label)}: ${esc(format(p.value))}</title>` +
      `<rect x="${i * (w + 6)}" y="${h - bh}" width="${w}" height="${bh}" fill="${color}" rx="2"/>` +
      `<text x="${i * (w + 6) + w / 2}" y="${h + 14}" font-size="9" text-anchor="middle">${esc(p.label.slice(5))}</text></g>`;
  });
  return `<svg viewBox="0 0 ${points.length * (w + 6)} ${h + 18}" width="100%" height="140" role="img">${body.join("")}</svg>` +
    `<p class="small muted">${t("max")} ${esc(format(top))}</p>`;
}

function page(title: string, body: string, lang: Lang = "en"): Response {
  return new Response(`<!doctype html><html lang="${lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(title)}</title><style>
:root{--bg:#fff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--card:#f6f8fa;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8d96a0;--line:#30363d;--card:#161b22;--ok:#3fb950;--warn:#d29922;--bad:#f85149}}
body{margin:0;font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:1100px;margin:0 auto;padding:1rem}a{color:#4493f8}
h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:2rem}.muted{color:var(--muted)}.small{font-size:.8rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.75rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.8rem}
.stat{font-size:1.4rem;font-weight:600}.wrap{overflow-x:auto}.top{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}
.nowrap{white-space:nowrap}th{font-size:.75rem;text-transform:uppercase;color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
button{font:inherit;padding:.15rem .6rem;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--fg);cursor:pointer}
form{display:inline}pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:.85rem}svg text{fill:var(--muted)}
</style></head><body><main>${body}</main></body></html>`, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

const statusClass = (s: unknown) =>
  ({ success: "ok", active: "ok", failed: "warn", paused: "warn", flagged: "warn", killed: "bad", error: "bad", blocked: "bad" })[String(s)] ?? "muted";
const pill = (s: unknown) => `<span class="${statusClass(s)}">${esc(s)}</span>`;
const score = (v: unknown) =>
  v === null || v === undefined ? '<span class="muted">—</span>' : `<span class="${Number(v) >= PASS_SCORE ? "ok" : "bad"}">${Number(v).toFixed(2)}</span>`;

type Summary = Awaited<ReturnType<typeof summary>>;

/** Aggregates shared by /admin and the public /dashboard. No user text here:
 * only counts, money, scores, rule names and releases. */
async function metrics(env: Env, s: Summary, t: T): Promise<string> {
  const days = await all(env, `SELECT date(ts) AS day, SUM(cost_usd) AS cost, COUNT(*) AS calls,
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
  const releases = await all(env, "SELECT ts, version, commit_sha FROM deployments ORDER BY id DESC LIMIT 5");
  const billed = Number(s.today.billed_usd);

  return `
<h2>${t("today")}</h2>
<div class="grid">
  <div class="card"><div class="small muted">${t("calls")}</div><div class="stat">${esc(s.today.calls)}</div></div>
  <div class="card"><div class="small muted">${t("billed")}</div><div class="stat ${billed > 0 ? "warn" : "ok"}">${usd(billed, 2)}</div>
    <div class="small muted">${billed > 0 ? t("billedOver") : t("billedNote")}</div></div>
  <div class="card"><div class="small muted">${t("value")}</div><div class="stat">${usd(s.today.value_usd)}</div>
    <div class="small muted">${t("valueNote")}</div></div>
  <div class="card"><div class="small muted">${t("freeUsed")}</div><div class="stat">${pct(s.today.free_neurons_used)}</div>
    <div class="small muted">${Math.round(Number(s.today.neurons))} ${t("neuronsOf")} ${FREE_NEURONS_PER_DAY} ${t("neurons")}</div></div>
  <div class="card"><div class="small muted">${t("errorRate")}</div><div class="stat">${pct(s.today.error_rate)}</div></div>
</div>

<h2>${t("last7")}</h2>
<div class="grid">
  <div class="card"><div class="small muted">${t("runs")}</div><div class="stat">${esc(runs7.runs)}</div></div>
  <div class="card"><div class="small muted">${t("successRate")}</div><div class="stat">${pct(runs7.success_rate)}</div></div>
  <div class="card"><div class="small muted">${t("avgCost")}</div><div class="stat">${usd(runs7.avg_cost, 5)}</div></div>
  <div class="card"><div class="small muted">${t("judge")}</div><div class="stat">${score(judge7.avg)}</div></div>
  <div class="card"><div class="small muted">${t("agreement")}</div><div class="stat">${pct(judge7.agreement)}</div>
    <div class="small muted">${esc(judge7.labeled)} ${t("labeled")}</div></div>
</div>

<div class="grid">
  <div class="card"><h2>${t("valuePerDay")}</h2>${bars(days.map((d) => ({ label: String(d.day), value: Number(d.cost) })), (v) => usd(v), t)}</div>
  <div class="card"><h2>${t("callsPerDay")}</h2>${bars(days.map((d) => ({ label: String(d.day), value: Number(d.calls) })), String, t, "#56a64b")}</div>
  <div class="card"><h2>${t("errorsPerDay")}</h2>${bars(days.map((d) => ({ label: String(d.day), value: Number(d.errors) })), String, t, "#cf222e")}</div>
  <div class="card"><h2>${t("judgePerDay")}</h2>${bars(judgeDays.map((d) => ({ label: String(d.day), value: Number(d.v) })), (v) => v.toFixed(2), t, "#a352cc")}</div>
</div>

<h2>${t("guardrails")}</h2>
<div class="wrap"><table><tr><th>${t("rule")}</th><th>${t("action")}</th><th>${t("count")}</th></tr>
${rules.map((r) => `<tr><td>${esc(r.rule)}</td><td>${pill(r.action)}</td><td>${esc(r.n)}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">${t("none")}</td></tr>`}
</table></div>

<h2>${t("releases")}</h2>
<div class="wrap"><table><tr><th>${t("deployed")}</th><th>${t("version")}</th><th>${t("commit")}</th></tr>
${releases.map((r) => `<tr><td>${esc(r.ts)}</td><td>${esc(r.version)}</td><td><code>${esc(r.commit_sha)}</code></td></tr>`).join("") || `<tr><td colspan="3" class="muted">${t("noReleases")}</td></tr>`}
</table></div>`;
}

/** Agents table; with `actions` the pause / guardrail-mode buttons (admin only). */
function agentsTable(s: Summary, actions: boolean, t: T): string {
  const rows = s.agents.map((a) => {
    const buttons = [a.status === "active" ? "pause" : "resume", a.guardrail_mode === "enforce" ? "monitor" : "enforce"]
      .map((x) => `<form method="post" action="/admin/agents/${esc(a.name)}"><button name="action" value="${x}">${x}</button></form>`);
    return `<tr><td class="nowrap">${esc(a.name)}</td><td>${pill(a.status)}</td><td>${esc(a.guardrail_mode)}</td>
      <td>${pct(a.budget_used)} ${t("of")} ${usd(CHARTERS[a.name].daily_budget_usd, 2)}</td>
      ${actions ? `<td>${buttons.join(" ")}</td>` : ""}<td class="small muted">${esc(CHARTERS[a.name].purpose)}</td></tr>`;
  });
  return `<h2>${t("agents")}</h2><div class="wrap"><table><tr><th>${t("agent")}</th><th>${t("status")}</th>
    <th>${t("guardrailMode")}</th><th>${t("budgetToday")}</th>${actions ? `<th>${t("actions")}</th>` : ""}<th>${t("purpose")}</th></tr>
    ${rows.join("")}</table></div>`;
}

/** Live evaluation results, graded here against evaluation/tasks.json. Eval
 * runs are only accepted for the suite's exact questions (see index.ts), and
 * the questions shown come from the task file, never from a request. */
async function evaluationSection(env: Env, t: T): Promise<string> {
  const graded = await evaluationRuns(env);
  const byVersion = new Map<string, { n: number; passed: number; cost: number }>();
  for (const r of graded) {
    const v = byVersion.get(String(r.version)) ?? { n: 0, passed: 0, cost: 0 };
    byVersion.set(String(r.version), { n: v.n + 1, passed: v.passed + Number(r.passed), cost: v.cost + Number(r.cost_usd) });
  }
  const versions = [...byVersion].slice(0, 5).map(([version, v]) =>
    `<tr><td>${esc(version)}</td><td>${esc(v.n)}</td><td>${pct(v.passed / v.n)}</td><td>${usd(v.cost / v.n, 5)}</td></tr>`);
  const latest = graded.slice(0, 8).map((r) =>
    `<tr><td class="small">${esc(r.ts)}</td><td class="nowrap">${esc(r.task_id)}</td><td>${esc(TASKS[String(r.task_id)].question)}</td>
     <td class="nowrap">${r.passed ? `<span class="ok">${t("pass")}</span>` : `<span class="bad">${t("fail")}</span>`}</td>
     <td>${pill(r.status)} <span class="small muted">${esc(r.stop_reason)}</span></td><td>${usd(r.cost_usd, 5)}</td><td>${score(r.score)}</td></tr>`);
  return `<h2>${t("evalByRelease")} <span class="small muted">${t("evalNote")}</span></h2>
<div class="wrap"><table><tr><th>${t("version")}</th><th>${t("evalRuns")}</th><th>${t("passRate")}</th><th>${t("avgPerTask")}</th></tr>
${versions.join("") || `<tr><td colspan="4" class="muted">${t("noEval")}</td></tr>`}</table></div>
<h2>${t("latestEval")}</h2>
<div class="wrap"><table><tr><th>${t("when")}</th><th>${t("task")}</th><th>${t("question")}</th><th>${t("result")}</th>
<th>${t("status")}</th><th>${t("cost")}</th><th>${t("judge")}</th></tr>
${latest.join("") || `<tr><td colspan="7" class="muted">${t("noEval")}</td></tr>`}</table></div>`;
}

/** Public, read-only: aggregates, agents, evaluation. Nothing a user typed. */
export async function publicDashboard(env: Env, lang: Lang): Promise<Response> {
  const t = translator(lang);
  const s = await summary(env);
  return page(t("dashboardTitle"), `
<div class="top"><h1>${t("dashboardTitle")} <span class="small muted">v${esc(s.version)} · ${esc(s.commit)}</span></h1>
  ${langSwitch("/dashboard", lang)}</div>
<p>${t("dashboardIntro")}</p>
<p class="small"><a href="/">${t("askAgent")}</a> · <a href="${REPO_URL}">${t("source")}</a> ·
  <a href="${REPO_URL}/blob/main/doc/architecture.md">${t("architecture")}</a></p>
${await metrics(env, s, t)}
${agentsTable(s, false, t)}
${await evaluationSection(env, t)}
<p class="small muted">${t("readOnly")}</p>`, lang);
}

async function dashboard(env: Env, lang: Lang): Promise<Response> {
  const t = translator(lang);
  const s = await summary(env);
  const recent = await all(env, `SELECT r.run_id, r.ts, r.source, r.status, r.stop_reason, r.steps, r.cost_usd,
    substr(r.question, 1, 90) AS question, j.score FROM runs r LEFT JOIN judgements j USING (run_id) ORDER BY r.ts DESC LIMIT 20`);
  const review = await all(env, `SELECT r.run_id, substr(r.question, 1, 90) AS question, j.score, substr(j.reasoning, 1, 140) AS reasoning
    FROM judgements j JOIN runs r USING (run_id) WHERE j.human_label IS NULL AND j.score < ${PASS_SCORE}
    ORDER BY j.ts DESC LIMIT 20`);

  return page(t("adminTitle"), `
<div class="top"><h1>${t("adminTitle")} <span class="small muted">v${esc(s.version)} · ${esc(s.commit)}</span></h1>
  ${langSwitch("/admin", lang)}</div>
<p class="small muted">${t("publicView")}: <a href="/dashboard">/dashboard</a>. ${t("adminNote")} ${esc(env.AI_GATEWAY_ID)}; ${t("adminNote2")}</p>
${await metrics(env, s, t)}
${agentsTable(s, true, t)}

<h2>${t("review")} <span class="small muted">${t("reviewNote")} ${PASS_SCORE}, ${t("reviewNote2")}</span></h2>
<div class="wrap"><table><tr><th>${t("run")}</th><th>${t("question")}</th><th>${t("judge")}</th><th>${t("label")}</th></tr>
${review.map((r) => `<tr><td><a href="/admin/runs/${esc(r.run_id)}">${esc(r.run_id)}</a></td><td>${esc(r.question)}<div class="small muted">${esc(r.reasoning)}</div></td>
  <td>${score(r.score)}</td><td>${labelForm(String(r.run_id))}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">${t("nothingToReview")}</td></tr>`}
</table></div>

<h2>${t("recent")}</h2>
<div class="wrap"><table><tr><th>${t("when")}</th><th>${t("run")}</th><th>${t("sourceCol")}</th><th>${t("question")}</th><th>${t("status")}</th>
<th>${t("steps")}</th><th>${t("cost")}</th><th>${t("judge")}</th></tr>
${recent.map((r) => `<tr><td class="small">${esc(r.ts)}</td><td><a href="/admin/runs/${esc(r.run_id)}">${esc(r.run_id)}</a></td><td>${esc(r.source)}</td>
  <td>${esc(r.question)}</td><td>${pill(r.status)} <span class="small muted">${esc(r.stop_reason)}</span></td><td>${esc(r.steps)}</td>
  <td>${usd(r.cost_usd, 5)}</td><td>${score(r.score)}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">${t("noRuns")}</td></tr>`}
</table></div>
${await evaluationSection(env, t)}`, lang);
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

export async function handleAdmin(req: Request, env: Env, path: string, lang: Lang): Promise<Response> {
  if (req.method === "GET" && path === "/admin") return dashboard(env, lang);
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
