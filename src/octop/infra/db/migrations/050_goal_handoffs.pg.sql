CREATE TABLE goal_handoffs (
    handoff_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    source_goal_id TEXT NOT NULL,
    source_worker_id TEXT NOT NULL,
    target_goal_id TEXT NOT NULL UNIQUE,
    target_worker_id TEXT NOT NULL,
    created_by BIGINT NOT NULL,
    request_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    reason TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL,
    target_snapshot_json TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    UNIQUE(organization_id, created_by, request_id)
);
CREATE INDEX idx_goal_handoffs_source ON goal_handoffs(organization_id, source_goal_id, handoff_id);
CREATE INDEX idx_goal_handoffs_target ON goal_handoffs(organization_id, target_goal_id, handoff_id);
CREATE INDEX idx_goal_handoffs_history ON goal_handoffs(organization_id, handoff_id);
UPDATE _schema_version SET version=50;
