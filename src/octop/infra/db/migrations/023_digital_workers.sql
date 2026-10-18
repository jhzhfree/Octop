-- Schema v23: organization-bound virtual worker profiles and stage goals.
CREATE TABLE IF NOT EXISTS digital_workers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worker_id TEXT NOT NULL UNIQUE,
  organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
  position_id TEXT NOT NULL REFERENCES organization_positions(position_id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
  owner_user_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  responsibilities TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','paused','archived')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(position_id, agent_id)
);

CREATE TABLE IF NOT EXISTS digital_worker_goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  goal_id TEXT NOT NULL UNIQUE,
  worker_id TEXT NOT NULL REFERENCES digital_workers(worker_id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  success_criteria TEXT NOT NULL,
  period_start TEXT,
  period_end TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','completed','cancelled')),
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_digital_workers_org ON digital_workers(organization_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_digital_worker_goals_worker ON digital_worker_goals(worker_id, period_start, period_end);
UPDATE _schema_version SET version = 23;
