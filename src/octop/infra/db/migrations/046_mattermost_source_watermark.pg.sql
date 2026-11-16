-- Distinguish last business transition from durably observed source high water.
ALTER TABLE mattermost_intakes ADD COLUMN observed_source_version BIGINT NOT NULL DEFAULT 1 CHECK(observed_source_version>=1);
UPDATE mattermost_intakes SET observed_source_version=source_version;
-- Old duplicate observations were not retained; their true maximum cannot be reconstructed.
-- No automatic re-enablement: retained identities require trusted reconciliation first.
UPDATE mattermost_brokers SET enabled=0 WHERE broker_id IN (SELECT broker_id FROM mattermost_intakes);
UPDATE _schema_version SET version=46;
