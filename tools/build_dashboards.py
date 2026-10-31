"""
build_dashboards.py — Grafana dashboards as code.

Generates observability/grafana/dashboards/*.json from the definitions
below. Edit this file, not the JSON:

  python tools/build_dashboards.py          # regenerate
  python tools/build_dashboards.py --check  # CI: fail if the JSON is stale
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

OUT_DIR = Path(__file__).resolve().parent.parent / "observability" / "grafana" / "dashboards"

PG = {"type": "grafana-postgresql-datasource", "uid": "llmops-postgres"}
PROM = {"type": "prometheus", "uid": "llmops-prometheus"}
TEMPO = {"type": "tempo", "uid": "llmops-tempo"}

# Data filters shared by every SQL query: demo vs real data, and projects.
SRC = "('$source' = 'all' OR synthetic = ('$source' = 'demo'))"
PROJ = "project IN ($project)"

COLORS = ["#3274D9", "#56A64B", "#F2CC0C", "#E0752D", "#A352CC", "#37872D", "#8AB8FF", "#C4162A"]


# ---------------------------------------------------------------------------
# Query and panel builders
# ---------------------------------------------------------------------------
def sql(query: str, fmt: str = "time_series", ref: str = "A") -> dict:
    return {"refId": ref, "datasource": PG, "rawSql": " ".join(query.split()), "format": fmt,
            "rawQuery": True, "editorMode": "code"}


def prom(expr: str, legend: str = "", ref: str = "A", instant: bool = False) -> dict:
    return {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend,
            "instant": instant, "range": not instant}


def _ds(targets: list[dict]) -> dict:
    return targets[0]["datasource"]


def _thresholds(steps: list[tuple[float | None, str]] | None) -> dict:
    steps = steps or [(None, "green")]
    return {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in steps]}


def stat(title, targets, unit="none", decimals=None, thresholds=None, text_field=None,
         description="", color_mode="value") -> dict:
    reduce = {"calcs": ["lastNotNull"], "fields": f"/^{text_field}$/" if text_field else "", "values": False}
    return {
        "type": "stat", "title": title, "description": description, "datasource": _ds(targets),
        "targets": targets,
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "thresholds": _thresholds(thresholds),
                                     "color": {"mode": "thresholds"}}, "overrides": []},
        "options": {"reduceOptions": reduce, "colorMode": color_mode, "graphMode": "none",
                    "textMode": "value", "justifyMode": "center", "wideLayout": True},
    }


def timeseries(title, targets, unit="none", stacked=False, bars=False, description="", decimals=None,
               overrides=None) -> dict:
    custom = {
        "drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": 80 if bars else 12,
        "barAlignment": 0, "showPoints": "never", "spanNulls": True, "pointSize": 4,
        "stacking": {"mode": "normal" if stacked else "none", "group": "A"},
        "axisSoftMin": 0,
    }
    return {
        "type": "timeseries", "title": title, "description": description, "datasource": _ds(targets),
        "targets": targets,
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "custom": custom,
                                     "color": {"mode": "palette-classic"}},
                        "overrides": overrides or []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
    }


def table(title, targets, overrides=None, description="", sort_by=None) -> dict:
    return {
        "type": "table", "title": title, "description": description, "datasource": _ds(targets),
        "targets": targets,
        "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
                        "overrides": overrides or []},
        "options": {"showHeader": True, "cellHeight": "sm",
                    "sortBy": [{"displayName": sort_by, "desc": True}] if sort_by else []},
    }


def piechart(title, targets, unit="currencyUSD", description="") -> dict:
    return {
        "type": "piechart", "title": title, "description": description, "datasource": _ds(targets),
        "targets": targets,
        "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"}}, "overrides": []},
        "options": {"pieType": "donut", "legend": {"displayMode": "table", "placement": "right",
                                                   "values": ["value", "percent"], "showLegend": True},
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                    "tooltip": {"mode": "single"}},
    }


def bargauge(title, targets, unit="percentunit", max_value=1.2, thresholds=None, description="") -> dict:
    return {
        "type": "bargauge", "title": title, "description": description, "datasource": _ds(targets),
        "targets": targets,
        "fieldConfig": {"defaults": {"unit": unit, "min": 0, "max": max_value,
                                     "thresholds": _thresholds(thresholds), "color": {"mode": "thresholds"}},
                        "overrides": []},
        "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                    "valueMode": "color", "namePlacement": "left",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True}},
    }


def text(title, content) -> dict:
    return {"type": "text", "title": title, "options": {"mode": "markdown", "content": content}}


def unit_override(field: str, unit: str, decimals: int | None = None) -> dict:
    props = [{"id": "unit", "value": unit}]
    if decimals is not None:
        props.append({"id": "decimals", "value": decimals})
    return {"matcher": {"id": "byName", "options": field}, "properties": props}


def link_override(field: str, title: str, url: str) -> dict:
    return {"matcher": {"id": "byName", "options": field},
            "properties": [{"id": "links", "value": [{"title": title, "url": url}]}]}


def color_override(field: str, color: str) -> dict:
    return {"matcher": {"id": "byName", "options": field},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}


def tempo_trace_url() -> str:
    panes = {"t": {"datasource": TEMPO["uid"], "queries": [
        {"refId": "A", "datasource": TEMPO, "queryType": "traceql", "query": "TRACE_ID"}],
        "range": {"from": "now-7d", "to": "now"}}}
    encoded = quote(json.dumps(panes, separators=(",", ":")))
    return "/explore?schemaVersion=1&panes=" + encoded.replace("TRACE_ID", "${__value.raw}")


class Layout:
    """Places panels left to right on Grafana's 24-column grid."""

    def __init__(self):
        self.panels: list[dict] = []
        self.x = self.y = self.row_h = 0

    def add(self, panel: dict, w: int, h: int) -> None:
        if self.x + w > 24:
            self.newline()
        panel["id"] = len(self.panels) + 1
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.panels.append(panel)
        self.x += w
        self.row_h = max(self.row_h, h)

    def newline(self) -> None:
        self.x, self.y, self.row_h = 0, self.y + self.row_h, 0

    def row(self, title: str) -> None:
        self.newline()
        self.panels.append({"type": "row", "title": title, "collapsed": False, "panels": [],
                            "id": len(self.panels) + 1, "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}})
        self.y += 1


# ---------------------------------------------------------------------------
# Shared dashboard pieces
# ---------------------------------------------------------------------------
def variables(with_project: bool = True) -> list[dict]:
    source = {
        "name": "source", "label": "Data", "type": "custom", "query": "all,real,demo",
        "current": {"text": "all", "value": "all"},
        "options": [{"text": v, "value": v, "selected": v == "all"} for v in ("all", "real", "demo")],
        "description": "real = traffic through the gateway; demo = synthetic seed data",
    }
    if not with_project:
        return [source]
    project = {
        "name": "project", "label": "Project", "type": "query", "datasource": PG,
        "query": "SELECT DISTINCT project FROM llm_requests ORDER BY 1",
        "definition": "SELECT DISTINCT project FROM llm_requests ORDER BY 1",
        "refresh": 2, "multi": True, "includeAll": True,
        "current": {"text": ["All"], "value": ["$__all"]}, "sort": 1,
    }
    return [source, project]


def deployment_annotations() -> list[dict]:
    return [
        {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True,
         "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
        {"name": "Releases", "datasource": PG, "enable": True, "iconColor": "#A352CC",
         "target": sql(f"""SELECT ts AS time, 'Release ' || version || COALESCE(' — ' || notes, '') AS text,
                           service AS tags FROM deployments WHERE $__timeFilter(ts) AND {SRC}""", "table", "Anno")},
    ]


def dashboard(uid: str, title: str, layout: Layout, description: str, time_from: str = "now-30d",
              with_project: bool = True) -> dict:
    return {
        "uid": uid, "title": title, "description": description, "tags": ["llmops"],
        "timezone": "browser", "editable": False, "graphTooltip": 1, "schemaVersion": 39,
        "time": {"from": time_from, "to": "now"}, "refresh": "1m",
        "templating": {"list": variables(with_project)},
        "annotations": {"list": deployment_annotations()},
        "links": [
            {"title": "LLMOps", "type": "dashboards", "tags": ["llmops"], "asDropdown": False,
             "includeVars": True, "keepTime": True},
            {"title": "Architecture", "type": "link", "url": "http://localhost:8090/architecture",
             "targetBlank": True, "icon": "doc"},
        ],
        "panels": layout.panels,
    }


DAY = "$__timeGroupAlias(ts, '1d', 0)"
TOKENS = "input_tokens + output_tokens + cache_read_tokens + cache_write_tokens"
COST_PER_SUCCESS = ("SUM(cost_usd) FILTER (WHERE status = 'success') / "
                    "NULLIF(COUNT(*) FILTER (WHERE status = 'success'), 0)")


# ---------------------------------------------------------------------------
# Overview (home)
# ---------------------------------------------------------------------------
def overview() -> dict:
    lay = Layout()
    lay.add(stat("Last release", [sql(f"""SELECT version, ts FROM deployments WHERE {SRC}
                                         ORDER BY ts DESC LIMIT 1""", "table")],
                 text_field="version", color_mode="background",
                 thresholds=[(None, "#3274D9")], description="Latest gateway release that started"), 4, 4)
    lay.add(stat("Deployed", [sql(f"""SELECT extract(epoch FROM ts) * 1000 AS deployed FROM deployments
                                     WHERE {SRC} ORDER BY ts DESC LIMIT 1""", "table")],
                 unit="dateTimeFromNow"), 4, 4)
    lay.add(stat("Spend month to date", [sql(f"""SELECT COALESCE(SUM(cost_usd), 0) FROM llm_requests
                 WHERE ts >= date_trunc('month', now()) AND {SRC} AND {PROJ}""", "table")],
                 unit="currencyUSD", decimals=2), 4, 4)
    lay.add(stat("Tokens today", [sql(f"""SELECT COALESCE(SUM({TOKENS}), 0) FROM llm_requests
                 WHERE ts >= date_trunc('day', now()) AND {SRC} AND {PROJ}""", "table")],
                 unit="short", decimals=1), 4, 4)
    lay.add(stat("Agent success rate (7d)", [sql(f"""SELECT AVG((status = 'success')::int) FROM agent_runs
                 WHERE started_at >= now() - interval '7 days' AND status <> 'running' AND {SRC}""", "table")],
                 unit="percentunit", decimals=1, thresholds=[(None, "red"), (0.7, "orange"), (0.85, "green")]), 4, 4)
    lay.add(stat("Eval pass rate (7d)", [sql(f"""SELECT AVG(passed::int) FROM eval_results
                 WHERE ts >= now() - interval '7 days' AND {SRC}""", "table")],
                 unit="percentunit", decimals=1, thresholds=[(None, "red"), (0.75, "orange"), (0.875, "green")]), 4, 4)

    lay.add(timeseries("Spend per day by project", [sql(f"""SELECT {DAY}, project AS metric,
                 SUM(cost_usd) AS value FROM llm_requests WHERE $__timeFilter(ts) AND {SRC} AND {PROJ}
                 GROUP BY 1, 2 ORDER BY 1""")], unit="currencyUSD", stacked=True, bars=True, decimals=2), 12, 9)
    lay.add(timeseries("Tokens per day", [sql(f"""SELECT {DAY},
                 SUM(input_tokens) AS "input", SUM(output_tokens) AS "output",
                 SUM(cache_read_tokens) AS "cache read", SUM(cache_write_tokens) AS "cache write"
                 FROM llm_requests WHERE $__timeFilter(ts) AND {SRC} AND {PROJ} GROUP BY 1 ORDER BY 1""")],
                 unit="short", stacked=True, bars=True), 12, 9)

    lay.add(bargauge("Budget used this month", [sql(f"""SELECT r.project AS "project",
                 SUM(r.cost_usd) / MAX(COALESCE(b.monthly_usd, d.monthly_usd)) AS "used"
                 FROM llm_requests r LEFT JOIN budgets b ON b.project = r.project
                 CROSS JOIN (SELECT monthly_usd FROM budgets WHERE project = 'default') d
                 WHERE r.ts >= date_trunc('month', now()) AND {SRC.replace('synthetic', 'r.synthetic')}
                 AND r.{PROJ} GROUP BY 1 ORDER BY 2 DESC""", "table")],
                 thresholds=[(None, "green"), (0.8, "orange"), (1, "red")],
                 description="Month-to-date spend / monthly budget (config/budgets.yaml)"), 10, 8)
    lay.add(table("Release history", [sql(f"""SELECT ts AS "deployed", version, git_commit AS "commit",
                 environment, notes FROM deployments WHERE {SRC} ORDER BY ts DESC LIMIT 20""", "table")]), 14, 8)

    lay.add(text("About this platform", """
**LLMOps Platform** — every LLM call goes through the gateway, which records tokens, cost and latency,
enforces budgets and traces agent runs.

| Dashboard | What it answers |
|---|---|
| **FinOps** | Where does the money go? Cost per model / project / successful run, cache savings, forecast |
| **AgentOps** | Do the agents work? Success rate, steps, loops, tool errors, eval scores, traces |
| **Platform** | What is deployed and is it healthy? Releases, throughput, latency, errors |

📐 [Architecture](http://localhost:8090/architecture) · 🔌 [Gateway API](http://localhost:8090/docs) ·
📈 [Prometheus](http://localhost:9090) · Use the **Data** selector to switch between real and demo data.
"""), 24, 6)
    return dashboard("llmops-overview", "LLMOps — Overview", lay,
                     "Spend, tokens, last release and agent health at a glance")


# ---------------------------------------------------------------------------
# FinOps
# ---------------------------------------------------------------------------
def finops() -> dict:
    lay = Layout()
    lay.add(stat("Spend in range", [sql(f"""SELECT COALESCE(SUM(cost_usd), 0) FROM llm_requests
                 WHERE $__timeFilter(ts) AND {SRC} AND {PROJ}""", "table")], unit="currencyUSD", decimals=2), 4, 4)
    lay.add(stat("Month-end forecast", [sql(f"""SELECT COALESCE(SUM(cost_usd), 0)
                 / GREATEST(extract(epoch FROM now() - date_trunc('month', now())) / 86400, 1)
                 * extract(day FROM date_trunc('month', now()) + interval '1 month - 1 day')
                 FROM llm_requests WHERE ts >= date_trunc('month', now()) AND {SRC} AND {PROJ}""", "table")],
                 unit="currencyUSD", decimals=2,
                 description="Linear projection: month-to-date spend / days elapsed × days in month"), 4, 4)
    lay.add(stat("Prompt-cache savings", [sql(f"""SELECT COALESCE(SUM(cache_savings_usd), 0) FROM llm_requests
                 WHERE $__timeFilter(ts) AND {SRC} AND {PROJ}""", "table")], unit="currencyUSD", decimals=2,
                 description="Cached input tokens × (input price − cache read price)"), 4, 4)
    lay.add(stat("Cost per successful run", [sql(f"""SELECT {COST_PER_SUCCESS} FROM agent_runs
                 WHERE $__timeFilter(started_at) AND {SRC} AND {PROJ}""", "table")], unit="currencyUSD", decimals=4,
                 description="Agent spend of successful runs / number of successful runs"), 4, 4)
    lay.add(stat("Requests", [sql(f"""SELECT COUNT(*) FROM llm_requests
                 WHERE $__timeFilter(ts) AND {SRC} AND {PROJ}""", "table")], unit="short"), 4, 4)
    lay.add(stat("Unpriced requests", [sql(f"""SELECT COUNT(*) FROM llm_requests
                 WHERE NOT priced AND $__timeFilter(ts) AND {SRC} AND {PROJ}""", "table")], unit="short",
                 thresholds=[(None, "green"), (1, "red")],
                 description="Models missing in config/pricing.yaml: their cost is recorded as $0"), 4, 4)

    lay.add(timeseries("Cost per day by model", [sql(f"""SELECT {DAY}, model AS metric, SUM(cost_usd) AS value
                 FROM llm_requests WHERE $__timeFilter(ts) AND {SRC} AND {PROJ} GROUP BY 1, 2 ORDER BY 1""")],
                 unit="currencyUSD", stacked=True, bars=True, decimals=2), 16, 9)
    lay.add(piechart("Spend by project", [sql(f"""SELECT project AS metric, SUM(cost_usd) AS value
                 FROM llm_requests WHERE $__timeFilter(ts) AND {SRC} AND {PROJ} GROUP BY 1 ORDER BY 2 DESC""",
                                              "table")]), 8, 9)

    lay.add(timeseries("Cost per successful agent run", [sql(f"""SELECT $__timeGroupAlias(started_at, '1d', 0),
                 {COST_PER_SUCCESS} AS "cost per success",
                 SUM(cost_usd) / NULLIF(COUNT(*), 0) AS "cost per run"
                 FROM agent_runs WHERE $__timeFilter(started_at) AND {SRC} AND {PROJ} GROUP BY 1 ORDER BY 1""")],
                 unit="currencyUSD", decimals=4,
                 description="Unit economics of the agent. Compare with the release annotations."), 12, 8)
    lay.add(timeseries("Prompt-cache hit ratio", [sql(f"""SELECT {DAY}, project AS metric,
                 SUM(cache_read_tokens)::float / NULLIF(SUM(input_tokens + cache_read_tokens + cache_write_tokens), 0)
                 AS value FROM llm_requests WHERE $__timeFilter(ts) AND {SRC} AND {PROJ}
                 GROUP BY 1, 2 ORDER BY 1""")], unit="percentunit",
                 description="Share of input tokens served from the prompt cache"), 12, 8)

    lay.add(piechart("Spend by model", [sql(f"""SELECT model AS metric, SUM(cost_usd) AS value
                 FROM llm_requests WHERE $__timeFilter(ts) AND {SRC} AND {PROJ} GROUP BY 1 ORDER BY 2 DESC""",
                                            "table")]), 8, 9)
    lay.add(table("Most expensive agent runs", [sql(f"""SELECT run_id, task_id AS task, status, steps,
                 total_tokens AS tokens, cost_usd AS cost, started_at AS started FROM agent_runs
                 WHERE $__timeFilter(started_at) AND {SRC} AND {PROJ} ORDER BY cost_usd DESC LIMIT 15""", "table")],
                 overrides=[unit_override("cost", "currencyUSD", 4)]), 16, 9)

    lay.add(table("Spend by project and model", [sql(f"""SELECT project, model, COUNT(*) AS requests,
                 SUM(input_tokens) AS "input", SUM(output_tokens) AS "output",
                 SUM(cache_read_tokens) AS "cache read", SUM(cost_usd) AS cost,
                 SUM(cost_usd) / NULLIF(COUNT(*), 0) AS "cost / request"
                 FROM llm_requests WHERE $__timeFilter(ts) AND {SRC} AND {PROJ}
                 GROUP BY 1, 2 ORDER BY cost DESC""", "table")],
                 overrides=[unit_override("cost", "currencyUSD", 2), unit_override("cost / request", "currencyUSD", 5)]),
            24, 8)
    return dashboard("llmops-finops", "LLMOps — FinOps", lay,
                     "Token usage and spend by date, model and project; budgets, cache savings and unit cost")


# ---------------------------------------------------------------------------
# AgentOps
# ---------------------------------------------------------------------------
def agentops() -> dict:
    lay = Layout()
    runs_where = f"$__timeFilter(started_at) AND status <> 'running' AND {SRC} AND {PROJ}"
    lay.add(stat("Runs", [sql(f"SELECT COUNT(*) FROM agent_runs WHERE {runs_where}", "table")], unit="short"), 4, 4)
    lay.add(stat("Success rate", [sql(f"SELECT AVG((status = 'success')::int) FROM agent_runs WHERE {runs_where}",
                                      "table")], unit="percentunit", decimals=1,
                 thresholds=[(None, "red"), (0.7, "orange"), (0.85, "green")]), 4, 4)
    lay.add(stat("Killed by guardrails", [sql(f"SELECT COUNT(*) FROM agent_runs WHERE status = 'killed' AND {runs_where}",
                                              "table")], unit="short", thresholds=[(None, "green"), (1, "orange")],
                 description="Runs stopped for loops, max steps or the per-run cost limit"), 4, 4)
    lay.add(stat("Avg steps per run", [sql(f"SELECT AVG(steps) FROM agent_runs WHERE {runs_where}", "table")],
                 decimals=1), 4, 4)
    lay.add(stat("Tool error rate", [sql(f"""SELECT AVG((NOT ok)::int) FROM agent_steps
                 WHERE kind = 'tool' AND $__timeFilter(ts) AND {SRC}""", "table")], unit="percentunit", decimals=1,
                 thresholds=[(None, "green"), (0.05, "orange"), (0.15, "red")]), 4, 4)
    lay.add(stat("Eval pass rate", [sql(f"""SELECT AVG(passed::int) FROM eval_results
                 WHERE $__timeFilter(ts) AND {SRC}""", "table")], unit="percentunit", decimals=1,
                 thresholds=[(None, "red"), (0.75, "orange"), (0.875, "green")]), 4, 4)

    lay.add(timeseries("Runs per day by outcome", [sql(f"""SELECT $__timeGroupAlias(started_at, '1d', 0),
                 COUNT(*) FILTER (WHERE status = 'success') AS "success",
                 COUNT(*) FILTER (WHERE status = 'failed') AS "failed",
                 COUNT(*) FILTER (WHERE status = 'killed') AS "killed",
                 COUNT(*) FILTER (WHERE status = 'error') AS "error"
                 FROM agent_runs WHERE {runs_where} GROUP BY 1 ORDER BY 1""")], stacked=True, bars=True,
                 overrides=[color_override("success", "#56A64B"), color_override("failed", "#F2CC0C"),
                            color_override("killed", "#C4162A"), color_override("error", "#8F3BB8")]), 12, 9)
    lay.add(timeseries("Eval pass rate and steps per run", [
        sql(f"""SELECT $__timeGroupAlias(ts, '1d', 0), AVG(passed::int) AS "eval pass rate"
                FROM eval_results WHERE $__timeFilter(ts) AND {SRC} GROUP BY 1 ORDER BY 1"""),
        sql(f"""SELECT $__timeGroupAlias(started_at, '1d', 0), AVG(steps) AS "avg steps"
                FROM agent_runs WHERE {runs_where} GROUP BY 1 ORDER BY 1""", ref="B")],
        overrides=[unit_override("eval pass rate", "percentunit"),
                   {"matcher": {"id": "byName", "options": "avg steps"},
                    "properties": [{"id": "custom.axisPlacement", "value": "right"}]}],
        description="Quality (nightly eval suite) against effort (steps). Releases are annotated."), 12, 9)

    lay.add(table("Tools", [sql(f"""SELECT name AS tool, COUNT(*) AS calls,
                 COUNT(*) FILTER (WHERE NOT ok) AS errors, AVG((NOT ok)::int) AS "error rate",
                 percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS "p95 ms"
                 FROM agent_steps WHERE kind = 'tool' AND $__timeFilter(ts) AND {SRC}
                 GROUP BY 1 ORDER BY calls DESC""", "table")],
                 overrides=[unit_override("error rate", "percentunit", 1), unit_override("p95 ms", "ms", 0)]), 10, 7)
    lay.add(timeseries("Stop reasons", [sql(f"""SELECT $__timeGroupAlias(started_at, '1d', 0),
                 stop_reason AS metric, COUNT(*) AS value FROM agent_runs
                 WHERE {runs_where} AND stop_reason <> 'end_turn' GROUP BY 1, 2 ORDER BY 1""")],
                 stacked=True, bars=True, description="Why runs ended, other than a normal answer"), 14, 7)

    lay.add(table("Recent runs", [sql(f"""SELECT started_at AS started, run_id, task_id AS task, model, status,
                 stop_reason, steps, tool_calls AS tools, tool_errors, loop_detected AS loop,
                 total_tokens AS tokens, cost_usd AS cost, trace_id AS trace FROM agent_runs
                 WHERE {runs_where} ORDER BY started_at DESC LIMIT 50""", "table")],
                 overrides=[unit_override("cost", "currencyUSD", 4),
                            link_override("trace", "Open trace in Tempo", tempo_trace_url())],
                 description="Real runs carry a trace id: click it to open the trace"), 24, 10)

    traces = {"refId": "A", "datasource": TEMPO, "queryType": "traceql", "limit": 20, "tableType": "traces",
              "query": '{ span.gen_ai.operation.name = "invoke_agent" }'}
    lay.add({"type": "table", "title": "Agent traces (Tempo)", "datasource": TEMPO, "targets": [traces],
             "description": "Latest agent runs as traces: run span → LLM calls (gateway) and tool calls",
             "fieldConfig": {"defaults": {}, "overrides": []}, "options": {"showHeader": True}}, 24, 8)
    return dashboard("llmops-agentops", "LLMOps — AgentOps", lay,
                     "Agent runs, outcomes, loops, tool reliability, eval quality and traces", time_from="now-30d")


# ---------------------------------------------------------------------------
# Platform (DevOps)
# ---------------------------------------------------------------------------
def platform() -> dict:
    lay = Layout()
    build = 'llmops_build_info{job="llm-gateway"}'
    lay.add(stat("Running version", [prom(build, "{{version}}", instant=True)], color_mode="background",
                 thresholds=[(None, "#3274D9")], description="From the gateway's /metrics (build info)"), 4, 4)
    lay.add({**stat("Commit", [prom(build, "{{commit}}", instant=True)]),
             "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                         "textMode": "name", "colorMode": "none", "graphMode": "none", "justifyMode": "center"}}, 4, 4)
    lay.add(stat("Uptime", [prom('time() - process_start_time_seconds{job="llm-gateway"}', instant=True)],
                 unit="s", decimals=0), 4, 4)
    lay.add(stat("Releases in range", [sql(f"SELECT COUNT(*) FROM deployments WHERE $__timeFilter(ts) AND {SRC}",
                                           "table")], unit="short", description="Deployment frequency"), 4, 4)
    lay.add(stat("Error rate (range)", [sql(f"""SELECT AVG((status_code >= 400)::int) FROM llm_requests
                 WHERE $__timeFilter(ts) AND {SRC} AND {PROJ}""", "table")], unit="percentunit", decimals=2,
                 thresholds=[(None, "green"), (0.02, "orange"), (0.05, "red")]), 4, 4)
    lay.add(stat("p95 latency (range)", [sql(f"""SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                 FROM llm_requests WHERE $__timeFilter(ts) AND {SRC} AND {PROJ}""", "table")], unit="ms", decimals=0), 4, 4)

    lay.add(timeseries("Requests per second (live)", [prom(
        'sum by (provider, status) (rate(llm_requests_total[$__rate_interval]))', "{{provider}} {{status}}")],
        unit="reqps", description="Prometheus — real traffic only"), 12, 8)
    lay.add(timeseries("Upstream latency p50 / p95 (live)", [
        prom('histogram_quantile(0.5, sum by (le, model) (rate(llm_request_duration_seconds_bucket[$__rate_interval])))',
             "p50 {{model}}"),
        prom('histogram_quantile(0.95, sum by (le, model) (rate(llm_request_duration_seconds_bucket[$__rate_interval])))',
             "p95 {{model}}", ref="B")], unit="s", description="Prometheus — real traffic only"), 12, 8)

    lay.add(timeseries("Latency p95 per day by provider", [sql(f"""SELECT {DAY}, provider AS metric,
                 percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS value FROM llm_requests
                 WHERE $__timeFilter(ts) AND {SRC} AND {PROJ} GROUP BY 1, 2 ORDER BY 1""")], unit="ms"), 12, 8)
    lay.add(timeseries("Errors per day by status code", [sql(f"""SELECT {DAY}, status_code::text AS metric,
                 COUNT(*) AS value FROM llm_requests WHERE status_code >= 400 AND $__timeFilter(ts)
                 AND {SRC} AND {PROJ} GROUP BY 1, 2 ORDER BY 1""")], stacked=True, bars=True,
                 description="429/529 = provider overloaded, 402 = gateway guardrail"), 12, 8)

    lay.add(timeseries("Guardrail rejections (live)", [prom(
        'sum by (project, reason) (increase(llm_guardrail_rejections_total[$__rate_interval]))',
        "{{project}} {{reason}}")], bars=True, description="Budget or per-run cost limit reached"), 10, 8)
    lay.add(table("Release history", [sql(f"""SELECT ts AS deployed, version, git_commit AS commit, build_date,
                 environment, notes FROM deployments WHERE {SRC} ORDER BY ts DESC LIMIT 30""", "table")]), 14, 8)
    return dashboard("llmops-platform", "LLMOps — Platform", lay,
                     "What is deployed and whether the gateway is healthy", time_from="now-30d")


DASHBOARDS = {"overview.json": overview, "finops.json": finops, "agentops.json": agentops,
              "platform.json": platform}


def render() -> dict[str, str]:
    return {name: json.dumps(build(), indent=2, ensure_ascii=False) + "\n" for name, build in DASHBOARDS.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--check", action="store_true", help="fail if the committed JSON is out of date")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stale = []
    for name, content in render().items():
        path = OUT_DIR / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(name)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(OUT_DIR.parent.parent.parent)}")
    if stale:
        print(f"Dashboards out of date: {', '.join(stale)}. Run: python tools/build_dashboards.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
