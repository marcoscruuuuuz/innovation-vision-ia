ALTER TABLE p2p_sessions
    ADD COLUMN IF NOT EXISTS vendor_session_ref text,
    ADD COLUMN IF NOT EXISTS relay_mode text,
    ADD COLUMN IF NOT EXISTS last_health_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_frame_at timestamptz,
    ADD COLUMN IF NOT EXISTS frame_probe_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS vendor_metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS uq_p2p_vendor_session_ref
ON p2p_sessions(vendor_session_ref)
WHERE vendor_session_ref IS NOT NULL;
