-- Schema of the LLMOps store. Applied by the gateway on every start
-- (idempotent). Grafana reads these tables directly.

-- One row per LLM call proxied by the gateway (FinOps source of truth).
CREATE TABLE IF NOT EXISTS llm_requests (
    id                 BIGSERIAL PRIMARY KEY,
    ts                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    provider           TEXT        NOT NULL,
    model              TEXT        NOT NULL,
    project            TEXT        NOT NULL,
    agent              TEXT,
    run_id             TEXT,
    endpoint           TEXT        NOT NULL,
    streamed           BOOLEAN     NOT NULL DEFAULT false,
    input_tokens       INTEGER     NOT NULL DEFAULT 0,
    output_tokens      INTEGER     NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER     NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER     NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(14, 8) NOT NULL DEFAULT 0,
    cache_savings_usd  NUMERIC(14, 8) NOT NULL DEFAULT 0,
    priced             BOOLEAN     NOT NULL DEFAULT true,
    latency_ms         INTEGER     NOT NULL DEFAULT 0,
    status_code        INTEGER     NOT NULL,
    error              TEXT,
    trace_id           TEXT,
    release            TEXT,
    synthetic          BOOLEAN     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS llm_requests_ts_idx      ON llm_requests (ts);
CREATE INDEX IF NOT EXISTS llm_requests_project_idx ON llm_requests (project, ts);
CREATE INDEX IF NOT EXISTS llm_requests_run_idx     ON llm_requests (run_id);

-- One row per agent run (AgentOps).
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id         TEXT PRIMARY KEY,
    agent          TEXT        NOT NULL,
    project        TEXT        NOT NULL,
    task_id        TEXT,
    model          TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    -- running | success | failed | killed (guardrail stopped it) | error
    status         TEXT        NOT NULL DEFAULT 'running',
    stop_reason    TEXT,
    steps          INTEGER     NOT NULL DEFAULT 0,
    tool_calls     INTEGER     NOT NULL DEFAULT 0,
    tool_errors    INTEGER     NOT NULL DEFAULT 0,
    loop_detected  BOOLEAN     NOT NULL DEFAULT false,
    score          NUMERIC(5, 4),
    cost_usd       NUMERIC(14, 8) NOT NULL DEFAULT 0,
    total_tokens   INTEGER     NOT NULL DEFAULT 0,
    trace_id       TEXT,
    release        TEXT,
    synthetic      BOOLEAN     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS agent_runs_started_idx ON agent_runs (started_at);

-- One row per agent step: an LLM turn or a tool call.
CREATE TABLE IF NOT EXISTS agent_steps (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT        NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    step        INTEGER     NOT NULL,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT        NOT NULL,          -- llm | tool
    name        TEXT        NOT NULL,          -- model id or tool name
    ok          BOOLEAN     NOT NULL DEFAULT true,
    latency_ms  INTEGER     NOT NULL DEFAULT 0,
    detail      TEXT,
    synthetic   BOOLEAN     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS agent_steps_run_idx ON agent_steps (run_id);
CREATE INDEX IF NOT EXISTS agent_steps_ts_idx  ON agent_steps (ts);

-- Release history: the gateway records itself here when a new
-- version/commit starts (DevOps "last release deployed").
CREATE TABLE IF NOT EXISTS deployments (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    service     TEXT        NOT NULL,
    version     TEXT        NOT NULL,
    git_commit  TEXT        NOT NULL,
    build_date  TEXT,
    environment TEXT        NOT NULL,
    notes       TEXT,
    synthetic   BOOLEAN     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS deployments_ts_idx ON deployments (ts);

-- Mirror of config/budgets.yaml, refreshed on start so Grafana can join it.
CREATE TABLE IF NOT EXISTS budgets (
    project     TEXT PRIMARY KEY,
    monthly_usd NUMERIC(12, 2) NOT NULL,
    alert_at    NUMERIC(4, 3)  NOT NULL
);

-- Eval suite results (one row per evaluated task run), written by the
-- agent's eval runner; drives the quality-vs-cost panels.
CREATE TABLE IF NOT EXISTS eval_results (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    suite_id    TEXT        NOT NULL,
    task_id     TEXT        NOT NULL,
    run_id      TEXT,
    model       TEXT,
    passed      BOOLEAN     NOT NULL,
    score       NUMERIC(5, 4) NOT NULL,
    cost_usd    NUMERIC(14, 8) NOT NULL DEFAULT 0,
    release     TEXT,
    synthetic   BOOLEAN     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS eval_results_ts_idx ON eval_results (ts);

-- ---------------------------------------------------------------------------
-- Governance (config/agents.yaml + console)
-- ---------------------------------------------------------------------------

-- Registered agents. The charter columns are refreshed from agents.yaml on
-- start; status and guardrail_mode are the console's switches and are only
-- set from the YAML the first time an agent appears.
CREATE TABLE IF NOT EXISTS agents (
    name               TEXT PRIMARY KEY,
    purpose            TEXT        NOT NULL,
    owner              TEXT,
    models             TEXT[]      NOT NULL,
    tools              TEXT[]      NOT NULL,
    judge_sample_rate  NUMERIC(4, 3) NOT NULL DEFAULT 0,
    status             TEXT        NOT NULL DEFAULT 'active',   -- active | paused
    guardrail_mode     TEXT        NOT NULL DEFAULT 'monitor',  -- monitor | enforce
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         TEXT
);

-- What the judge and the console need to show a run.
ALTER TABLE agent_runs  ADD COLUMN IF NOT EXISTS question TEXT;
ALTER TABLE agent_runs  ADD COLUMN IF NOT EXISTS answer   TEXT;
ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS output   TEXT;

-- Every time a charter rule or a guardrail fires.
CREATE TABLE IF NOT EXISTS guardrail_events (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent      TEXT        NOT NULL,
    project    TEXT        NOT NULL,
    run_id     TEXT,
    stage      TEXT        NOT NULL,   -- charter | input | output
    rule       TEXT        NOT NULL,   -- e.g. model_not_allowed, pii_email, prompt_injection
    action     TEXT        NOT NULL,   -- blocked | flagged
    detail     TEXT,
    synthetic  BOOLEAN     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS guardrail_events_ts_idx  ON guardrail_events (ts);
CREATE INDEX IF NOT EXISTS guardrail_events_run_idx ON guardrail_events (run_id);

-- LLM-as-judge grades (1-5 per criterion) and the optional human label
-- that measures how much the judge can be trusted.
CREATE TABLE IF NOT EXISTS judgements (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_id        TEXT        NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    agent         TEXT        NOT NULL,
    judge_model   TEXT        NOT NULL,
    on_purpose    SMALLINT    NOT NULL,
    grounded      SMALLINT    NOT NULL,
    helpful       SMALLINT    NOT NULL,
    score         NUMERIC(5, 4) NOT NULL,  -- mean of the three, scaled to 0-1
    reasoning     TEXT,
    human_label   TEXT,                    -- good | bad | NULL (not reviewed)
    labeled_at    TIMESTAMPTZ,
    synthetic     BOOLEAN     NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX IF NOT EXISTS judgements_run_idx ON judgements (run_id);
CREATE INDEX IF NOT EXISTS judgements_ts_idx ON judgements (ts);
