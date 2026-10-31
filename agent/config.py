"""
config.py — Configuration of the demo research agent.

The agent never holds provider keys: every LLM call goes through the gateway,
which also decides which providers are available.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8090").rstrip("/")

AGENT_NAME = os.getenv("AGENT_NAME", "research-agent")
AGENT_PROJECT = os.getenv("AGENT_PROJECT", "research-agent")

# "anthropic" | "openai"; empty = the first provider the gateway has a key for
# (Anthropic preferred).
AGENT_PROVIDER = os.getenv("AGENT_PROVIDER", "").strip().lower()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# Claude effort level: low | medium | high | xhigh | max
AGENT_EFFORT = os.getenv("AGENT_EFFORT", "medium")

# --- Guardrails (the gateway also enforces MAX_RUN_COST_USD per run) ---
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "8"))
# The same tool called with the same arguments this many times = a loop.
LOOP_REPEAT_LIMIT = int(os.getenv("AGENT_LOOP_REPEAT_LIMIT", "3"))

OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")

TASKS_FILE = Path(__file__).with_name("tasks.json")
BASELINE_FILE = PROJECT_ROOT / "evals" / "baseline.json"
RESULTS_DIR = PROJECT_ROOT / "results"
