-- D1 schema. Applied by `npm run migrate` (the deploy workflow runs it).
-- Timestamps are UTC text, 'YYYY-MM-DD HH:MM:SS'.

-- One row per LLM call (FinOps ledger).
CREATE TABLE llm_calls (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              TEXT    NOT NULL DEFAULT (datetime('now')),
  agent           TEXT    NOT NULL,
  run_id          TEXT,
  provider        TEXT    NOT NULL,
  model           TEXT    NOT NULL,
  input_tokens    INTEGER NOT NULL DEFAULT 0,
  output_tokens   INTEGER NOT NULL DEFAULT 0,
  neurons         REAL    NOT NULL DEFAULT 0,   -- Workers AI only
  cost_usd        REAL    NOT NULL DEFAULT 0,
  latency_ms      INTEGER NOT NULL DEFAULT 0,
  ok              INTEGER NOT NULL DEFAULT 1,
  error           TEXT,
  gateway_log_id  TEXT                          -- AI Gateway log of this call
);
CREATE INDEX llm_calls_ts ON llm_calls (ts);
CREATE INDEX llm_calls_run ON llm_calls (run_id);

-- One row per agent run. Questions and answers are stored with PII redacted.
CREATE TABLE runs (
  run_id       TEXT PRIMARY KEY,
  ts           TEXT    NOT NULL DEFAULT (datetime('now')),
  agent        TEXT    NOT NULL,
  source       TEXT    NOT NULL,                -- chat | eval
  task_id      TEXT,
  model        TEXT    NOT NULL,
  question     TEXT    NOT NULL,
  answer       TEXT,
  status       TEXT    NOT NULL DEFAULT 'running',  -- success | failed | killed | error
  stop_reason  TEXT,
  steps        INTEGER NOT NULL DEFAULT 0,
  tool_calls   INTEGER NOT NULL DEFAULT 0,
  cost_usd     REAL    NOT NULL DEFAULT 0,
  version      TEXT
);
CREATE INDEX runs_ts ON runs (ts);

-- LLM turns and tool calls of a run (tool output kept for the judge).
CREATE TABLE steps (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT    NOT NULL,
  step        INTEGER NOT NULL,
  kind        TEXT    NOT NULL,                 -- llm | tool
  name        TEXT    NOT NULL,
  ok          INTEGER NOT NULL DEFAULT 1,
  latency_ms  INTEGER NOT NULL DEFAULT 0,
  detail      TEXT,
  output      TEXT
);
CREATE INDEX steps_run ON steps (run_id);

-- Every time a charter rule, a budget or a guardrail fires.
CREATE TABLE guardrail_events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT NOT NULL DEFAULT (datetime('now')),
  agent   TEXT NOT NULL,
  run_id  TEXT,
  stage   TEXT NOT NULL,                        -- charter | budget | input | output
  rule    TEXT NOT NULL,
  action  TEXT NOT NULL,                        -- blocked | flagged
  detail  TEXT
);
CREATE INDEX guardrail_events_ts ON guardrail_events (ts);

-- LLM-as-judge grades (1-5) and the optional human label.
CREATE TABLE judgements (
  run_id       TEXT PRIMARY KEY,
  ts           TEXT    NOT NULL DEFAULT (datetime('now')),
  judge_model  TEXT    NOT NULL,
  on_purpose   INTEGER NOT NULL,
  grounded     INTEGER NOT NULL,
  helpful      INTEGER NOT NULL,
  score        REAL    NOT NULL,                -- 0-1
  reasoning    TEXT,
  human_label  TEXT                             -- good | bad | NULL
);

-- Switches flipped from the admin dashboard (defaults: config/agents.json).
CREATE TABLE agent_switches (
  agent           TEXT PRIMARY KEY,
  status          TEXT NOT NULL,                -- active | paused
  guardrail_mode  TEXT NOT NULL,                -- monitor | enforce
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Release history, written by the deploy workflow.
CREATE TABLE deployments (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       TEXT NOT NULL DEFAULT (datetime('now')),
  version  TEXT NOT NULL,
  commit_sha TEXT NOT NULL
);
