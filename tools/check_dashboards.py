"""
check_dashboards.py — Smoke test: run every dashboard query through Grafana.

Catches SQL / PromQL errors and (with --require-data) panels that come back
empty against the demo data. Used by CI after `docker compose up`.

  python tools/check_dashboards.py [--grafana http://localhost:3000] [--require-data]
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "observability" / "grafana" / "dashboards"
# Panels that only show real traffic may be empty. Tempo searches run from
# the browser (Grafana's query API does not serve them), so they are skipped.
LIVE_DATASOURCES = {"prometheus"}
SKIP_DATASOURCES = {"tempo"}


def post(url: str, payload: dict, auth: str) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read() or b"{}")


def interpolate(query: str, projects: list[str]) -> str:
    """Grafana's query API does not expand dashboard variables; do it here."""
    return (query.replace("'$source'", "'all'")
                 .replace("$project", ", ".join(f"'{p}'" for p in projects))
                 .replace("$__rate_interval", "1m"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grafana", default="http://localhost:3000")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--require-data", action="store_true", help="fail on empty SQL panels")
    args = parser.parse_args()
    auth = base64.b64encode(f"{args.user}:{args.password}".encode()).decode()
    api = f"{args.grafana}/api/ds/query"

    projects_resp = post(api, {"from": "now-30d", "to": "now", "queries": [{
        "refId": "P", "datasource": {"uid": "llmops-postgres"}, "format": "table", "rawQuery": True,
        "rawSql": "SELECT DISTINCT project FROM llm_requests"}]}, auth)
    try:
        frames = projects_resp["results"]["P"]["frames"]
        projects = frames[0]["data"]["values"][0] if frames else []
    except (KeyError, IndexError):
        print(f"cannot query Grafana: {projects_resp}")
        return 1
    projects = projects or ["default"]

    failures = checked = 0
    for path in sorted(DASHBOARD_DIR.glob("*.json")):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        releases = dashboard["annotations"]["list"][1]["target"]
        queries = [(releases, "annotation: releases", releases["datasource"]["type"])]
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                queries.append((target, panel["title"], target["datasource"]["type"]))
        for target, title, ds_type in queries:
            if ds_type in SKIP_DATASOURCES:
                print(f"skip  {path.stem:<9} {title} (browser-only query)")
                continue
            query = dict(target)
            for key in ("rawSql", "expr", "query"):
                if key in query:
                    query[key] = interpolate(query[key], projects)
            result = post(api, {"from": "now-30d", "to": "now", "queries": [query]}, auth)
            frame_result = (result.get("results") or {}).get(query["refId"], {})
            error = frame_result.get("error") or result.get("message")
            rows = sum(len((f.get("data", {}).get("values") or [[]])[0]) for f in frame_result.get("frames", []))
            checked += 1
            live = ds_type in LIVE_DATASOURCES
            if error:
                failures += 1
                print(f"FAIL  {path.stem:<9} {title}: {error}")
            elif not rows and args.require_data and not live:
                failures += 1
                print(f"EMPTY {path.stem:<9} {title}")
            else:
                print(f"ok    {path.stem:<9} {title} ({rows} rows{', live' if live else ''})")
    print(f"\n{checked} queries checked, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
