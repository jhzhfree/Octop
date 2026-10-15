-- Schema v22: immutable scene-definition versions and run version references.
ALTER TABLE enterprise_scenes ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE enterprise_scene_runs ADD COLUMN scene_version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE enterprise_scene_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scene_id TEXT NOT NULL REFERENCES enterprise_scenes(scene_id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  name TEXT NOT NULL,
  prompt TEXT NOT NULL,
  mcp_servers_json TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at INTEGER NOT NULL,
  UNIQUE(scene_id, version)
);

INSERT INTO enterprise_scene_versions(scene_id, version, name, prompt, mcp_servers_json, enabled, created_by, created_at)
SELECT scene_id, version, name, prompt, mcp_servers_json, enabled, owner_user_id, created_at
FROM enterprise_scenes;

CREATE INDEX idx_enterprise_scene_versions_scene ON enterprise_scene_versions(scene_id, version);
UPDATE _schema_version SET version = 22;
