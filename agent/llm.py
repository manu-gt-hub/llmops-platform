"""
llm.py — Provider adapters behind one small interface.

Both use the official SDKs pointed at the gateway (base_url), so the gateway
sees and accounts for every call. The run id and the trace context travel as
headers on each request.
"""

import json
from dataclasses import dataclass, field

import anthropic
import openai

from agent import config
from agent.tools import TOOL_SPECS

SYSTEM_PROMPT = (
    "You are a research assistant for Acme Cloud employees. Answer questions using only "
    "the Acme Cloud handbook, which you can query with your tools. Look up every fact you "
    "rely on, and use the calculator for any arithmetic. When you have the answer, reply "
    "with one short paragraph that states the final figure or name explicitly. If the "
    "handbook does not contain the answer, say so."
)

# Models that accept the server-side refusal fallback ("default" routing).
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class GatewayRejected(Exception):
    """The gateway refused the call: 402 for spending limits, 403 for the
    agent's charter or a guardrail. Retrying would not help."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str  # tool_use | end_turn | refusal | max_tokens | ...
    model: str = ""


@dataclass
class ToolOutcome:
    call: ToolCall
    output: str
    is_error: bool = False


def _rejection(exc: Exception) -> GatewayRejected | None:
    if getattr(exc, "status_code", None) not in (402, 403):
        return None
    body = getattr(exc, "body", None) or {}
    err = body.get("error", {}) if isinstance(body, dict) else {}
    return GatewayRejected(err.get("type", "rejected"), err.get("message", str(exc)))


class AnthropicConversation:
    provider = "anthropic"

    def __init__(self, question: str, model: str = config.ANTHROPIC_MODEL):
        self.model = model
        self.client = anthropic.Anthropic(
            base_url=f"{config.GATEWAY_URL}/anthropic",
            api_key="via-gateway",  # the gateway injects the real key
            default_headers={"X-Project": config.AGENT_PROJECT, "X-Agent": config.AGENT_NAME},
        )
        self.messages: list = [{"role": "user", "content": question}]
        self.tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"], "strict": True}
            for t in TOOL_SPECS
        ]

    def step(self, headers: dict) -> Turn:
        extra = {}
        if self.model in _FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=self.tools,
                messages=self.messages,
                # Caches tools + system + history prefix across the run's turns.
                cache_control={"type": "ephemeral"},
                output_config={"effort": config.AGENT_EFFORT},
                extra_headers=headers,
                **extra,
            )
        except anthropic.APIStatusError as exc:
            rejection = _rejection(exc)
            if rejection is None:
                raise
            raise rejection from exc
        # Append the full content (thinking and fallback blocks included).
        self.messages.append({"role": "assistant", "content": response.content})
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"]
        text = "".join(b.text for b in response.content if b.type == "text")
        return Turn(text, calls, response.stop_reason or "", response.model)

    def add_results(self, outcomes: list[ToolOutcome]) -> None:
        # All results of one turn go back in a single user message.
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": o.call.id, "content": o.output, "is_error": o.is_error}
            for o in outcomes
        ]})


class OpenAIConversation:
    provider = "openai"

    def __init__(self, question: str, model: str = config.OPENAI_MODEL):
        self.model = model
        self.client = openai.OpenAI(
            base_url=f"{config.GATEWAY_URL}/openai/v1",
            api_key="via-gateway",
            default_headers={"X-Project": config.AGENT_PROJECT, "X-Agent": config.AGENT_NAME},
        )
        self.messages: list = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        self.tools = [{"type": "function", "function": t} for t in TOOL_SPECS]

    def step(self, headers: dict) -> Turn:
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=self.messages, tools=self.tools, extra_headers=headers,
            )
        except openai.APIStatusError as exc:
            rejection = _rejection(exc)
            if rejection is None:
                raise
            raise rejection from exc
        choice = response.choices[0]
        message = choice.message
        self.messages.append(message.model_dump(exclude_none=True))
        calls = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {"_invalid_json": tc.function.arguments}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        stop = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens",
                "content_filter": "refusal"}.get(choice.finish_reason, choice.finish_reason or "")
        return Turn(message.content or "", calls, stop, response.model)

    def add_results(self, outcomes: list[ToolOutcome]) -> None:
        for o in outcomes:
            content = f"ERROR: {o.output}" if o.is_error else o.output
            self.messages.append({"role": "tool", "tool_call_id": o.call.id, "content": content})


@dataclass
class ProviderChoice:
    provider: str
    model: str
    available: dict = field(default_factory=dict)


def choose_provider(available: dict) -> ProviderChoice:
    """Pick the provider from AGENT_PROVIDER or what the gateway has keys for."""
    provider = config.AGENT_PROVIDER or ("anthropic" if available.get("anthropic") else "openai")
    if not available.get(provider):
        raise RuntimeError(
            f"The gateway has no key for '{provider}'. Set ANTHROPIC_API_KEY or OPENAI_API_KEY "
            "in .env and restart the gateway (docker compose up -d gateway)."
        )
    model = config.ANTHROPIC_MODEL if provider == "anthropic" else config.OPENAI_MODEL
    return ProviderChoice(provider, model, available)


def new_conversation(choice: ProviderChoice, question: str):
    if choice.provider == "anthropic":
        return AnthropicConversation(question, choice.model)
    return OpenAIConversation(question, choice.model)
