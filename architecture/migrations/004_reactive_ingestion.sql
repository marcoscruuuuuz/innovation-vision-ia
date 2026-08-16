CREATE TABLE IF NOT EXISTS ingestion_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dvr_id uuid NOT NULL UNIQUE REFERENCES dvrs(id) ON DELETE CASCADE,
    source_key text NOT NULL UNIQUE,
    hmac_secret_ref text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    allowed_clock_skew_seconds integer NOT NULL DEFAULT 300 CHECK (allowed_clock_skew_seconds BETWEEN 30 AND 3600),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_source_id uuid NOT NULL REFERENCES ingestion_sources(id) ON DELETE RESTRICT,
    condominium_id uuid NOT NULL REFERENCES condominiums(id) ON DELETE RESTRICT,
    dvr_id uuid NOT NULL REFERENCES dvrs(id) ON DELETE RESTRICT,
    camera_id uuid REFERENCES cameras(id) ON DELETE SET NULL,
    channel integer CHECK (channel IS NULL OR channel > 0),
    external_event_id text NOT NULL,
    event_name text NOT NULL,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    snapshot_object_key text,
    snapshot_sha256 text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    processing_mode text NOT NULL DEFAULT 'SNAPSHOT' CHECK (processing_mode IN ('SNAPSHOT','TEMPORAL_BURST','METADATA_ONLY')),
    queue_status text NOT NULL DEFAULT 'RECEIVED' CHECK (queue_status IN ('RECEIVED','QUEUED','PROCESSING','DONE','FAILED','DUPLICATE')),
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ingestion_source_id, external_event_id)
);

CREATE TABLE IF NOT EXISTS ingestion_nonces (
    ingestion_source_id uuid NOT NULL REFERENCES ingestion_sources(id) ON DELETE CASCADE,
    nonce text NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ingestion_source_id, nonce)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_events_status_received
ON ingestion_events(queue_status, received_at);

CREATE INDEX IF NOT EXISTS idx_ingestion_events_condo_time
ON ingestion_events(condominium_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_ingestion_nonces_expiry
ON ingestion_nonces(expires_at);
