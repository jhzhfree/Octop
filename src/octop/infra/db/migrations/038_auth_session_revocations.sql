-- Tombstones are deliberately retained: sliding sessions have no fixed lifetime.
CREATE TABLE auth_session_revocations (
    session_digest TEXT PRIMARY KEY,
    revoked_at BIGINT NOT NULL
);
UPDATE _schema_version SET version=38;
