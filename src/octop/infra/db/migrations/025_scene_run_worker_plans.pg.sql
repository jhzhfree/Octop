-- Schema v25: bind virtual worker plan snapshots to approval-gated scene runs.
ALTER TABLE enterprise_scene_runs
  ADD COLUMN IF NOT EXISTS plan_id TEXT REFERENCES digital_worker_plans(plan_id) ON DELETE SET NULL;
ALTER TABLE enterprise_scene_runs ADD COLUMN IF NOT EXISTS plan_snapshot_json TEXT;
CREATE INDEX IF NOT EXISTS idx_enterprise_scene_runs_plan ON enterprise_scene_runs(plan_id, created_at);
UPDATE _schema_version SET version = 25;
