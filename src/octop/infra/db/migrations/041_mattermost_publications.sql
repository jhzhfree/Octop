-- Local approval ledger only. There is deliberately no dispatcher or sent state.
CREATE TABLE scene_mattermost_publications (
 run_id TEXT PRIMARY KEY REFERENCES enterprise_scene_runs(run_id) ON DELETE CASCADE,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
 snapshot_json TEXT NOT NULL,
 publication_sha256 TEXT NOT NULL CHECK(length(publication_sha256)=64),
 proposed_by INTEGER NOT NULL,
 approved_by INTEGER,
 status TEXT NOT NULL CHECK(status IN ('pending_approval','blocked_not_connected','revoked','unknown')),
 created_at BIGINT NOT NULL,
 updated_at BIGINT NOT NULL
);
CREATE INDEX idx_mm_publication_org ON scene_mattermost_publications(organization_id);
UPDATE _schema_version SET version=41;
