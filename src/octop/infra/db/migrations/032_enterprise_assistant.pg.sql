-- Enterprise workflows are opt-in. Secrets never enter run snapshots.
CREATE TABLE IF NOT EXISTS enterprise_assistant_resources (
    id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    config_json TEXT NOT NULL,
    secret_blob TEXT NOT NULL DEFAULT '',
    next_due INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ea_resources_owner ON enterprise_assistant_resources(owner_id, category);
CREATE TABLE IF NOT EXISTS enterprise_assistant_runs (
    seq BIGSERIAL PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES enterprise_assistant_resources(id) ON DELETE CASCADE,
    trigger_key TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    snapshot_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    content TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(job_id, trigger_key)
);
CREATE INDEX IF NOT EXISTS idx_ea_runs_owner ON enterprise_assistant_runs(owner_id, created_at);
-- One active run per job, including pending human decisions and uncertain writes.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ea_runs_active ON enterprise_assistant_runs(job_id)
    WHERE status IN ('queued', 'running', 'awaiting_approval', 'approved', 'executing', 'unknown');
UPDATE _schema_version SET version = 32;
