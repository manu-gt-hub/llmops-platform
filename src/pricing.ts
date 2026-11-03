/**
 * pricing.ts — Cost of one LLM call (config/pricing.json).
 *
 * Workers AI reports the exact neurons of each call in `usage.neurons`; their
 * price is what it would cost beyond the free daily allocation. Paid
 * providers are priced per token.
 */

import pricing from "../config/pricing.json";

export interface Usage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  neurons?: number; // Workers AI only
}

const PER_MILLION = 1_000_000;
type Price = { input: number; output: number; cache_read: number; cache_write: number };
const MODELS = pricing.models_usd_per_million_tokens as Record<string, Price>;

export const FREE_NEURONS_PER_DAY = pricing.workers_ai.free_neurons_per_day;

export function neuronsToUsd(neurons: number): number {
  return (neurons / 1000) * pricing.workers_ai.usd_per_1000_neurons;
}

/** USD cost; null when the model has no price (it is still recorded, at $0). */
export function costUsd(provider: string, model: string, usage: Usage): number | null {
  if (provider === "workers-ai") return neuronsToUsd(usage.neurons ?? 0);
  const price = MODELS[model];
  if (!price) return null;
  return (
    (usage.inputTokens * price.input +
      usage.outputTokens * price.output +
      (usage.cacheReadTokens ?? 0) * price.cache_read +
      (usage.cacheWriteTokens ?? 0) * price.cache_write) /
    PER_MILLION
  );
}
