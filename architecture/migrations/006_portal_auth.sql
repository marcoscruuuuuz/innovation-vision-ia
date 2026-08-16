CREATE TABLE IF NOT EXISTS user_api_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE,
    label text NOT NULL,
    scopes text[] NOT NULL DEFAULT ARRAY['client:read']::text[],
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    expires_at timestamptz
);

ALTER TABLE event_candidates
    ADD COLUMN IF NOT EXISTS reviewed_by uuid REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reviewed_at timestamptz,
    ADD COLUMN IF NOT EXISTS review_notes text;

CREATE TABLE IF NOT EXISTS retention_job_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    cutoff_at timestamptz NOT NULL,
    deleted_logs integer NOT NULL DEFAULT 0,
    deleted_evidence integer NOT NULL DEFAULT 0,
    status text NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED')),
    error text
);

CREATE TABLE IF NOT EXISTS evidence_deletion_queue (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_key text NOT NULL UNIQUE,
    queued_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    last_error text
);

CREATE INDEX IF NOT EXISTS idx_user_api_tokens_user ON user_api_tokens(user_id) WHERE active=true;
CREATE INDEX IF NOT EXISTS idx_review_queue ON event_candidates(review_status, created_at DESC);
