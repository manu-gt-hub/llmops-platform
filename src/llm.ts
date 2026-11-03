/**
 * llm.ts — One small interface over three providers, all routed through
 * Cloudflare AI Gateway (requests, tokens, cost, errors and logs in the
 * Cloudflare dashboard, tagged with the agent and run id).
 *
 *   workers-ai  (default, free daily allocation)  env.AI.run(..., { gateway })
 *   anthropic   official SDK, base URL = the gateway's Anthropic endpoint
 *   openai      official SDK, base URL = the gateway's OpenAI endpoint
 *
 * Each conversation keeps the provider's native message list, so whatever the
 * model returns (thinking blocks, tool-call ids) is sent back unchanged.
 */

import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";

import type { Env } from "./env";
import type { Usage } from "./pricing";
import type { ToolSpec } from "./tools";

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface Turn {
  text: string;
  toolCalls: ToolCall[];
  stop: string; // tool_use | end_turn | refusal | max_tokens | ...
  model: string;
  usage: Usage;
  gatewayLogId: string | null; // Workers AI only
}

export interface ToolOutcome {
  call: ToolCall;
  output: string;
  isError: boolean;
}

export interface Conversation {
  provider: string;
  model: string;
  /** One model call. With finalAnswer the model may not call tools and must
   * answer from what it has (see agent.ts). */
  step(tags: Record<string, string>, finalAnswer?: boolean): Promise<Turn>;
  addResults(outcomes: ToolOutcome[]): void;
}

/** The free Workers AI allocation for the day is used up. */
export class QuotaExceededError extends Error {}

export function activeModel(env: Env): { provider: string; model: string } {
  if (env.LLM_PROVIDER === "anthropic" && env.ANTHROPIC_API_KEY) return { provider: "anthropic", model: env.ANTHROPIC_MODEL };
  if (env.LLM_PROVIDER === "openai" && env.OPENAI_API_KEY) return { provider: "openai", model: env.OPENAI_MODEL };
  return { provider: "workers-ai", model: env.WORKERS_AI_MODEL };
}

export function newConversation(env: Env, system: string, question: string, tools: ToolSpec[]): Conversation {
  const { provider, model } = activeModel(env);
  if (provider === "anthropic") return new AnthropicConversation(env, model, system, question, tools);
  if (provider === "openai") return new OpenAIConversation(env, model, system, question, tools);
  return new WorkersAiConversation(env, model, system, question, tools);
}

/** Paid providers go through AI Gateway when CF_AIG_TOKEN is set (the
 * gateway requires authentication), otherwise straight to the provider. */
async function gatewayOptions(env: Env, provider: string, tags: Record<string, string>) {
  if (!env.CF_AIG_TOKEN) return { baseURL: undefined, headers: {} };
  return {
    baseURL: await env.AI.gateway(env.AI_GATEWAY_ID).getUrl(provider),
    headers: { "cf-aig-authorization": `Bearer ${env.CF_AIG_TOKEN}`, "cf-aig-metadata": JSON.stringify(tags) },
  };
}

// ---------------------------------------------------------------------------
// Workers AI
// ---------------------------------------------------------------------------
type WorkersAiMessage = { role: string; content: string; name?: string };
type WorkersAiResult = {
  response?: string | object;
  tool_calls?: { name: string; arguments: Record<string, unknown> | string }[];
  usage?: { prompt_tokens?: number; completion_tokens?: number; neurons?: number };
};

export class WorkersAiConversation implements Conversation {
  provider = "workers-ai";
  private messages: WorkersAiMessage[];

  constructor(private env: Env, public model: string, system: string, question: string, private tools: ToolSpec[]) {
    this.messages = [{ role: "system", content: system }, { role: "user", content: question }];
  }

  async step(tags: Record<string, string>, finalAnswer = false): Promise<Turn> {
    let out: WorkersAiResult;
    try {
      out = (await this.env.AI.run(
        this.model as keyof AiModels,
        { messages: this.messages, tools: finalAnswer ? undefined : this.tools, max_tokens: 1024 } as never,
        { gateway: { id: this.env.AI_GATEWAY_ID, metadata: tags } },
      )) as WorkersAiResult;
    } catch (e) {
      // Daily free allocation used up: error 3036 / HTTP 429
      if (/3036|daily free allocation|429/i.test(String(e))) throw new QuotaExceededError(String(e));
      throw e;
    }
    const calls = (out.tool_calls ?? []).map((c, i) => ({
      id: `call_${this.messages.length}_${i}`,
      name: c.name,
      args: typeof c.arguments === "string" ? safeJson(c.arguments) : c.arguments ?? {},
    }));
    const text = typeof out.response === "string" ? out.response : out.response ? JSON.stringify(out.response) : "";
    for (const c of calls) this.messages.push({ role: "assistant", content: JSON.stringify({ name: c.name, arguments: c.args }) });
    if (!calls.length) this.messages.push({ role: "assistant", content: text });
    return {
      text,
      toolCalls: calls,
      stop: calls.length ? "tool_use" : "end_turn",
      model: this.model,
      usage: {
        inputTokens: out.usage?.prompt_tokens ?? 0,
        outputTokens: out.usage?.completion_tokens ?? 0,
        neurons: out.usage?.neurons ?? 0,
      },
      gatewayLogId: this.env.AI.aiGatewayLogId ?? null,
    };
  }

  addResults(outcomes: ToolOutcome[]): void {
    for (const o of outcomes) {
      this.messages.push({ role: "tool", name: o.call.name, content: o.isError ? `ERROR: ${o.output}` : o.output });
    }
  }
}

function safeJson(text: string): Record<string, unknown> {
  try {
    return JSON.parse(text);
  } catch {
    return { _invalid_json: text };
  }
}

// ---------------------------------------------------------------------------
// Anthropic (official SDK)
// ---------------------------------------------------------------------------
// Models that accept the server-side refusal fallback ("default" routing).
const FALLBACK_MODELS = new Set(["claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"]);

class AnthropicConversation implements Conversation {
  provider = "anthropic";
  private messages: Anthropic.Beta.BetaMessageParam[];

  constructor(private env: Env, public model: string, private system: string, question: string, private tools: ToolSpec[]) {
    this.messages = [{ role: "user", content: question }];
  }

  async step(tags: Record<string, string>, finalAnswer = false): Promise<Turn> {
    const gw = await gatewayOptions(this.env, "anthropic", tags);
    const client = new Anthropic({ apiKey: this.env.ANTHROPIC_API_KEY, baseURL: gw.baseURL, defaultHeaders: gw.headers });
    const fallback = FALLBACK_MODELS.has(this.model)
      ? { betas: ["server-side-fallback-2026-07-01"], fallbacks: "default" as const }
      : {};
    const response = await client.beta.messages.create({
      model: this.model,
      max_tokens: 16000,
      system: this.system,
      tools: this.tools.map((t) => ({ name: t.name, description: t.description, input_schema: t.parameters })),
      // Tools stay declared (cache-friendly); "none" forbids calling them
      ...(finalAnswer ? { tool_choice: { type: "none" as const } } : {}),
      messages: this.messages,
      output_config: { effort: "medium" },
      ...fallback,
    });
    // Append the full content (thinking and fallback blocks included).
    this.messages.push({ role: "assistant", content: response.content as Anthropic.Beta.BetaContentBlockParam[] });
    const calls: ToolCall[] = [];
    let text = "";
    for (const block of response.content) {
      if (block.type === "tool_use") calls.push({ id: block.id, name: block.name, args: block.input as Record<string, unknown> });
      if (block.type === "text") text += block.text;
    }
    return {
      text,
      toolCalls: calls,
      stop: response.stop_reason ?? "",
      model: response.model,
      usage: {
        inputTokens: response.usage.input_tokens,
        outputTokens: response.usage.output_tokens,
        cacheReadTokens: response.usage.cache_read_input_tokens ?? 0,
        cacheWriteTokens: response.usage.cache_creation_input_tokens ?? 0,
      },
      gatewayLogId: null,
    };
  }

  addResults(outcomes: ToolOutcome[]): void {
    // All results of one turn go back in a single user message.
    this.messages.push({
      role: "user",
      content: outcomes.map((o) => ({ type: "tool_result" as const, tool_use_id: o.call.id, content: o.output, is_error: o.isError })),
    });
  }
}

// ---------------------------------------------------------------------------
// OpenAI (official SDK)
// ---------------------------------------------------------------------------
class OpenAIConversation implements Conversation {
  provider = "openai";
  private messages: OpenAI.Chat.ChatCompletionMessageParam[];

  constructor(private env: Env, public model: string, system: string, question: string, private tools: ToolSpec[]) {
    this.messages = [{ role: "system", content: system }, { role: "user", content: question }];
  }

  async step(tags: Record<string, string>, finalAnswer = false): Promise<Turn> {
    const gw = await gatewayOptions(this.env, "openai", tags);
    const client = new OpenAI({ apiKey: this.env.OPENAI_API_KEY, baseURL: gw.baseURL, defaultHeaders: gw.headers });
    const response = await client.chat.completions.create({
      model: this.model,
      messages: this.messages,
      tools: this.tools.map((t) => ({ type: "function" as const, function: t })),
      ...(finalAnswer ? { tool_choice: "none" as const } : {}),
    });
    const choice = response.choices[0];
    this.messages.push(choice.message);
    const calls = (choice.message.tool_calls ?? [])
      .filter((tc) => tc.type === "function")
      .map((tc) => ({ id: tc.id, name: tc.function.name, args: safeJson(tc.function.arguments || "{}") }));
    const stop = { tool_calls: "tool_use", stop: "end_turn", length: "max_tokens", content_filter: "refusal" }[
      choice.finish_reason as string
    ];
    const cached = response.usage?.prompt_tokens_details?.cached_tokens ?? 0;
    return {
      text: choice.message.content ?? "",
      toolCalls: calls,
      stop: stop ?? choice.finish_reason ?? "",
      model: response.model,
      usage: {
        inputTokens: (response.usage?.prompt_tokens ?? 0) - cached,
        outputTokens: response.usage?.completion_tokens ?? 0,
        cacheReadTokens: cached,
      },
      gatewayLogId: null,
    };
  }

  addResults(outcomes: ToolOutcome[]): void {
    for (const o of outcomes) {
      this.messages.push({ role: "tool", tool_call_id: o.call.id, content: o.isError ? `ERROR: ${o.output}` : o.output });
    }
  }
}
