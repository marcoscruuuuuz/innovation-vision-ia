DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vision_portal') THEN
    CREATE ROLE vision_portal NOLOGIN;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO vision_portal;
GRANT SELECT ON event_logs, event_evidence, cameras TO vision_portal;

ALTER TABLE event_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE cameras ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS portal_event_logs_select ON event_logs;
CREATE POLICY portal_event_logs_select ON event_logs
FOR SELECT TO vision_portal
USING (
  condominium_id = ANY(
    COALESCE(NULLIF(string_to_array(current_setting('app.tenant_ids', true), ','), ARRAY['']), ARRAY[]::text[])::uuid[]
  )
);

DROP POLICY IF EXISTS portal_cameras_select ON cameras;
CREATE POLICY portal_cameras_select ON cameras
FOR SELECT TO vision_portal
USING (
  condominium_id = ANY(
    COALESCE(NULLIF(string_to_array(current_setting('app.tenant_ids', true), ','), ARRAY['']), ARRAY[]::text[])::uuid[]
  )
);

DROP POLICY IF EXISTS portal_event_evidence_select ON event_evidence;
CREATE POLICY portal_event_evidence_select ON event_evidence
FOR SELECT TO vision_portal
USING (
  event_log_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM event_logs l
    WHERE l.id = event_evidence.event_log_id
  )
);
