-- Deployment-managed aliases. No credentials, URLs from end users, or verification grants.
-- Keep revoked rows: deleting/recreating would reset the CAS revision (ABA).
CREATE TABLE mattermost_instances (
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    instance_key TEXT NOT NULL,
    origin TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    credential_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('configured','revoked')),
    revision BIGINT NOT NULL CHECK(revision > 0),
    updated_by INTEGER NOT NULL,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(organization_id, instance_key)
);
UPDATE _schema_version SET version=40;
