ALTER TABLE event_candidates
    ADD COLUMN IF NOT EXISTS rule_evaluation_id uuid REFERENCES rule_evaluations(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_event_candidates_rule_evaluation
ON event_candidates(rule_evaluation_id)
WHERE rule_evaluation_id IS NOT NULL;
