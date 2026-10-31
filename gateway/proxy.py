"""
proxy.py — Transparent pass-through to the LLM providers.

Clients keep using the official SDKs and only change the base URL:

  Anthropic(base_url="http://gateway:8080/anthropic")
  OpenAI(base_url="http://gateway:8080/openai/v1")

The gateway swaps in the real API key, enforces the guardrails (project
budget, per-run cost), forwards the request untouched (streaming included)
and records tokens, cost and latency of every call.
"""

import json
import logging
import time
from dataclasses import dataclass

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from opentelemetry import trace

from gateway import config, db, metrics
from gateway.budgets import Budgets, over_budget
from gateway.pricing import PriceList
from gateway.telemetry import trace_id_hex
from gateway.usage import StreamUsage, Usage, parse_response

log = logging.getLogger("gateway.proxy")

# Headers that describe the client's connection or carry its credentials;
# never forwarded upstream.
_DROP_REQUEST_HEADERS = {
    "host", "content-length", "connection", "authorization", "x-api-key",
    "accept-encoding", "x-project", "x-agent", "x-run-id", "traceparent", "tracestate",
}
# httpx already decoded the body, so the length/encoding no longer apply.
_DROP_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding", "connection"}


@dataclass
class Attribution:
    project: str
    agent: str | None
    run_id: str | None


@dataclass
class Upstream:
    base_url: str
    api_key: str
    key_env: str

    def auth_headers(self, provider: str) -> dict[str, str]:
        if provider == "anthropic":
            return {"x-api-key": self.api_key}
        return {"authorization": f"Bearer {self.api_key}"}


UPSTREAMS = {
    "anthropic": Upstream(config.ANTHROPIC_UPSTREAM, config.ANTHROPIC_API_KEY, "ANTHROPIC_API_KEY"),
    "openai": Upstream(config.OPENAI_UPSTREAM, config.OPENAI_API_KEY, "OPENAI_API_KEY"),
}


def _error(status: int, kind: str, message: str) -> JSONResponse:
    # Shaped like the providers' own errors so SDKs surface the message.
    return JSONResponse({"type": "error", "error": {"type": kind, "message": message}}, status_code=status)


def _prepare_body(provider: str, path: str, raw: bytes) -> tuple[bytes, dict]:
    """Parse the JSON body; for OpenAI streams ask for the usage chunk."""
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        return raw, {}
    if not isinstance(body, dict):
        return raw, {}
    if provider == "openai" and body.get("stream") and path.endswith("chat/completions"):
        options = body.get("stream_options") or {}
        if not options.get("include_usage"):
            body["stream_options"] = {**options, "include_usage": True}
            raw = json.dumps(body).encode()
    return raw, body


class Proxy:
    def __init__(self, prices: PriceList, budgets: Budgets, tracer: trace.Tracer):
        self.prices = prices
        self.budgets = budgets
        self.tracer = tracer
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(config.UPSTREAM_TIMEOUT_S, connect=10))

    async def aclose(self) -> None:
        await self.client.aclose()

    async def guardrails(self, who: Attribution) -> JSONResponse | None:
        """402 (not retried by the SDKs) when a limit is already reached."""
        budget = self.budgets.for_project(who.project)
        spent = await db.month_spend(who.project)
        if over_budget(spent, budget):
            metrics.REJECTED.labels(who.project, "budget").inc()
            return _error(402, "budget_exceeded",
                          f"Project '{who.project}' reached its monthly budget "
                          f"(${spent:.2f} of ${budget.monthly_usd:.2f}).")
        if who.run_id and config.MAX_RUN_COST_USD > 0:
            run_cost = await db.run_spend(who.run_id)
            if run_cost >= config.MAX_RUN_COST_USD:
                metrics.REJECTED.labels(who.project, "run_cost").inc()
                return _error(402, "run_cost_limit",
                              f"Run '{who.run_id}' reached the per-run limit "
                              f"(${run_cost:.4f} of ${config.MAX_RUN_COST_USD:.2f}).")
        return None

    async def forward(self, request: Request, provider: str, path: str, who: Attribution) -> Response:
        upstream = UPSTREAMS[provider]
        if not upstream.api_key:
            return _error(503, "provider_not_configured",
                          f"{upstream.key_env} is not set in the gateway's .env")
        if (rejection := await self.guardrails(who)) is not None:
            return rejection

        raw, body = _prepare_body(provider, path, await request.body())
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}
        headers.update(upstream.auth_headers(provider))
        url = f"{upstream.base_url}/{path}"
        if request.url.query:
            url += f"?{request.url.query}"

        requested_model = str(body.get("model") or "unknown")
        span = self.tracer.start_span(
            f"chat {requested_model}",
            kind=trace.SpanKind.CLIENT,
            attributes={
                "gen_ai.system": provider,
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": requested_model,
                "llmops.project": who.project,
                "llmops.agent": who.agent or "",
                "llmops.run_id": who.run_id or "",
            },
        )
        started = time.perf_counter()
        upstream_request = self.client.build_request(request.method, url, headers=headers, content=raw)

        try:
            if body.get("stream"):
                resp = await self.client.send(upstream_request, stream=True)
            else:
                resp = await self.client.send(upstream_request)
        except httpx.HTTPError as exc:
            await self._record(provider, path, who, requested_model, Usage(), started,
                               502, str(exc), False, span)
            return _error(502, "upstream_unreachable", f"{provider}: {exc}")

        response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS}

        if body.get("stream") and resp.status_code == 200:
            return StreamingResponse(
                self._stream(resp, provider, path, who, requested_model, started, span),
                status_code=resp.status_code,
                headers=response_headers,
            )

        content = await resp.aread()
        await resp.aclose()
        model, usage, error = requested_model, Usage(), None
        if resp.status_code == 200:
            try:
                parsed_model, usage = parse_response(provider, json.loads(content))
                model = parsed_model or requested_model
            except ValueError:
                pass
        else:
            error = content[:500].decode(errors="replace")
        await self._record(provider, path, who, model, usage, started,
                           resp.status_code, error, False, span)
        return Response(content, status_code=resp.status_code, headers=response_headers)

    async def _stream(self, resp, provider, path, who, requested_model, started, span):
        collector = StreamUsage(provider)
        error = None
        try:
            async for chunk in resp.aiter_bytes():
                collector.feed(chunk)
                yield chunk
        except httpx.HTTPError as exc:
            error = f"stream interrupted: {exc}"
            raise
        finally:
            collector.close()
            await resp.aclose()
            await self._record(provider, path, who, collector.model or requested_model,
                               collector.usage, started, resp.status_code, error, True, span)

    async def _record(self, provider, path, who, model, usage: Usage, started,
                      status_code, error, streamed, span) -> None:
        latency = time.perf_counter() - started
        cost = self.prices.cost(model, usage)
        status = "ok" if status_code < 400 else "error"

        span.set_attributes({
            "gen_ai.response.model": model,
            "gen_ai.usage.input_tokens": usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
            "gen_ai.usage.output_tokens": usage.output_tokens,
            "llmops.cost_usd": cost.usd,
            "http.response.status_code": status_code,
        })
        if error:
            span.set_status(trace.Status(trace.StatusCode.ERROR, error[:200]))
        span.end()

        metrics.REQUESTS.labels(provider, model, who.project, status).inc()
        metrics.LATENCY.labels(provider, model).observe(latency)
        for kind, value in (("input", usage.input_tokens), ("output", usage.output_tokens),
                            ("cache_read", usage.cache_read_tokens), ("cache_write", usage.cache_write_tokens)):
            if value:
                metrics.TOKENS.labels(provider, model, who.project, kind).inc(value)
        if cost.usd:
            metrics.COST.labels(provider, model, who.project).inc(cost.usd)
        if not cost.priced and usage.total_tokens:
            metrics.UNPRICED.labels(model).inc()

        try:
            await db.insert_request({
                "provider": provider,
                "model": model,
                "project": who.project,
                "agent": who.agent,
                "run_id": who.run_id,
                "endpoint": path,
                "streamed": streamed,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "cost_usd": cost.usd,
                "cache_savings_usd": cost.cache_savings_usd,
                "priced": cost.priced or not usage.total_tokens,
                "latency_ms": int(latency * 1000),
                "status_code": status_code,
                "error": error,
                "trace_id": trace_id_hex(span),
                "release": config.APP_VERSION,
            })
            budget = self.budgets.for_project(who.project)
            if budget.monthly_usd > 0:
                spent = await db.month_spend(who.project)
                metrics.BUDGET_UTILIZATION.labels(who.project).set(spent / budget.monthly_usd)
        except Exception:  # recording must never break the client's request
            log.exception("could not record request")
