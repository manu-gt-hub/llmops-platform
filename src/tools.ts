/**
 * tools.ts — The research agent's tools over the bundled handbook.
 *
 *   search_docs  keyword search, returns the best matching sections
 *   read_doc     full text of one document
 *   calculator   arithmetic with a tiny parser (no eval)
 */

import { CORPUS } from "./corpus";

export interface ToolSpec {
  name: string;
  description: string;
  parameters: { type: "object"; properties: Record<string, { type: string; description?: string }>; required: string[] };
}

export const TOOL_SPECS: ToolSpec[] = [
  {
    name: "search_docs",
    description: "Search the Acme Cloud handbook (plans, pricing, SLA, on-call, expenses). Returns the best matching sections.",
    parameters: { type: "object", properties: { query: { type: "string", description: "Keywords" } }, required: ["query"] },
  },
  {
    name: "read_doc",
    description: "Read a whole handbook document: plans, sla, oncall or expenses.",
    parameters: { type: "object", properties: { name: { type: "string" } }, required: ["name"] },
  },
  {
    name: "calculator",
    description: "Evaluate an arithmetic expression with + - * / ( ) and decimals, e.g. '29 * 12 * 0.85'.",
    parameters: { type: "object", properties: { expression: { type: "string" } }, required: ["expression"] },
  },
];

export class ToolError extends Error {}

const words = (text: string) => new Set(text.toLowerCase().match(/[a-z0-9]{3,}/g) ?? []);

export function searchDocs(query: string): string {
  const terms = words(query);
  if (!terms.size) throw new ToolError("query has no searchable words");
  const sections = Object.entries(CORPUS).flatMap(([doc, text]) => text.split(/\n(?=## )/).map((s) => ({ doc, s })));
  const scored = sections
    .map(({ doc, s }) => ({ doc, s, score: [...words(s)].filter((w) => terms.has(w)).length }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 3);
  return scored.length ? scored.map((x) => `[${x.doc}]\n${x.s.trim()}`).join("\n\n---\n\n") : "No matching sections.";
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

export function runTool(name: string, args: Record<string, unknown>): string {
  if (name === "search_docs") return searchDocs(String(args.query ?? ""));
  if (name === "read_doc") return readDoc(String(args.name ?? ""));
  if (name === "calculator") return calculator(String(args.expression ?? ""));
  throw new ToolError(`unknown tool '${name}'`);
}
