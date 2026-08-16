CREATE TABLE IF NOT EXISTS p2p_failover_operations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dvr_id uuid NOT NULL REFERENCES dvrs(id) ON DELETE CASCADE,
    source_session_id uuid REFERENCES p2p_sessions(id) ON DELETE SET NULL,
    destination_session_id uuid REFERENCES p2p_sessions(id) ON DELETE SET NULL,
    source_wine_worker_id uuid REFERENCES wine_workers(id) ON DELETE SET NULL,
    destination_wine_worker_id uuid REFERENCES wine_workers(id) ON DELETE SET NULL,
    actor_type text NOT NULL CHECK (actor_type IN ('USER','AI','WATCHDOG','SYSTEM')),
    reason text NOT NULL,
    state text NOT NULL CHECK (state IN ('PLANNED','OPENING_DESTINATION','VALIDATING_DESTINATION','SWITCHING_ROUTES','VERIFYING','CLOSING_SOURCE','COMMITTED','ROLLING_BACK','ROLLED_BACK','FAILED')),
    step text,
    error text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_failover_operations_dvr_time
ON p2p_failover_operations(dvr_id, started_at DESC);
