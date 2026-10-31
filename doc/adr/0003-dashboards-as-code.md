# 0003 — Grafana dashboards generated from Python

**Status:** Accepted

## Context

Hand-edited Grafana JSON is thousands of lines, hard to review, and drifts
between environments. Panels repeat the same filters (demo/real data,
project) and the same release annotations.

## Decision

- [tools/build_dashboards.py](../../tools/build_dashboards.py) defines the
  four dashboards with small builders (`stat`, `timeseries`, `table`, ...)
  and writes the JSON that Grafana provisions read-only.
- CI fails if the committed JSON differs from the generator output
  (`--check`) and runs **every** panel query against a live Grafana
  ([tools/check_dashboards.py](../../tools/check_dashboards.py)).
- Data sources and alert rules are provisioned from YAML as well.

## Consequences

- ➕ Reviews show the query that changed, not a JSON diff of grid positions.
- ➕ A broken SQL query fails CI instead of showing "No data" in a demo.
- ➖ Edits made in the Grafana UI are not saved (`allowUiUpdates: false`):
  prototype in the UI, then port to the generator.
