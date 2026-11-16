-- Quiesce brokers affected by the unsafe v47 retirement/resume path. Older
-- receivers already enforce enabled=0; a revision alone cannot fence them.
-- Retain existing fences/data for explicit investigation; never retire more.
INSERT INTO audit_log(ts,actor,action,target,payload)
 SELECT 0,'schema-migration','mattermost.maintenance.resume_blocked',broker_id,
 '{"schema":48,"reason":"process_isolation_unproven","timestamp_unknown":true}'
 FROM mattermost_brokers b WHERE b.enabled=1 AND (
 EXISTS(SELECT 1 FROM mattermost_intakes i JOIN mattermost_retired_intakes r
 ON r.delivery_key=i.delivery_key WHERE i.broker_id=b.broker_id)
 OR EXISTS(SELECT 1 FROM audit_log a WHERE a.target=b.broker_id
 AND a.action='mattermost.maintenance.retire_and_resume'));
UPDATE mattermost_brokers SET enabled=0,revision=revision+1 WHERE enabled=1 AND (
 EXISTS(SELECT 1 FROM mattermost_intakes i JOIN mattermost_retired_intakes r
 ON r.delivery_key=i.delivery_key WHERE i.broker_id=mattermost_brokers.broker_id)
 OR EXISTS(SELECT 1 FROM audit_log a WHERE a.target=mattermost_brokers.broker_id
 AND a.action='mattermost.maintenance.retire_and_resume'));
UPDATE _schema_version SET version=48;
