-- ARIA v3.0 — Tabla call_intelligence
-- Ejecutar en Supabase Dashboard > SQL Editor

CREATE TABLE IF NOT EXISTS call_intelligence (
    id BIGSERIAL PRIMARY KEY,
    vapi_call_id TEXT UNIQUE NOT NULL,
    audit_id BIGINT,
    call_type TEXT,
    language TEXT,
    interest_level INTEGER,
    zones_mentioned JSONB,
    objections JSONB,
    questions_asked JSONB,
    barriers JSONB,
    outcome_reason TEXT,
    best_callback_signal TEXT,
    engagement_quality TEXT,
    trust_signals JSONB,
    buying_stage TEXT,
    price_sensitivity TEXT,
    treatment_knowledge TEXT,
    phone_number TEXT,
    ghl_contact_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ci_buying_stage ON call_intelligence(buying_stage);
CREATE INDEX IF NOT EXISTS idx_ci_interest_level ON call_intelligence(interest_level);
CREATE INDEX IF NOT EXISTS idx_ci_phone ON call_intelligence(phone_number);
CREATE INDEX IF NOT EXISTS idx_ci_created_at ON call_intelligence(created_at);
