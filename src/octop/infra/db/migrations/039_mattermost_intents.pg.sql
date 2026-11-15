-- Intent only: these rows never authorize network access or message delivery.
CREATE TABLE worker_mattermost_intents (
    worker_id TEXT PRIMARY KEY REFERENCES digital_workers(worker_id) ON DELETE CASCADE,
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    instance_key TEXT NOT NULL,
    team_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','revoked')),
    revision INTEGER NOT NULL CHECK(revision > 0),
    worker_version INTEGER NOT NULL,
    updated_by INTEGER NOT NULL,
    updated_at BIGINT NOT NULL
);
UPDATE _schema_version SET version=39;
