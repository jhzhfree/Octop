-- Schema v21: organizations, departments, positions, members, and appointments.
-- SQLite adds cron_jobs.mcp_servers_exact through the idempotent migration helper.
CREATE TABLE IF NOT EXISTS organizations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  organization_id TEXT NOT NULL UNIQUE,
  owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS organization_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'manager', 'member')),
  created_at INTEGER NOT NULL,
  UNIQUE(organization_id, user_id)
);

CREATE TABLE IF NOT EXISTS organization_departments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  department_id TEXT NOT NULL UNIQUE,
  organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
  parent_department_id TEXT REFERENCES organization_departments(department_id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  code TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS organization_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  position_id TEXT NOT NULL UNIQUE,
  organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
  department_id TEXT REFERENCES organization_departments(department_id) ON DELETE SET NULL,
  reports_to_position_id TEXT REFERENCES organization_positions(position_id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  code TEXT NOT NULL DEFAULT '',
  duties TEXT NOT NULL DEFAULT '',
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS organization_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  assignment_id TEXT NOT NULL UNIQUE,
  position_id TEXT NOT NULL REFERENCES organization_positions(position_id) ON DELETE CASCADE,
  subject_type TEXT NOT NULL CHECK (subject_type IN ('user', 'agent')),
  subject_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(position_id, subject_type, subject_id)
);

CREATE INDEX IF NOT EXISTS idx_organizations_owner ON organizations(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_organization_members_user ON organization_members(user_id);
CREATE INDEX IF NOT EXISTS idx_org_departments_org_parent ON organization_departments(organization_id, parent_department_id);
CREATE INDEX IF NOT EXISTS idx_org_positions_org_department ON organization_positions(organization_id, department_id);
CREATE INDEX IF NOT EXISTS idx_org_positions_reports_to ON organization_positions(reports_to_position_id);
CREATE INDEX IF NOT EXISTS idx_org_assignments_position ON organization_assignments(position_id);

-- Scenario definitions and immutable run snapshots share the unreleased v21 migration.
CREATE TABLE IF NOT EXISTS enterprise_scenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scene_id TEXT NOT NULL UNIQUE,
  organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
  position_id TEXT NOT NULL REFERENCES organization_positions(position_id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
  owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  prompt TEXT NOT NULL,
  mcp_servers_json TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS enterprise_scene_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  scene_id TEXT NOT NULL REFERENCES enterprise_scenes(scene_id) ON DELETE CASCADE,
  organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
  position_id TEXT NOT NULL REFERENCES organization_positions(position_id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
  scene_name TEXT NOT NULL,
  prompt_snapshot TEXT NOT NULL,
  mcp_servers_json TEXT NOT NULL DEFAULT '[]',
  requested_by INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL CHECK (status IN ('awaiting_approval','rejected','queued','succeeded','failed')),
  approved_by INTEGER REFERENCES users(id),
  decision_note TEXT NOT NULL DEFAULT '',
  cron_id TEXT,
  error_summary TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_enterprise_scenes_org ON enterprise_scenes(organization_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_enterprise_scene_runs_org ON enterprise_scene_runs(organization_id, created_at);
CREATE INDEX IF NOT EXISTS idx_enterprise_scene_runs_scene ON enterprise_scene_runs(scene_id, created_at);

UPDATE _schema_version SET version = 21;
