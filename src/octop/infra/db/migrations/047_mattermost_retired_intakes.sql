-- Storage only: an upgrade must not implicitly retire any existing identity.
CREATE TABLE mattermost_retired_intakes (
 delivery_key TEXT PRIMARY KEY REFERENCES mattermost_intakes(delivery_key),
 reason TEXT NOT NULL,
 retired_at BIGINT NOT NULL
);
-- Creating the table does not authorize permanent retirement of any identity.
UPDATE _schema_version SET version=47;
