/**
 * tools.ts — The platform copilot's tools.
 *
 *   search_docs       keyword search over the platform's documentation (doc/,
 *                     bundled as src/corpus.ts), returns the best sections
 *   read_doc          full text of one document
 *   get_live_metrics  today's usage, billing, errors, budgets, agent switches,
 *                     judge score and the latest evaluation, from the ledger
 *   calculator        arithmetic with a tiny parser (no eval)
 */

import { CORPUS } from "./corpus";
import type { Env } from "./env";
import { evaluationRuns, summary } from "./ledger";

export interface ToolSpec {
  name: string;
  description: string;
  parameters: { type: "object"; properties: Record<string, { type: string; description?: string }>; required: string[] };
}

export const TOOL_SPECS: ToolSpec[] = [
  {
    name: "search_docs",
    description: "Search the LLMOps platform documentation (architecture, decisions/ADRs, runbook): FinOps, budgets, " +
      "scoring, guardrails, DevOps, security, configuration. Returns the best matching sections.",
    parameters: { type: "object", properties: { query: { type: "string", description: "Keywords" } }, required: ["query"] },
  },
  {
    name: "read_doc",
    description: `Read a whole document: ${Object.keys(CORPUS).join(", ")}.`,
    parameters: { type: "object", properties: { name: { type: "string" } }, required: ["name"] },
  },
  {
    name: "get_live_metrics",
    description: "Live numbers of the running platform: today's LLM calls, usage at list price, amount billed, free " +
      "Workers AI allocation used, error rate, each agent's status and budget used, judge score (24 h) and the latest " +
      "evaluation pass rate.",
    parameters: { type: "object", properties: {}, required: [] },
  },
  {
    name: "calculator",
    description: "Evaluate an arithmetic expression with + - * / ( ) and decimals, e.g. '5000 * 0.011 / 1000'.",
    parameters: { type: "object", properties: { expression: { type: "string" } }, required: ["expression"] },
  },
];

export class ToolError extends Error {}

// Terms: words of 3+ letters cut to their first 5 (a crude stemmer: pause,
// pauses, paused -> "pause"); identifiers like `agent_paused` stay whole
const words = (text: string) =>
  new Set((text.toLowerCase().match(/[a-z0-9_]{3,}/g) ?? []).map((w) => (w.includes("_") ? w : w.slice(0, 5))));

// The docs split into "## " sections, each with its set of words
const SECTIONS = Object.entries(CORPUS).flatMap(([doc, text]) =>
  text.split(/\n(?=## )/).map((s) => ({ doc, s, words: words(s) })));

// Inverse document frequency: a word that appears in few sections ("rollback",
// "agent_paused") says much more about a section than one in all of them ("agent")
const IDF = new Map<string, number>();
for (const { words: ws } of SECTIONS) for (const w of ws) IDF.set(w, (IDF.get(w) ?? 0) + 1);
for (const [w, df] of IDF) IDF.set(w, Math.log(SECTIONS.length / df));

export function searchDocs(query: string): string {
  const terms = words(query);
  if (!terms.size) throw new ToolError("query has no searchable words");
  const scored = SECTIONS
    .map((x) => ({ ...x, score: [...terms].reduce((sum, t) => sum + (x.words.has(t) ? IDF.get(t) ?? 0 : 0), 0) }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 3);
  // Sections are capped: enough to answer, cheap in tokens
  return scored.length ? scored.map((x) => `[${x.doc}]\n${x.s.trim().slice(0, 1200)}`).join("\n\n---\n\n") : "No matching sections.";
}

export function readDoc(name: string): string {
  const doc = CORPUS[String(name).replace(/\.md$/, "")];
  if (!doc) throw new ToolError(`unknown document '${name}'. Available: ${Object.keys(CORPUS).join(", ")}`);
  return doc;
}

/** Recursive-descent parser for + - * / and parentheses. */
export function calculator(expression: string): string {
  const src = String(expression).replace(/,/g, "");
  let pos = 0;
  const peek = (): string | undefined => {
    while (src[pos] === " ") pos++;
    return src[pos];
  };
  const number = (): number => {
    const m = /^\s*-?\d+(\.\d+)?/.exec(src.slice(pos));
    if (!m) throw new ToolError(`invalid expression at '${src.slice(pos)}'`);
    pos += m[0].length;
    return Number(m[0]);
  };
  const factor = (): number => {
    if (peek() === "(") {
      pos++;
      const value = expr();
      if (peek() !== ")") throw new ToolError("missing ')'");
      pos++;
      return value;
    }
    return number();
  };
  const term = (): number => {
    let value = factor();
    for (let op = peek(); op === "*" || op === "/"; op = peek()) {
      pos++;
      const rhs = factor();
      if (op === "/" && rhs === 0) throw new ToolError("division by zero");
      value = op === "*" ? value * rhs : value / rhs;
    }
    return value;
  };
  const expr = (): number => {
    let value = term();
    for (let op = peek(); op === "+" || op === "-"; op = peek()) {
      pos++;
      value = op === "+" ? value + term() : value - term();
    }
    return value;
  };
  const result = expr();
  if (peek() !== undefined) throw new ToolError(`unexpected '${src.slice(pos)}'`);
  return String(Math.round(result * 1e6) / 1e6);
}

/** The live numbers, rounded and labelled so the model can quote them. */
export async function liveMetrics(env: Env): Promise<string> {
  const s = await summary(env);
  const evals = await evaluationRuns(env);
  const latest = evals.filter((r) => r.version === evals[0]?.version);
  const round = (v: unknown, d = 4) => Math.round(Number(v ?? 0) * 10 ** d) / 10 ** d;
  return JSON.stringify({
    version: s.version,
    today_utc: {
      llm_calls: s.today.calls,
      usage_at_list_price_usd: round(s.today.value_usd),
      billed_usd: round(s.today.billed_usd),
      neurons_used: Math.round(Number(s.today.neurons)),
      free_neurons_per_day: 10000,
      free_allocation_used_percent: round(Number(s.today.free_neurons_used) * 100, 1),
      error_rate_percent: round(Number(s.today.error_rate) * 100, 1),
    },
    agents: s.agents.map((a) => ({ name: a.name, status: a.status, guardrail_mode: a.guardrail_mode,
      daily_budget_used_percent: round(a.budget_used * 100, 1) })),
    judge_last_24h: { average_score_0_to_1: s.judge_24h.judge_score === null ? null : round(s.judge_24h.judge_score, 2),
      judged_runs: s.judge_24h.judged },
    latest_evaluation: latest.length
      ? { version: latest[0].version, runs: latest.length, pass_rate_percent: round((latest.filter((r) => r.passed).length / latest.length) * 100, 1) }
      : null,
  });
}

export async function runTool(env: Env, name: string, args: Record<string, unknown>): Promise<string> {
  if (name === "search_docs") return searchDocs(String(args.query ?? ""));
  if (name === "read_doc") return readDoc(String(args.name ?? ""));
  if (name === "get_live_metrics") return liveMetrics(env);
  if (name === "calculator") return calculator(String(args.expression ?? ""));
  throw new ToolError(`unknown tool '${name}'`);
}
