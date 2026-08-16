-- Client portal needs a tenant-scoped condominium list in addition to logs, evidence and cameras.
GRANT SELECT ON condominiums TO vision_portal;

ALTER TABLE condominiums ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS portal_condominiums_select ON condominiums;
CREATE POLICY portal_condominiums_select ON condominiums
FOR SELECT TO vision_portal
USING (
  id = ANY(
    COALESCE(NULLIF(string_to_array(current_setting('app.tenant_ids', true), ','), ARRAY['']), ARRAY[]::text[])::uuid[]
  )
);
