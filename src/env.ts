/** Bindings, variables and secrets declared in wrangler.jsonc. */
export interface Env {
  AI: Ai;
  DB: D1Database;
  ASSETS: Fetcher;
  RATE_LIMITER?: RateLimit;

  // vars
  LLM_PROVIDER: string; // "workers-ai" | "anthropic" | "openai"
  WORKERS_AI_MODEL: string;
  ANTHROPIC_MODEL: string;
  OPENAI_MODEL: string;
  JUDGE_MODEL: string;
  AI_GATEWAY_ID: string;
  APP_VERSION: string;
  GIT_COMMIT: string;

  // secrets
  ADMIN_TOKEN?: string;
  ANTHROPIC_API_KEY?: string;
  OPENAI_API_KEY?: string;
  CF_AIG_TOKEN?: string; // routes the paid providers through an authenticated AI Gateway
}
