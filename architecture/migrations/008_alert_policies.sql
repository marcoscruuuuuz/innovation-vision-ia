CREATE TABLE IF NOT EXISTS alert_policies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    condominium_id uuid NOT NULL REFERENCES condominiums(id) ON DELETE CASCADE,
    event_type text NOT NULL,
    channel text NOT NULL CHECK (channel IN ('WHATSAPP','WEBHOOK','EMAIL','INTERNAL')),
    recipient_ref text NOT NULL,
    provider text,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (condominium_id,event_type,channel,recipient_ref)
);

CREATE INDEX IF NOT EXISTS idx_alert_policy_match
ON alert_policies(condominium_id,event_type) WHERE enabled=true;
