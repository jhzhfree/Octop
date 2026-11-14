-- Schema v33: explicit draft knowledge mappings; no read authorization.
-- Parent deletions cascade; revoked rows are not a permanent audit archive.
CREATE TABLE IF NOT EXISTS worker_knowledge_source_mappings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 mapping_id TEXT NOT NULL UNIQUE,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
 worker_id TEXT NOT NULL REFERENCES digital_workers(worker_id) ON DELETE CASCADE,
 capability_key TEXT NOT NULL,
 capability_json TEXT NOT NULL,
 capability_version INTEGER NOT NULL,
 worker_version INTEGER NOT NULL,
 external_source_id TEXT NOT NULL REFERENCES external_knowledge_sources(source_id) ON DELETE CASCADE,
 expected_connection_id TEXT NOT NULL REFERENCES external_knowledge_connections(connection_id) ON DELETE CASCADE,
 connection_revision INTEGER NOT NULL,
 resource_revision INTEGER NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','revoked')),
 created_by INTEGER NOT NULL REFERENCES users(id),
 updated_by INTEGER NOT NULL REFERENCES users(id),
 created_at BIGINT NOT NULL,
 updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worker_knowledge_mapping_org ON worker_knowledge_source_mappings(organization_id,worker_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_knowledge_mapping_draft ON worker_knowledge_source_mappings(worker_id,capability_key) WHERE status='draft';
UPDATE _schema_version SET version = 33;
