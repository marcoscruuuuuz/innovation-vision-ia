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

CREATE TABLE IF NOT EXISTS stream_routes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id uuid NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    p2p_session_id uuid REFERENCES p2p_sessions(id) ON DELETE SET NULL,
    source_type text NOT NULL CHECK (source_type IN ('P2P_RTSP','DIRECT_RTSP','EDGE_PUSH')),
    local_uri text NOT NULL,
    state text NOT NULL DEFAULT 'ACTIVE' CHECK (state IN ('ACTIVE','DRAINING','FAILED','INACTIVE')),
    generation bigint NOT NULL DEFAULT 1,
    activated_at timestamptz NOT NULL DEFAULT now(),
    deactivated_at timestamptz,
    last_frame_at timestamptz,
    consecutive_health_failures integer NOT NULL DEFAULT 0,
    consecutive_health_successes integer NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_stream_route_active_camera
ON stream_routes(camera_id)
WHERE state='ACTIVE' AND deactivated_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_stream_routes_session
ON stream_routes(p2p_session_id, state);

CREATE TABLE IF NOT EXISTS stream_route_switches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id uuid NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    from_route_id uuid REFERENCES stream_routes(id) ON DELETE SET NULL,
    to_route_id uuid REFERENCES stream_routes(id) ON DELETE SET NULL,
    reason text NOT NULL,
    actor_type text NOT NULL CHECK (actor_type IN ('USER','AI','WATCHDOG','SYSTEM')),
    status text NOT NULL CHECK (status IN ('PLANNED','VALIDATING','COMMITTED','ROLLED_BACK','FAILED','BLOCKED')),
    probe_frames integer NOT NULL DEFAULT 0,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_route_switches_camera_time
ON stream_route_switches(camera_id, started_at DESC);

CREATE TABLE IF NOT EXISTS p2p_failover_state (
    dvr_id uuid PRIMARY KEY REFERENCES dvrs(id) ON DELETE CASCADE,
    last_failover_at timestamptz,
    cooldown_until timestamptz,
    failure_streak integer NOT NULL DEFAULT 0,
    recovery_streak integer NOT NULL DEFAULT 0,
    last_reason text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
