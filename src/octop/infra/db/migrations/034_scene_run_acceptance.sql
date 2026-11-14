-- Human acceptance is independent from execution status. No response body is stored.
CREATE TABLE IF NOT EXISTS scene_run_acceptances (
 run_id TEXT PRIMARY KEY REFERENCES enterprise_scene_runs(run_id) ON DELETE CASCADE,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
 decision TEXT NOT NULL CHECK(decision IN ('accepted','changes_requested')),
 reviewer_id INTEGER NOT NULL REFERENCES users(id),
 reviewed_at BIGINT NOT NULL,
 evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
 evidence_length INTEGER NOT NULL CHECK(evidence_length>0),
 snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256)=64),
 scene_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scene_acceptance_org ON scene_run_acceptances(organization_id);
UPDATE _schema_version SET version=34;
