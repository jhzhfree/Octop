-- One fail-closed, locally delivered inspection batch per approved scene run.
CREATE TABLE scene_inspection_batches (
 run_id TEXT PRIMARY KEY REFERENCES enterprise_scene_runs(run_id) ON DELETE CASCADE,
 status TEXT NOT NULL,
 sources_json TEXT NOT NULL,
 content TEXT NOT NULL DEFAULT '',
 content_sha256 TEXT NOT NULL DEFAULT '',
 counts_json TEXT NOT NULL DEFAULT '{}',
 created_at BIGINT NOT NULL,
 updated_at BIGINT NOT NULL
);
CREATE TABLE scene_inspection_sources (
 source_run_id TEXT PRIMARY KEY REFERENCES enterprise_assistant_runs(id),
 scene_run_id TEXT NOT NULL REFERENCES scene_inspection_batches(run_id) ON DELETE CASCADE
);
ALTER TABLE enterprise_scene_runs ADD COLUMN inspection_report_sha256 TEXT NOT NULL DEFAULT '';
UPDATE _schema_version SET version=36;
