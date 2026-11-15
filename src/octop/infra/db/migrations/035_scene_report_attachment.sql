-- Explicit owner sharing of an immutable report snapshot, independent of outbound approval.
ALTER TABLE enterprise_scene_runs ADD COLUMN report_attachment_sha256 TEXT NOT NULL DEFAULT '';
CREATE TABLE scene_report_attachments (
 run_id TEXT PRIMARY KEY REFERENCES enterprise_scene_runs(run_id) ON DELETE CASCADE,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
 report_run_id TEXT NOT NULL,
 report_version INTEGER NOT NULL,
 report_content_hash TEXT NOT NULL,
 attachment_sha256 TEXT NOT NULL,
 attached_by INTEGER NOT NULL REFERENCES users(id),
 attached_at BIGINT NOT NULL,
 content TEXT NOT NULL
);
UPDATE _schema_version SET version=35;
