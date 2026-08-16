CREATE TABLE IF NOT EXISTS evidence_clip_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_log_id uuid NOT NULL UNIQUE REFERENCES event_logs(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','DONE','FAILED')),
    attempts integer NOT NULL DEFAULT 0,
    duration_seconds integer NOT NULL DEFAULT 10 CHECK (duration_seconds BETWEEN 3 AND 60),
    last_error text,
    retry_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_evidence_clip_jobs_pending
ON evidence_clip_jobs(status,retry_at,created_at);

CREATE OR REPLACE FUNCTION vision_enqueue_evidence_clip()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'APPROVED' AND NEW.client_visible THEN
        INSERT INTO evidence_clip_jobs(event_log_id)
        VALUES (NEW.id)
        ON CONFLICT(event_log_id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enqueue_evidence_clip ON event_logs;
CREATE TRIGGER trg_enqueue_evidence_clip
AFTER INSERT OR UPDATE OF status,client_visible ON event_logs
FOR EACH ROW EXECUTE FUNCTION vision_enqueue_evidence_clip();

INSERT INTO evidence_clip_jobs(event_log_id)
SELECT l.id FROM event_logs l
WHERE l.status='APPROVED' AND l.client_visible=true
ON CONFLICT(event_log_id) DO NOTHING;
