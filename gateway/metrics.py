"""
metrics.py — Prometheus metrics exposed on /metrics.

Postgres holds the per-request history (FinOps, AgentOps); Prometheus covers
the live operational view: throughput, latency, error rate and build info.
"""

from prometheus_client import Counter, Gauge, Histogram, Info

from gateway import config

BUILD_INFO = Info("llmops_build", "Version of the running gateway")
BUILD_INFO.info({
    "version": config.APP_VERSION,
    "commit": config.GIT_COMMIT,
    "build_date": config.BUILD_DATE,
    "environment": config.DEPLOY_ENV,
})

REQUESTS = Counter(
    "llm_requests_total", "LLM requests proxied", ["provider", "model", "project", "status"]
)
TOKENS = Counter(
    "llm_tokens_total", "Tokens processed", ["provider", "model", "project", "type"]
)
COST = Counter("llm_cost_usd_total", "Spend in USD", ["provider", "model", "project"])
UNPRICED = Counter("llm_unpriced_requests_total", "Requests for models missing in pricing.yaml", ["model"])
LATENCY = Histogram(
    "llm_request_duration_seconds",
    "Upstream latency (time to last byte)",
    ["provider", "model"],
    buckets=(0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120),
)
REJECTED = Counter(
    "llm_guardrail_rejections_total", "Requests refused by the gateway", ["project", "reason"]
)
BUDGET_UTILIZATION = Gauge(
    "llm_budget_utilization_ratio", "Month-to-date spend / monthly budget", ["project"]
)
AGENT_RUNS = Counter("agent_runs_total", "Finished agent runs", ["agent", "status"])
