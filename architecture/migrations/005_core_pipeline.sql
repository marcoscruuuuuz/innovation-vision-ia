CREATE TABLE IF NOT EXISTS detection_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_event_id uuid NOT NULL UNIQUE REFERENCES ingestion_events(id) ON DELETE CASCADE,
    camera_id uuid REFERENCES cameras(id) ON DELETE SET NULL,
    backend text NOT NULL,
    model_key text,
    model_version text,
    status text NOT NULL CHECK (status IN ('SUCCESS','BLOCKED_MODEL','FAILED')),
    detections jsonb NOT NULL DEFAULT '[]'::jsonb,
    inference_ms numeric(12,3),
    error text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rule_evaluations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    detection_result_id uuid NOT NULL REFERENCES detection_results(id) ON DELETE CASCADE,
    event_rule_version_id uuid NOT NULL REFERENCES event_rule_versions(id) ON DELETE CASCADE,
    outcome text NOT NULL CHECK (outcome IN ('MATCH','NO_MATCH','NEEDS_TEMPORAL','MODEL_REQUIRED','ERROR')),
    confidence numeric(6,5),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (detection_result_id, event_rule_version_id)
);

CREATE TABLE IF NOT EXISTS event_confidence_policies (
    event_type text PRIMARY KEY,
    min_log_confidence numeric(6,5) NOT NULL DEFAULT 0.80000 CHECK (min_log_confidence BETWEEN 0 AND 1),
    review_from_confidence numeric(6,5) NOT NULL DEFAULT 0.92000 CHECK (review_from_confidence BETWEEN 0 AND 1),
    evidence_from_confidence numeric(6,5) NOT NULL DEFAULT 0.93000 CHECK (evidence_from_confidence BETWEEN 0 AND 1),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (min_log_confidence <= review_from_confidence AND review_from_confidence <= evidence_from_confidence)
);

ALTER TABLE event_candidates
    ADD COLUMN IF NOT EXISTS ingestion_event_id uuid REFERENCES ingestion_events(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS detection_result_id uuid REFERENCES detection_results(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS pipeline_action text CHECK (pipeline_action IS NULL OR pipeline_action IN ('DROP','TEXT_LOG','HUMAN_REVIEW','EVIDENCE_LOG'));

CREATE INDEX IF NOT EXISTS idx_detection_camera_time ON detection_results(camera_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rule_eval_detection ON rule_evaluations(detection_result_id);
CREATE INDEX IF NOT EXISTS idx_candidates_pipeline_action ON event_candidates(pipeline_action, created_at DESC);
