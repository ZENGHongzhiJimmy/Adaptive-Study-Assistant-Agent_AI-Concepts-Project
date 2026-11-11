-- P2: student knowledge model + append-only event log.
-- The events table is created HERE (not in P5) so Agent Trace and experiment
-- metrics never need retroactive instrumentation. P6 adds latency/token/cost
-- values to the same columns.

CREATE TABLE IF NOT EXISTS student_topic_state (
    student_id       TEXT    NOT NULL,
    topic            TEXT    NOT NULL,
    mastery          REAL    NOT NULL DEFAULT 0.0 CHECK (mastery >= 0.0 AND mastery <= 1.0),
    attempts         INTEGER NOT NULL DEFAULT 0,
    correct_count    INTEGER NOT NULL DEFAULT 0,
    incorrect_count  INTEGER NOT NULL DEFAULT 0,
    recent_errors    TEXT    NOT NULL DEFAULT '[]',   -- JSON list, bounded window
    last_studied     TEXT,
    PRIMARY KEY (student_id, topic)
);

CREATE TABLE IF NOT EXISTS events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key          TEXT UNIQUE,       -- idempotency key for repeated answers / replays
    ts                 TEXT    NOT NULL,
    run_id             TEXT,
    student_id         TEXT    NOT NULL,
    topic              TEXT    NOT NULL,
    step               INTEGER,
    action             TEXT,              -- Teach / MCQ / Challenge / Review
    action_reason      TEXT,              -- why the planner chose it
    mastery_before     REAL,
    mastery_after      REAL,
    performance        REAL,              -- [0,1] input to the mastery formula
    evaluation_result  TEXT,              -- correct / incorrect / score
    question_id        TEXT,
    is_error           INTEGER NOT NULL DEFAULT 0,
    recent_errors      TEXT,              -- JSON snapshot after the update
    lecture            TEXT,
    page               INTEGER,
    citation           TEXT,              -- Source: Lecture 05, Page 14
    latency_ms         REAL,
    prompt_tokens      INTEGER,
    completion_tokens  INTEGER,
    total_tokens       INTEGER,
    cost_usd           REAL,
    model              TEXT,
    platform_version   TEXT,
    mastery_alpha      REAL,
    seed               INTEGER,
    config_snapshot    TEXT               -- JSON of config_for_log()
);

CREATE INDEX IF NOT EXISTS idx_events_student_topic
    ON events (student_id, topic, id);
CREATE INDEX IF NOT EXISTS idx_events_run
    ON events (run_id, step);
