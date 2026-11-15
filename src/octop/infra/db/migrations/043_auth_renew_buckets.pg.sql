CREATE TABLE auth_renew_buckets (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    window_start BIGINT NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts >= 1 AND attempts <= 10)
);
UPDATE _schema_version SET version=43;
