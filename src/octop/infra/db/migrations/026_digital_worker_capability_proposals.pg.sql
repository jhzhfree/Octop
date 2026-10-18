-- Schema v26: reviewed capability improvement proposals based on run evidence.
ALTER TABLE digital_workers ADD COLUMN IF NOT EXISTS capabilities_json TEXT NOT NULL DEFAULT '{"skills":[],"knowledge":[],"operating_guidance":[],"skill_package_ids":[],"knowledge_base_ids":[]}';
ALTER TABLE digital_workers ADD COLUMN IF NOT EXISTS capability_version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS digital_worker_capability_proposals (
  id BIGSERIAL PRIMARY KEY,
  proposal_id TEXT NOT NULL UNIQUE,
  worker_id TEXT NOT NULL REFERENCES digital_workers(worker_id) ON DELETE CASCADE,
  source_run_id TEXT NOT NULL REFERENCES enterprise_scene_runs(run_id) ON DELETE CASCADE,
  source_summary TEXT NOT NULL,
  observed_gaps TEXT NOT NULL,
  proposed_changes_json TEXT NOT NULL,
  expected_benefit TEXT NOT NULL,
  risk_notes TEXT NOT NULL DEFAULT '',
  base_capability_version INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','published','rejected')),
  created_by INTEGER NOT NULL REFERENCES users(id),
  reviewed_by INTEGER REFERENCES users(id),
  review_note TEXT NOT NULL DEFAULT '',
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_worker_capability_proposals_worker ON digital_worker_capability_proposals(worker_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_capability_proposals_source ON digital_worker_capability_proposals(source_run_id);
UPDATE _schema_version SET version = 26;
