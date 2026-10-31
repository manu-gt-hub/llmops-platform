"""
usage.py — Normalize token usage from provider responses.

Every provider reports usage differently; the rest of the gateway only sees
`Usage`, where `input_tokens` are always the *uncached* input tokens:

  Anthropic   input_tokens is already uncached; cache tokens are separate.
  OpenAI      prompt_tokens / input_tokens include the cached ones, which
              are reported in *_tokens_details.cached_tokens.
"""

import json
from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    def merge_max(self, other: "Usage") -> None:
        """Streams report cumulative counters, so keep the highest value seen."""
        self.input_tokens = max(self.input_tokens, other.input_tokens)
        self.output_tokens = max(self.output_tokens, other.output_tokens)
        self.cache_read_tokens = max(self.cache_read_tokens, other.cache_read_tokens)
        self.cache_write_tokens = max(self.cache_write_tokens, other.cache_write_tokens)


def _int(value) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def parse_usage(provider: str, usage: dict | None) -> Usage:
    if not usage:
        return Usage()
    if provider == "anthropic":
        return Usage(
            input_tokens=_int(usage.get("input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
            cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
            cache_write_tokens=_int(usage.get("cache_creation_input_tokens")),
        )
    # OpenAI: Chat Completions (prompt/completion) or Responses (input/output)
    prompt = _int(usage.get("prompt_tokens", usage.get("input_tokens")))
    output = _int(usage.get("completion_tokens", usage.get("output_tokens")))
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached = _int(details.get("cached_tokens"))
    return Usage(input_tokens=max(prompt - cached, 0), output_tokens=output, cache_read_tokens=cached)


def parse_response(provider: str, body: dict) -> tuple[str, Usage]:
    """Return (model, usage) from a non-streaming JSON response."""
    return str(body.get("model") or ""), parse_usage(provider, body.get("usage"))


class StreamUsage:
    """Incrementally parse a server-sent-events stream and collect usage.

    Anthropic sends usage in `message_start` (input/cache) and
    `message_delta` (output); OpenAI sends it in the last chunk when
    `stream_options.include_usage` is set (the gateway sets it), and the
    Responses API in `response.completed`.
    """

    def __init__(self, provider: str):
        self.provider = provider
        self.model = ""
        self.usage = Usage()
        self._buffer = b""

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            self._handle_line(line.strip())

    def close(self) -> None:
        if self._buffer:
            self._handle_line(self._buffer.strip())
            self._buffer = b""

    def _handle_line(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        payload = line[5:].strip()
        if not payload or payload == b"[DONE]":
            return
        try:
            event = json.loads(payload)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        # The model and usage can be nested in `message` (Anthropic
        # message_start) or `response` (OpenAI Responses API events).
        for container in (event, event.get("message"), event.get("response")):
            if not isinstance(container, dict):
                continue
            if container.get("model"):
                self.model = str(container["model"])
            if isinstance(container.get("usage"), dict):
                self.usage.merge_max(parse_usage(self.provider, container["usage"]))
