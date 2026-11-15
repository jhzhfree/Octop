ALTER TABLE digital_worker_goals ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1);
UPDATE _schema_version SET version=42;
