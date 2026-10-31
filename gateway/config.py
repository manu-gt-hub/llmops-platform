"""
config.py — Centralized configuration for the LLM gateway.
Loads variables from .env and exposes constants for all modules.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _secret(name: str) -> str:
    """Read an API key, treating the .env.example placeholders as unset."""
    value = os.getenv(name, "").strip()
    return "" if not value or "XXXX" in value else value


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Upstream providers. The gateway holds the real keys; clients never see them.
# ---------------------------------------------------------------------------
OPENAI_API_KEY = _secret("OPENAI_API_KEY")
ANTHROPIC_API_KEY = _secret("ANTHROPIC_API_KEY")
OPENAI_UPSTREAM = os.getenv("OPENAI_UPSTREAM", "https://api.openai.com").rstrip("/")
ANTHROPIC_UPSTREAM = os.getenv("ANTHROPIC_UPSTREAM", "https://api.anthropic.com").rstrip("/")
UPSTREAM_TIMEOUT_S = float(os.getenv("UPSTREAM_TIMEOUT_S", "600"))

# ---------------------------------------------------------------------------
# Attribution and guardrails
# ---------------------------------------------------------------------------
DEFAULT_PROJECT = os.getenv("DEFAULT_PROJECT", "default")
# Kill switch for agent runs: once a run (X-Run-Id) has spent this much, the
# gateway refuses its next LLM call with 429.
MAX_RUN_COST_USD = float(os.getenv("MAX_RUN_COST_USD", "0.50"))

# --- Governance (config/agents.yaml) and LLM-as-judge ---
AGENTS_FILE = Path(os.getenv("AGENTS_FILE", PROJECT_ROOT / "config" / "agents.yaml"))
# Empty = claude-sonnet-5-5 if the Anthropic key is set, else gpt-4o-mini.
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "").strip()
# How the gateway reaches itself (the judge's calls go through the gateway).
GATEWAY_SELF_URL = os.getenv("GATEWAY_SELF_URL", "http://localhost:8080").rstrip("/")

PRICING_FILE = Path(os.getenv("PRICING_FILE", PROJECT_ROOT / "config" / "pricing.yaml"))
BUDGETS_FILE = Path(os.getenv("BUDGETS_FILE", PROJECT_ROOT / "config" / "budgets.yaml"))

# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5433"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "llmops")
POSTGRES_USER = os.getenv("POSTGRES_USER", "llmops")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "llmops")

POSTGRES_DSN = (
    f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
    f"user={POSTGRES_USER} password={POSTGRES_PASSWORD}"
)

# ---------------------------------------------------------------------------
# Demo data: 30 days of synthetic usage so the dashboards are never empty.
# Rows are flagged synthetic=true and can be filtered out in Grafana.
# ---------------------------------------------------------------------------
SEED_DEMO_DATA = _bool("SEED_DEMO_DATA", "true")
SEED_DAYS = int(os.getenv("SEED_DAYS", "30"))

# ---------------------------------------------------------------------------
# Tracing (OTLP/HTTP). Empty endpoint disables span export.
# ---------------------------------------------------------------------------
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")

# ---------------------------------------------------------------------------
# Build / deployment metadata, baked into the image at build time.
# ---------------------------------------------------------------------------
_version_file = PROJECT_ROOT / "VERSION"
APP_VERSION = os.getenv("APP_VERSION", "").strip() or (
    _version_file.read_text().strip() if _version_file.exists() else "0.0.0"
)
GIT_COMMIT = os.getenv("GIT_COMMIT", "").strip() or "local"
BUILD_DATE = os.getenv("BUILD_DATE", "").strip() or "unknown"
DEPLOY_ENV = os.getenv("DEPLOY_ENV", "local")
SERVICE_NAME = "llm-gateway"
REPO_URL = os.getenv("REPO_URL", "https://github.com/manu-gt-hub/llmops-platform")

GATEWAY_HOST = os.getenv("GATEWAY_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8080"))
