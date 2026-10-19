-- Schema v27: approval-gated platform memory candidates from successful worker runs.
CREATE TABLE IF NOT EXISTS digital_worker_memory_candidates (
  id BIGSERIAL PRIMARY KEY,
  candidate_id TEXT NOT NULL UNIQUE,
  organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
  worker_id TEXT NOT NULL REFERENCES digital_workers(worker_id) ON DELETE CASCADE,
  source_run_id TEXT NOT NULL REFERENCES enterprise_scene_runs(run_id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  mcp_server TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_code TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','writing','written','rejected','failed')),
  memory_code TEXT,
  created_by INTEGER NOT NULL REFERENCES users(id),
  reviewed_by INTEGER REFERENCES users(id),
  review_note TEXT NOT NULL DEFAULT '',
  error_summary TEXT,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  UNIQUE(source_run_id, provider, mcp_server, scope_type, scope_code)
);
CREATE INDEX IF NOT EXISTS idx_worker_memory_candidates_org
  ON digital_worker_memory_candidates(organization_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_worker_memory_candidates_worker
  ON digital_worker_memory_candidates(worker_id, created_at);
UPDATE _schema_version SET version = 27;
