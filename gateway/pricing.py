"""
pricing.py — Price list and per-request cost calculation.

Prices live in config/pricing.yaml (USD per 1M tokens) so that a price change
is a reviewed commit, not a code change.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from gateway.usage import Usage

PER_TOKEN = 1_000_000


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(frozen=True)
class Cost:
    usd: float
    priced: bool
    # What the cached tokens would have cost at the full input price, minus
    # what they actually cost. Feeds the "prompt caching savings" panel.
    cache_savings_usd: float = 0.0


class PriceList:
    def __init__(self, models: dict[str, Price], as_of: str = ""):
        self.models = models
        self.as_of = as_of

    @classmethod
    def load(cls, path: Path) -> "PriceList":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        models = {
            name: Price(
                input=float(p.get("input", 0)),
                output=float(p.get("output", 0)),
                cache_read=float(p.get("cache_read", 0)),
                cache_write=float(p.get("cache_write", 0)),
            )
            for name, p in (data.get("models") or {}).items()
        }
        return cls(models, str(data.get("as_of", "")))

    def lookup(self, model: str) -> Price | None:
        """Exact id first, then the longest listed id the model starts with
        (dated snapshots such as gpt-4o-mini-2024-07-18)."""
        if model in self.models:
            return self.models[model]
        matches = [name for name in self.models if model.startswith(name)]
        return self.models[max(matches, key=len)] if matches else None

    def cost(self, model: str, usage: Usage) -> Cost:
        price = self.lookup(model)
        if price is None:
            return Cost(0.0, priced=False)
        usd = (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_read_tokens * price.cache_read
            + usage.cache_write_tokens * price.cache_write
        ) / PER_TOKEN
        savings = usage.cache_read_tokens * (price.input - price.cache_read) / PER_TOKEN
        return Cost(round(usd, 8), priced=True, cache_savings_usd=round(savings, 8))
