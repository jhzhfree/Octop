-- Existing installations are closed conservatively, including CLI-created admins.
CREATE TABLE setup_lifecycle (
 id INTEGER PRIMARY KEY CHECK (id = 1),
 phase TEXT NOT NULL,
 admin_id BIGINT,
    token_digest TEXT
);
INSERT INTO setup_lifecycle(id, phase)
 SELECT 1, CASE WHEN EXISTS (SELECT 1 FROM users) THEN 'completed' ELSE 'pending' END;
UPDATE _schema_version SET version=37;
