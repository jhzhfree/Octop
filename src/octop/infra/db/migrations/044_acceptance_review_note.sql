ALTER TABLE scene_run_acceptances ADD COLUMN review_note TEXT NOT NULL DEFAULT '' CHECK(length(review_note)<=2000);
UPDATE _schema_version SET version=44;
