CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS condominiums (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('admin','operator','reviewer','client')),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_condominiums (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    condominium_id uuid NOT NULL REFERENCES condominiums(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, condominium_id)
);

CREATE TABLE IF NOT EXISTS dvrs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    condominium_id uuid NOT NULL REFERENCES condominiums(id) ON DELETE RESTRICT,
    name text NOT NULL,
    model text,
    serial_secret_ref text,
    username_secret_ref text,
    password_secret_ref text,
    connection_mode text NOT NULL DEFAULT 'intelbras_p2p' CHECK (connection_mode IN ('intelbras_p2p','rtsp','edge_push')),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (condominium_id, name)
);

CREATE TABLE IF NOT EXISTS cameras (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    condominium_id uuid NOT NULL REFERENCES condominiums(id) ON DELETE RESTRICT,
    dvr_id uuid NOT NULL REFERENCES dvrs(id) ON DELETE CASCADE,
    channel integer NOT NULL CHECK (channel > 0),
    name text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    health_state text NOT NULL DEFAULT 'OFFLINE' CHECK (health_state IN ('ONLINE','DEGRADED','OFFLINE','P2P_CONNECTED_NO_VIDEO','VIDEO_NO_FRAMES','DECODER_ERROR','AUTH_ERROR')),
    last_frame_at timestamptz,
    last_heartbeat_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dvr_id, channel)
);

CREATE TABLE IF NOT EXISTS wine_workers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    worker_key text NOT NULL UNIQUE,
    host text NOT NULL DEFAULT 'local',
    pid integer,
    state text NOT NULL DEFAULT 'STOPPED',
    cpu_percent numeric(7,3),
    ram_mb numeric(12,2),
    active_sessions integer NOT NULL DEFAULT 0,
    last_heartbeat_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS p2p_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dvr_id uuid NOT NULL REFERENCES dvrs(id) ON DELETE CASCADE,
    wine_worker_id uuid REFERENCES wine_workers(id) ON DELETE SET NULL,
    sdk_local_port integer NOT NULL CHECK (sdk_local_port BETWEEN 1 AND 65535),
    rtsp_local_port integer NOT NULL CHECK (rtsp_local_port BETWEEN 1 AND 65535),
    state text NOT NULL CHECK (state IN ('OPENING','ACTIVE','DEGRADED','CLOSING','CLOSED','FAILED')),
    latency_ms numeric(12,3),
    last_sdk_error text,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    open_reason text,
    close_reason text,
    actor_type text NOT NULL DEFAULT 'WATCHDOG' CHECK (actor_type IN ('USER','AI','WATCHDOG','SYSTEM')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_p2p_sdk_port
ON p2p_sessions (sdk_local_port) WHERE ended_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_p2p_rtsp_port
ON p2p_sessions (rtsp_local_port) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS camera_health_history (
    id bigserial PRIMARY KEY,
    camera_id uuid NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    state text NOT NULL,
    fps numeric(8,3),
    frame_gap_ms numeric(12,3),
    decode_latency_ms numeric(12,3),
    observed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS event_rules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id uuid NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    event_type text NOT NULL,
    display_label text,
    active_version integer NOT NULL DEFAULT 1,
    enabled boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (camera_id, event_type)
);

CREATE TABLE IF NOT EXISTS event_rule_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_rule_id uuid NOT NULL REFERENCES event_rules(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    geometry jsonb,
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    model_requirements jsonb NOT NULL DEFAULT '{}'::jsonb,
    certification_status text NOT NULL DEFAULT 'DRAFT' CHECK (certification_status IN ('DRAFT','CONFIGURED','SHADOW','HOMOLOGATION','AI_REVIEW','CERTIFIED','PRODUCTION','REJECTED','ADJUSTMENT_REQUIRED')),
    created_by uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (event_rule_id, version)
);

CREATE TABLE IF NOT EXISTS event_candidates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    condominium_id uuid NOT NULL REFERENCES condominiums(id) ON DELETE RESTRICT,
    camera_id uuid NOT NULL REFERENCES cameras(id) ON DELETE RESTRICT,
    event_rule_version_id uuid NOT NULL REFERENCES event_rule_versions(id) ON DELETE RESTRICT,
    detected_at timestamptz NOT NULL,
    confidence numeric(6,5) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    review_status text NOT NULL DEFAULT 'AI_REVIEW_PENDING' CHECK (review_status IN ('AI_REVIEW_PENDING','AI_APPROVED','AI_REJECTED','AI_UNCERTAIN','HUMAN_REVIEW','APPROVED','REJECTED')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS event_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id uuid UNIQUE REFERENCES event_candidates(id) ON DELETE SET NULL,
    condominium_id uuid NOT NULL REFERENCES condominiums(id) ON DELETE RESTRICT,
    camera_id uuid NOT NULL REFERENCES cameras(id) ON DELETE RESTRICT,
    event_type text NOT NULL,
    display_name text NOT NULL,
    occurred_at timestamptz NOT NULL,
    confidence numeric(6,5),
    client_visible boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'APPROVED',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS event_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_log_id uuid REFERENCES event_logs(id) ON DELETE CASCADE,
    event_candidate_id uuid REFERENCES event_candidates(id) ON DELETE CASCADE,
    object_key text NOT NULL,
    media_type text NOT NULL CHECK (media_type IN ('snapshot','clip')),
    sha256 text NOT NULL,
    size_bytes bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (event_log_id IS NOT NULL OR event_candidate_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS certification_samples (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_rule_version_id uuid NOT NULL REFERENCES event_rule_versions(id) ON DELETE CASCADE,
    candidate_id uuid REFERENCES event_candidates(id) ON DELETE SET NULL,
    expected_label text CHECK (expected_label IN ('positive','negative','unknown')),
    ai_label text CHECK (ai_label IN ('positive','negative','unknown')),
    human_label text CHECK (human_label IN ('positive','negative','unknown')),
    notes text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_tool_calls (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    tool_name text NOT NULL,
    permission_level text NOT NULL CHECK (permission_level IN ('READ','PLAN','EXECUTE')),
    sanitized_arguments jsonb NOT NULL DEFAULT '{}'::jsonb,
    result_status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id bigserial PRIMARY KEY,
    actor_type text NOT NULL CHECK (actor_type IN ('USER','AI','WATCHDOG','SYSTEM')),
    actor_id text,
    action text NOT NULL,
    object_type text NOT NULL,
    object_id text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cameras_condominium ON cameras(condominium_id);
CREATE INDEX IF NOT EXISTS idx_camera_health_observed ON camera_health_history(camera_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_review ON event_candidates(review_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_condo_time ON event_logs(condominium_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_camera_time ON event_logs(camera_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_p2p_dvr_time ON p2p_sessions(dvr_id, started_at DESC);

CREATE TABLE IF NOT EXISTS p2p_port_leases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    port_type text NOT NULL CHECK (port_type IN ('SDK_TCP','RTSP')),
    port integer NOT NULL CHECK (port BETWEEN 1 AND 65535),
    dvr_id uuid REFERENCES dvrs(id) ON DELETE CASCADE,
    wine_worker_id uuid REFERENCES wine_workers(id) ON DELETE SET NULL,
    lease_owner text NOT NULL,
    leased_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz,
    released_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_p2p_port_lease
ON p2p_port_leases(port) WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS dvr_wine_assignments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dvr_id uuid NOT NULL REFERENCES dvrs(id) ON DELETE CASCADE,
    wine_worker_id uuid NOT NULL REFERENCES wine_workers(id) ON DELETE CASCADE,
    active boolean NOT NULL DEFAULT true,
    load_score numeric(12,4),
    reason text,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_dvr_wine_assignment
ON dvr_wine_assignments(dvr_id) WHERE active = true AND ended_at IS NULL;

CREATE TABLE IF NOT EXISTS model_registry (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_key text NOT NULL,
    version text NOT NULL,
    task text NOT NULL,
    artifact_ref text NOT NULL,
    sha256 text NOT NULL,
    active boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model_key, version)
);

CREATE TABLE IF NOT EXISTS event_feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_candidate_id uuid REFERENCES event_candidates(id) ON DELETE CASCADE,
    event_log_id uuid REFERENCES event_logs(id) ON DELETE CASCADE,
    label text NOT NULL CHECK (label IN ('CORRECT','FALSE_POSITIVE','FALSE_NEGATIVE','INCONCLUSIVE')),
    user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (event_candidate_id IS NOT NULL OR event_log_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_log_id uuid NOT NULL REFERENCES event_logs(id) ON DELETE CASCADE,
    channel text NOT NULL CHECK (channel IN ('WHATSAPP','WEBHOOK','EMAIL','INTERNAL')),
    recipient_ref text NOT NULL,
    provider text,
    status text NOT NULL CHECK (status IN ('QUEUED','SENT','DELIVERED','FAILED','CANCELLED')),
    attempts integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_chat_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    condominium_id uuid REFERENCES condominiums(id) ON DELETE SET NULL,
    scope text NOT NULL CHECK (scope IN ('ADMIN_IDE','CLIENT_LOG_QUERY')),
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_port_leases_active ON p2p_port_leases(port_type, port) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_feedback_candidate ON event_feedback(event_candidate_id);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notification_deliveries(status, created_at);
