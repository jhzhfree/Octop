-- Schema v21: organizations, departments, positions, members, and appointments.
CREATE TABLE organizations (
  id BIGSERIAL PRIMARY KEY,
  organization_id TEXT NOT NULL UNIQUE,
  owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE organization_members (
  id BIGSERIAL PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'manager', 'member')),
  created_at INTEGER NOT NULL,
  UNIQUE(organization_id, user_id)
);

CREATE TABLE organization_departments (
  id BIGSERIAL PRIMARY KEY,
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

CREATE TABLE organization_positions (
  id BIGSERIAL PRIMARY KEY,
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

CREATE TABLE organization_assignments (
  id BIGSERIAL PRIMARY KEY,
  assignment_id TEXT NOT NULL UNIQUE,
  position_id TEXT NOT NULL REFERENCES organization_positions(position_id) ON DELETE CASCADE,
  subject_type TEXT NOT NULL CHECK (subject_type IN ('user', 'agent')),
  subject_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(position_id, subject_type, subject_id)
);

CREATE INDEX idx_organizations_owner ON organizations(owner_user_id);
CREATE INDEX idx_organization_members_user ON organization_members(user_id);
CREATE INDEX idx_org_departments_org_parent ON organization_departments(organization_id, parent_department_id);
CREATE INDEX idx_org_positions_org_department ON organization_positions(organization_id, department_id);
CREATE INDEX idx_org_positions_reports_to ON organization_positions(reports_to_position_id);
CREATE INDEX idx_org_assignments_position ON organization_assignments(position_id);

UPDATE _schema_version SET version = 21;
