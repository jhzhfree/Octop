-- Existing checks predate resource version binding and must be re-run.
ALTER TABLE external_connection_checks ADD COLUMN resource_revision INTEGER NOT NULL DEFAULT 0;
UPDATE _schema_version SET version = 31;
