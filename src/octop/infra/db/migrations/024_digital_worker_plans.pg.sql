-- Schema v24: draftable, reviewable plans attached to virtual worker goals.
CREATE TABLE IF NOT EXISTS digital_worker_plans (
  id BIGSERIAL PRIMARY KEY,
  plan_id TEXT NOT NULL UNIQUE,
  goal_id TEXT NOT NULL REFERENCES digital_worker_goals(goal_id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  approach TEXT NOT NULL DEFAULT '',
  steps_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','ready','cancelled')),
  version INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_digital_worker_plans_goal ON digital_worker_plans(goal_id, created_at);
UPDATE _schema_version SET version = 24;
