-- Explicit service connections, resources, identity bindings and check history.
CREATE TABLE IF NOT EXISTS external_knowledge_connections (
  connection_id TEXT PRIMARY KEY,
  owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  display_name TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  instance_id TEXT NOT NULL DEFAULT '',
  auth_type TEXT NOT NULL DEFAULT 'legacy' CHECK(auth_type IN ('legacy','service','agent')),
  allow_insecure_http INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  shared INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 1,
  credential_blob BYTEA,
  identity_json TEXT NOT NULL DEFAULT '{}',
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
);
ALTER TABLE external_knowledge_sources ADD COLUMN resource_revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE external_knowledge_sources ADD COLUMN connection_id TEXT REFERENCES external_knowledge_connections(connection_id);
INSERT INTO external_knowledge_connections(connection_id,owner_user_id,kind,display_name,endpoint,instance_id,allow_insecure_http,enabled,shared,credential_blob,created_at,updated_at)
SELECT source_id,owner_user_id,kind,display_name,endpoint,instance_id,allow_insecure_http,enabled,shared,credential_blob,created_at,updated_at FROM external_knowledge_sources WHERE source_id NOT IN (SELECT connection_id FROM external_knowledge_connections);
UPDATE external_knowledge_sources SET connection_id=source_id WHERE connection_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_external_resources_connection ON external_knowledge_sources(connection_id);
CREATE TABLE IF NOT EXISTS external_agent_bindings (
  connection_id TEXT NOT NULL REFERENCES external_knowledge_connections(connection_id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL,
  binding_json TEXT NOT NULL,
  key_ref TEXT REFERENCES secrets(k),
  last_verified_at TEXT,
  last_verification_status TEXT,
  PRIMARY KEY(connection_id,agent_id)
);
CREATE INDEX IF NOT EXISTS idx_external_agent_key_ref ON external_agent_bindings(key_ref);
CREATE TABLE IF NOT EXISTS external_connection_checks (
  connection_id TEXT NOT NULL REFERENCES external_knowledge_connections(connection_id) ON DELETE CASCADE,
  resource_id TEXT NOT NULL DEFAULT '',
  agent_id TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  code TEXT NOT NULL DEFAULT '',
  checked_at BIGINT NOT NULL,
  PRIMARY KEY(connection_id,resource_id,agent_id,stage)
);
UPDATE _schema_version SET version = 30;
