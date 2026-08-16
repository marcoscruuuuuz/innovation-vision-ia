ALTER TABLE ingestion_events
    ADD COLUMN IF NOT EXISTS clip_object_key text,
    ADD COLUMN IF NOT EXISTS clip_sha256 text,
    ADD COLUMN IF NOT EXISTS clip_duration_seconds numeric(8,3);

CREATE INDEX IF NOT EXISTS idx_ingestion_events_clip
ON ingestion_events(camera_id, occurred_at DESC)
WHERE clip_object_key IS NOT NULL;
