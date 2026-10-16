-- Schema v22: immutable scene-definition versions and run version references.
ALTER TABLE enterprise_scenes ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE enterprise_scene_runs ADD COLUMN IF NOT EXISTS scene_version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS enterprise_scene_versions (
  id BIGSERIAL PRIMARY KEY,
  scene_id TEXT NOT NULL REFERENCES enterprise_scenes(scene_id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  name TEXT NOT NULL,
  prompt TEXT NOT NULL,
  mcp_servers_json TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at BIGINT NOT NULL,
  UNIQUE(scene_id, version)
);

INSERT INTO enterprise_scene_versions(scene_id, version, name, prompt, mcp_servers_json, enabled, created_by, created_at)
SELECT s.scene_id, s.version, s.name, s.prompt, s.mcp_servers_json, s.enabled, s.owner_user_id, s.created_at
FROM enterprise_scenes s
WHERE NOT EXISTS (
  SELECT 1 FROM enterprise_scene_versions v
  WHERE v.scene_id = s.scene_id AND v.version = s.version
);

CREATE INDEX IF NOT EXISTS idx_enterprise_scene_versions_scene ON enterprise_scene_versions(scene_id, version);
UPDATE _schema_version SET version = 22;
