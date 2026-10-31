"""Pricing, usage normalization and budgets (no database needed)."""

import json

import pytest

from gateway import config
from gateway.budgets import Budget, Budgets, over_budget
from gateway.pricing import Price, PriceList
from gateway.usage import StreamUsage, Usage, parse_response, parse_usage


@pytest.fixture
def prices() -> PriceList:
    return PriceList({
        "claude-opus-5-5": Price(input=4.0, output=20.0, cache_read=0.20, cache_write=5.0),
        "gpt-4o-mini": Price(input=0.15, output=0.60, cache_read=0.075),
        "gpt-4o": Price(input=2.5, output=10.0),
    })


def test_cost_includes_every_token_type(prices):
    usage = Usage(input_tokens=1_000, output_tokens=500, cache_read_tokens=2_000, cache_write_tokens=1_000)
    cost = prices.cost("claude-opus-5-5", usage)
    assert cost.priced
    assert cost.usd == pytest.approx((1_000 * 4 + 500 * 20 + 2_000 * 0.2 + 1_000 * 5) / 1e6)
    assert cost.cache_savings_usd == pytest.approx(2_000 * (4 - 0.2) / 1e6)


def test_dated_snapshot_uses_longest_prefix(prices):
    assert prices.lookup("gpt-4o-mini-2024-07-18") == prices.models["gpt-4o-mini"]
    assert prices.lookup("gpt-4o-2024-11-20") == prices.models["gpt-4o"]


def test_unknown_model_is_flagged_unpriced(prices):
    cost = prices.cost("brand-new-model", Usage(100, 100))
    assert not cost.priced and cost.usd == 0


def test_price_file_loads_and_covers_agent_defaults():
    prices = PriceList.load(config.PRICING_FILE)
    for model in ("claude-opus-5-5", "claude-haiku-5-5", "gpt-4o-mini"):
        assert prices.lookup(model) is not None, model


def test_anthropic_usage_keeps_cache_tokens_separate():
    usage = parse_usage("anthropic", {"input_tokens": 50, "output_tokens": 10,
                                      "cache_read_input_tokens": 900, "cache_creation_input_tokens": 100})
    assert usage == Usage(50, 10, 900, 100)


def test_openai_usage_subtracts_cached_prompt_tokens():
    usage = parse_usage("openai", {"prompt_tokens": 1_500, "completion_tokens": 20,
                                   "prompt_tokens_details": {"cached_tokens": 1_024}})
    assert usage == Usage(476, 20, 1_024, 0)


def test_openai_responses_api_shape():
    usage = parse_usage("openai", {"input_tokens": 300, "output_tokens": 40,
                                   "input_tokens_details": {"cached_tokens": 100}})
    assert usage == Usage(200, 40, 100, 0)


def test_parse_response_returns_served_model():
    model, usage = parse_response("anthropic", {"model": "claude-opus-5-5", "usage": {"input_tokens": 3}})
    assert model == "claude-opus-5-5" and usage.input_tokens == 3


def _sse(*events) -> bytes:
    return b"".join(f"event: x\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def test_anthropic_stream_usage_split_across_chunks():
    stream = _sse(
        {"type": "message_start", "message": {"model": "claude-opus-5-5", "usage": {
            "input_tokens": 12, "output_tokens": 1, "cache_read_input_tokens": 400}}},
        {"type": "content_block_delta", "delta": {"text": "hi"}},
        {"type": "message_delta", "usage": {"output_tokens": 57}},
    )
    collector = StreamUsage("anthropic")
    for i in range(0, len(stream), 7):  # arbitrary network chunking
        collector.feed(stream[i:i + 7])
    collector.close()
    assert collector.model == "claude-opus-5-5"
    assert collector.usage == Usage(12, 57, 400, 0)


def test_openai_stream_usage_from_final_chunk():
    stream = (_sse({"model": "gpt-4o-mini", "choices": [{"delta": {"content": "hi"}}]},
                   {"model": "gpt-4o-mini", "choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 5}})
              + b"data: [DONE]\n\n")
    collector = StreamUsage("openai")
    collector.feed(stream)
    collector.close()
    assert collector.usage == Usage(30, 5, 0, 0)


def test_budgets_fallback_and_limit(tmp_path):
    path = tmp_path / "budgets.yaml"
    path.write_text("default: {monthly_usd: 5, alert_at: 0.5}\nprojects:\n  a: {monthly_usd: 10}\n")
    budgets = Budgets.load(path)
    assert budgets.for_project("a") == Budget("a", 10, 0.5)
    assert budgets.for_project("unknown").monthly_usd == 5
    assert not over_budget(9.99, budgets.for_project("a"))
    assert over_budget(10, budgets.for_project("a"))
    assert not over_budget(1_000, Budget("free", 0, 0.8)), "0 means no limit"
