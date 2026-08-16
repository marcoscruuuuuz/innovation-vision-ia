CREATE OR REPLACE FUNCTION vision_guard_after_hours_candidate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    event_name text;
    local_t time;
BEGIN
    SELECT r.event_type INTO event_name
      FROM event_rule_versions v
      JOIN event_rules r ON r.id=v.event_rule_id
     WHERE v.id=NEW.event_rule_version_id;

    IF event_name='movimentacao_apos_22h' THEN
        local_t := (NEW.detected_at AT TIME ZONE 'America/Sao_Paulo')::time;
        IF NOT (local_t >= time '22:00:00' OR local_t < time '06:00:00') THEN
            NEW.pipeline_action := 'DROP';
            NEW.review_status := 'REJECTED';
            NEW.payload := COALESCE(NEW.payload,'{}'::jsonb) || jsonb_build_object(
                'schedule_guard','outside_22_06',
                'local_time',local_t::text
            );
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_guard_after_hours_candidate ON event_candidates;
CREATE TRIGGER trg_guard_after_hours_candidate
BEFORE INSERT OR UPDATE OF detected_at,pipeline_action ON event_candidates
FOR EACH ROW EXECUTE FUNCTION vision_guard_after_hours_candidate();
