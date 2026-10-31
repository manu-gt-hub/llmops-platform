"""Dashboards as code, demo data story and docs rendering."""

import random
from datetime import UTC, datetime

from gateway import config
from gateway.pricing import PriceList
from gateway.seed import Generator
from tools import build_dashboards


def test_committed_dashboards_match_the_generator():
    for name, content in build_dashboards.render().items():
        committed = (build_dashboards.OUT_DIR / name).read_text(encoding="utf-8")
        assert committed == content, f"{name} is stale: run python tools/build_dashboards.py"


def test_panel_ids_are_unique():
    for build in build_dashboards.DASHBOARDS.values():
        ids = [p["id"] for p in build()["panels"]]
        assert len(ids) == len(set(ids))


def _generate() -> Generator:
    gen = Generator(30, datetime(2026, 10, 31, 18, tzinfo=UTC),
                    PriceList.load(config.PRICING_FILE), random.Random(42))
    gen.generate()
    return gen


def test_seed_is_deterministic():
    assert _generate().requests == _generate().requests


def test_seed_tells_the_release_story():
    status, cost, release = 7, 14, 16  # column positions in RUN_COLUMNS
    runs = _generate().runs

    def runs_of(version):
        return [r for r in runs if r[release] == version]

    def avg_cost(version):
        rows = runs_of(version)
        return sum(r[cost] for r in rows) / len(rows)

    assert any(r[status] == "killed" for r in runs_of("0.2.1")), "0.2.1 introduced the tool loop"
    assert not any(r[status] == "killed" for r in runs_of("0.2.2")), "0.2.2 fixed it"
    assert avg_cost("0.3.0") < avg_cost("0.2.2") * 0.8, "prompt caching cuts the cost per run"


def test_architecture_page_renders_mermaid():
    from gateway.main import DOC_DIR, _render

    page = _render(DOC_DIR / "architecture.md")
    assert page.startswith("<!doctype html>")
