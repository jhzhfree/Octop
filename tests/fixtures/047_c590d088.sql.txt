-- Permanent identity fences: keep unknown legacy source histories unusable.
CREATE TABLE mattermost_retired_intakes (
 delivery_key TEXT PRIMARY KEY REFERENCES mattermost_intakes(delivery_key),
 reason TEXT NOT NULL,
 retired_at BIGINT NOT NULL
);
INSERT INTO mattermost_retired_intakes(delivery_key,reason,retired_at)
 SELECT i.delivery_key,'schema047_unknown_legacy_history',0
 FROM mattermost_intakes i JOIN mattermost_brokers b ON b.broker_id=i.broker_id
 WHERE b.enabled=0;
UPDATE _schema_version SET version=47;
