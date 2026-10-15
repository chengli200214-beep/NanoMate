PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS project (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spec_revision (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_snapshot (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_conflict (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision_revision (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS report_revision (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS impact_event (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mute_rule (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_call (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notification (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_run (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, spec_id TEXT NOT NULL,
    kind TEXT NOT NULL, status TEXT NOT NULL, request_key TEXT NOT NULL,
    input_version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL, error TEXT NOT NULL DEFAULT '', missed_from REAL, missed_to REAL,
    UNIQUE(project_id,request_key)
);
CREATE TABLE IF NOT EXISTS research_step (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES research_run(id),
    position INTEGER NOT NULL, stage TEXT NOT NULL, source_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0, input_hash TEXT NOT NULL DEFAULT '',
    output TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
    UNIQUE(run_id,position)
);
CREATE INDEX IF NOT EXISTS step_claim ON research_step(status,retry_at,position);
CREATE INDEX IF NOT EXISTS runs_project ON research_run(project_id,created_at);
PRAGMA user_version=2;
