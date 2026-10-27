-- Schema v28: external knowledge sources (third-party knowledge bases).
CREATE TABLE IF NOT EXISTS external_knowledge_sources (
  id BIGSERIAL PRIMARY KEY,
  source_id TEXT NOT NULL UNIQUE,
  owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  instance_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  display_name TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '',
  top_k INTEGER NOT NULL DEFAULT 8,
  min_score REAL NOT NULL DEFAULT 0,
  timeout_ms BIGINT NOT NULL DEFAULT 10000,
  allow_insecure_http INTEGER NOT NULL DEFAULT 0,
  selector_argument TEXT NOT NULL DEFAULT '',
  forbidden_arguments TEXT NOT NULL DEFAULT '',
  read_tools TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  shared INTEGER NOT NULL DEFAULT 0,
  credential_blob BYTEA,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_external_knowledge_sources_owner
  ON external_knowledge_sources(owner_user_id, kind);

CREATE TABLE IF NOT EXISTS external_knowledge_bindings (
  source_id TEXT NOT NULL REFERENCES external_knowledge_sources(source_id) ON DELETE CASCADE,
  target_type TEXT NOT NULL CHECK (target_type IN ('agent','knowledge_base')),
  target_id TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at BIGINT NOT NULL,
  PRIMARY KEY (source_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS idx_external_knowledge_bindings_target
  ON external_knowledge_bindings(target_type, target_id);
UPDATE _schema_version SET version = 28;
