/**
 * fakes.ts — A Worker environment without a Cloudflare account:
 *   DB   D1 on Node's built-in SQLite, with the real migrations applied
 *   AI   Workers AI that plays back a script of model turns, and records the
 *        AI Gateway calls (metadata, patchLog)
 */

import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";

import type { Env } from "../src/env";

type Script = (input: { messages: { role: string; content: string }[]; tools?: unknown[] }) => unknown;

export interface Fake {
  env: Env;
  sql: (query: string, ...args: unknown[]) => Record<string, unknown>[];
  gatewayCalls: { metadata?: Record<string, unknown> }[];
  patched: { logId: string; data: unknown }[];
}

const MIGRATIONS = new URL("../migrations/", import.meta.url);

export function fakeEnv(script: Script, overrides: Partial<Env> = {}): Fake {
  const { DatabaseSync } = createRequire(import.meta.url)("node:sqlite");
  const db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).sort()) db.exec(readFileSync(new URL(file, MIGRATIONS), "utf-8"));

  const statement = (query: string, args: unknown[] = []) => ({
    bind: (...a: unknown[]) => statement(query, a.map((v) => (v === undefined ? null : v))),
    all: async () => ({ results: db.prepare(query).all(...args) }),
    first: async () => db.prepare(query).get(...args) ?? null,
    run: async () => db.prepare(query).run(...args),
  });

  const gatewayCalls: Fake["gatewayCalls"] = [];
  const patched: Fake["patched"] = [];
  let logCounter = 0;
  const ai = {
    aiGatewayLogId: null as string | null,
    run: async (_model: string, input: Parameters<Script>[0], options?: { gateway?: { metadata?: Record<string, unknown> } }) => {
      gatewayCalls.push({ metadata: options?.gateway?.metadata });
      ai.aiGatewayLogId = `log-${++logCounter}`;
      return script(input);
    },
    gateway: () => ({
      patchLog: async (logId: string, data: unknown) => void patched.push({ logId, data }),
      getUrl: async (provider: string) => `https://gateway.test/${provider}`,
    }),
  };

  const env = {
    DB: {
      prepare: (query: string) => statement(query),
      batch: (stmts: ReturnType<typeof statement>[]) => Promise.all(stmts.map((s) => s.run())),
    },
    AI: ai,
    ASSETS: { fetch: async () => new Response("<html>chat</html>") },
    RATE_LIMITER: { limit: async () => ({ success: true }) },
    LLM_PROVIDER: "workers-ai",
    WORKERS_AI_MODEL: "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    ANTHROPIC_MODEL: "claude-opus-5-5",
    OPENAI_MODEL: "gpt-4o-mini",
    JUDGE_MODEL: "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    AI_GATEWAY_ID: "default",
    APP_VERSION: "2.0.0",
    GIT_COMMIT: "abc1234",
    ADMIN_TOKEN: "s3cret",
    ...overrides,
  } as unknown as Env;

  return { env, sql: (query, ...args) => db.prepare(query).all(...args), gatewayCalls, patched };
}

const usage = { prompt_tokens: 200, completion_tokens: 30, neurons: 11 };

/** A well-behaved tool-using model: search, calculate, answer. Judge calls
 * (no tools) get a JSON verdict. */
export function scriptedModel(answer = "According to the handbook, the answer is 147."): Script {
  return (input) => {
    if (!input.tools) {
      return { response: { on_purpose: 5, grounded: 4, helpful: 5, reasoning: "Supported by the handbook." }, usage };
    }
    const toolTurns = input.messages.filter((m) => m.role === "tool").length;
    if (toolTurns === 0) return { tool_calls: [{ name: "search_docs", arguments: { query: "starter pro price" } }], usage };
    if (toolTurns === 1) return { tool_calls: [{ name: "calculator", arguments: { expression: "5 * 12 + 3 * 29" } }], usage };
    return { response: answer, tool_calls: [], usage };
  };
}

export const ctx = () => {
  const pending: Promise<unknown>[] = [];
  return { waitUntil: (p: Promise<unknown>) => void pending.push(p), passThroughOnException() {}, pending } as
    unknown as ExecutionContext & { pending: Promise<unknown>[] };
};

export const basic = (password: string) => `Basic ${btoa(`admin:${password}`)}`;
