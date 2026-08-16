ALTER TABLE dvrs
    ADD COLUMN IF NOT EXISTS ip_lan text,
    ADD COLUMN IF NOT EXISTS ip_wan text,
    ADD COLUMN IF NOT EXISTS rtsp_tcp_port integer CHECK (rtsp_tcp_port IS NULL OR rtsp_tcp_port BETWEEN 1 AND 65535),
    ADD COLUMN IF NOT EXISTS rtsp_udp_port integer CHECK (rtsp_udp_port IS NULL OR rtsp_udp_port BETWEEN 1 AND 65535),
    ADD COLUMN IF NOT EXISTS tcp_p2p_port integer CHECK (tcp_p2p_port IS NULL OR tcp_p2p_port BETWEEN 1 AND 65535),
    ADD COLUMN IF NOT EXISTS channel_count integer CHECK (channel_count IS NULL OR channel_count > 0),
    ADD COLUMN IF NOT EXISTS ddns_host text,
    ADD COLUMN IF NOT EXISTS mac text,
    ADD COLUMN IF NOT EXISTS ddns_lan_ip text,
    ADD COLUMN IF NOT EXISTS ddns_wan_ip text,
    ADD COLUMN IF NOT EXISTS notes text;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS last_login_at timestamptz;

CREATE TABLE IF NOT EXISTS portal_login_audit (
    id bigserial PRIMARY KEY,
    user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    username text NOT NULL,
    success boolean NOT NULL,
    remote_hint text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_portal_login_audit_time
ON portal_login_audit(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_dvrs_condominium_enabled
ON dvrs(condominium_id, enabled);

CREATE INDEX IF NOT EXISTS idx_cameras_dvr_enabled
ON cameras(dvr_id, enabled);

CREATE INDEX IF NOT EXISTS idx_stream_routes_active
ON stream_routes(camera_id, state, deactivated_at);
