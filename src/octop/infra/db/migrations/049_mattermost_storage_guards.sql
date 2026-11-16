-- Close the old-startup clamp -> old-maintenance resume path inside SQLite.
-- These guards constrain tested SQL paths, not a database owner who can drop them.
INSERT INTO audit_log(ts,actor,action,target,payload)
 SELECT 0,'schema-migration','mattermost.maintenance.storage_guard',broker_id,
 '{"schema":49,"timestamp_unknown":true}' FROM mattermost_brokers b
 WHERE b.enabled=1 AND (
 EXISTS(SELECT 1 FROM mattermost_intakes i JOIN mattermost_retired_intakes r
 ON r.delivery_key=i.delivery_key WHERE i.broker_id=b.broker_id)
 OR EXISTS(SELECT 1 FROM audit_log a WHERE a.target=b.broker_id
 AND a.action='mattermost.maintenance.retire_and_resume'));
UPDATE mattermost_brokers SET enabled=0,revision=revision+1 WHERE enabled=1 AND (
 EXISTS(SELECT 1 FROM mattermost_intakes i JOIN mattermost_retired_intakes r
 ON r.delivery_key=i.delivery_key WHERE i.broker_id=mattermost_brokers.broker_id)
 OR EXISTS(SELECT 1 FROM audit_log a WHERE a.target=mattermost_brokers.broker_id
 AND a.action='mattermost.maintenance.retire_and_resume'));
CREATE TRIGGER mattermost_retired_broker_no_enable BEFORE UPDATE ON mattermost_brokers
 WHEN NEW.enabled=1 AND (
 EXISTS(SELECT 1 FROM mattermost_intakes i JOIN mattermost_retired_intakes r
 ON r.delivery_key=i.delivery_key WHERE i.broker_id=OLD.broker_id OR i.broker_id=NEW.broker_id)
 OR EXISTS(SELECT 1 FROM audit_log a WHERE a.target IN (OLD.broker_id,NEW.broker_id)
 AND a.action='mattermost.maintenance.retire_and_resume'))
 BEGIN SELECT RAISE(ABORT,'MATTERMOST_RESUME_STORAGE_BLOCKED'); END;
CREATE TRIGGER octop_schema_floor_update BEFORE UPDATE ON _schema_version
 WHEN NEW.version<49 BEGIN SELECT RAISE(ABORT,'SCHEMA_DOWNGRADE_BLOCKED'); END;
CREATE TRIGGER octop_schema_floor_insert BEFORE INSERT ON _schema_version
 WHEN NEW.version<49 BEGIN SELECT RAISE(ABORT,'SCHEMA_DOWNGRADE_BLOCKED'); END;
CREATE TRIGGER octop_schema_floor_delete BEFORE DELETE ON _schema_version
 BEGIN SELECT RAISE(ABORT,'SCHEMA_DOWNGRADE_BLOCKED'); END;
UPDATE _schema_version SET version=49;
