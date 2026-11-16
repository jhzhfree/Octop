-- Offline broker admission storage. No production registration or transport is installed.
CREATE TABLE mattermost_brokers (
 broker_id TEXT PRIMARY KEY,
 broker_public_key TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 revision BIGINT NOT NULL CHECK(revision>=1),
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 worker_id TEXT NOT NULL REFERENCES digital_workers(worker_id),
 actor_id BIGINT NOT NULL REFERENCES users(id),
 worker_version BIGINT NOT NULL CHECK(worker_version>=1),
 proof_key_id TEXT NOT NULL,
 proof_public_key TEXT NOT NULL,
 binding_json TEXT NOT NULL
);
CREATE TABLE mattermost_broker_nonces (
 broker_id TEXT NOT NULL REFERENCES mattermost_brokers(broker_id),
 nonce TEXT NOT NULL,
 created_at BIGINT NOT NULL,
 PRIMARY KEY(broker_id,nonce)
);
CREATE TABLE mattermost_intakes (
 delivery_key TEXT PRIMARY KEY,
 fingerprint TEXT NOT NULL,
 broker_id TEXT NOT NULL REFERENCES mattermost_brokers(broker_id),
 organization_id TEXT NOT NULL,
 worker_id TEXT NOT NULL,
 actor_id BIGINT NOT NULL,
 binding_json TEXT NOT NULL,
 receipt TEXT NOT NULL,
 commitment TEXT NOT NULL,
 source_expires_at BIGINT NOT NULL,
 source_version BIGINT NOT NULL CHECK(source_version>=1),
 state TEXT NOT NULL CHECK(state IN ('pending_authorization','cancelled','expired')),
 revision BIGINT NOT NULL DEFAULT 1 CHECK(revision BETWEEN 1 AND 8),
 created_at BIGINT NOT NULL,
 updated_at BIGINT NOT NULL
);
CREATE INDEX idx_mattermost_intakes_broker ON mattermost_intakes(broker_id);
UPDATE _schema_version SET version=45;
