-- ============================================================
-- ARIA (Auditoría y Revisión Inteligente Automatizada)
-- Schema SQL para Supabase / PostgreSQL
-- Versión: 1.0.0 — 28 marzo 2026
-- ============================================================
-- INSTRUCCIONES: Ejecutar este script en el SQL Editor de Supabase
-- URL: https://supabase.com/dashboard/project/subzlfzuzcyqyfrzszjb/sql/new
-- ============================================================

-- ============================================================
-- 1. TABLA: call_audits
-- Registro maestro de cada llamada auditada por ARIA
-- ============================================================
CREATE TABLE IF NOT EXISTS call_audits (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Identificadores de la llamada
    vapi_call_id            TEXT NOT NULL UNIQUE,
    ghl_contact_id          TEXT,
    phone_number            TEXT,
    agent_name              TEXT NOT NULL DEFAULT 'elena',  -- para multi-agente futuro
    
    -- Timing
    call_started_at         TIMESTAMPTZ,
    call_ended_at           TIMESTAMPTZ,
    call_duration_seconds   INTEGER,
    
    -- Clasificación original (lo que Elena/GHL escribió)
    original_outcome        TEXT,   -- agendo, no_agendo, no_contesto, llamar_luego, error_tecnico
    original_ended_reason   TEXT,   -- customer-ended-call, assistant-ended-call, etc.
    original_success_eval   BOOLEAN,
    original_summary        TEXT,
    
    -- Clasificación de ARIA (resultado de la auditoría)
    aria_outcome            TEXT,   -- lo que ARIA cree que debería ser
    aria_confidence         FLOAT,  -- 0.0 - 1.0
    aria_reasoning          TEXT,   -- explicación de por qué ARIA difiere (si difiere)
    
    -- Estado de la auditoría
    audit_status            TEXT NOT NULL DEFAULT 'pending',
    -- pending: aún no auditado
    -- audited: auditado, sin discrepancia
    -- discrepancy_found: ARIA detectó clasificación incorrecta
    -- corrected: GHL fue actualizado con la clasificación de ARIA
    -- feedback_approved: Juan aprobó la corrección de ARIA (✅)
    -- feedback_rejected: Juan rechazó la corrección de ARIA (❌)
    
    -- Métricas de calidad de la conversación
    playbook_adherence_score    FLOAT,  -- 0.0 - 1.0 (qué tan bien siguió Elena el playbook)
    silence_detected            BOOLEAN DEFAULT FALSE,
    silence_duration_seconds    INTEGER,
    language_switch_detected    BOOLEAN DEFAULT FALSE,
    objection_handled           BOOLEAN,
    appointment_offered         BOOLEAN,
    
    -- Errores detectados por ARIA
    errors_detected             JSONB DEFAULT '[]',
    -- Array de objetos: [{type: "missed_close", description: "...", severity: "high"}]
    
    -- Transcripción y audio
    transcript_text             TEXT,
    audio_url                   TEXT,
    
    -- Metadatos
    audit_model                 TEXT DEFAULT 'claude-3-5-sonnet-20241022',
    audit_version               TEXT DEFAULT '1.0.0',
    raw_vapi_data               JSONB,  -- payload completo de Vapi para debugging
    
    -- Índices para queries frecuentes
    CONSTRAINT valid_outcome CHECK (
        original_outcome IN ('agendo', 'no_agendo', 'no_contesto', 'llamar_luego', 
                             'error_tecnico', 'no_interesado', NULL)
    ),
    CONSTRAINT valid_aria_outcome CHECK (
        aria_outcome IN ('agendo', 'no_agendo', 'no_contesto', 'llamar_luego', 
                        'error_tecnico', 'no_interesado', NULL)
    ),
    CONSTRAINT valid_status CHECK (
        audit_status IN ('pending', 'audited', 'discrepancy_found', 'corrected', 
                        'feedback_approved', 'feedback_rejected')
    )
);

CREATE INDEX IF NOT EXISTS idx_call_audits_vapi_call_id ON call_audits(vapi_call_id);
CREATE INDEX IF NOT EXISTS idx_call_audits_created_at ON call_audits(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_audits_audit_status ON call_audits(audit_status);
CREATE INDEX IF NOT EXISTS idx_call_audits_agent_name ON call_audits(agent_name);
CREATE INDEX IF NOT EXISTS idx_call_audits_original_outcome ON call_audits(original_outcome);


-- ============================================================
-- 2. TABLA: feedback_log
-- Registro del feedback de Juan (RLHF) sobre las correcciones de ARIA
-- ============================================================
CREATE TABLE IF NOT EXISTS feedback_log (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Referencia a la auditoría
    audit_id            UUID NOT NULL REFERENCES call_audits(id) ON DELETE CASCADE,
    vapi_call_id        TEXT NOT NULL,
    
    -- Feedback
    feedback_type       TEXT NOT NULL,  -- 'approved' (✅) o 'rejected' (❌)
    feedback_source     TEXT DEFAULT 'whatsapp',  -- whatsapp, email, dashboard
    
    -- Detalles del feedback
    original_outcome    TEXT,
    aria_outcome        TEXT,
    final_outcome       TEXT,  -- el outcome que quedó después del feedback
    
    -- Notas opcionales de Juan
    notes               TEXT,
    
    -- Para RLHF: qué aprendió ARIA de este feedback
    rlhf_applied        BOOLEAN DEFAULT FALSE,
    rlhf_notes          TEXT,
    
    CONSTRAINT valid_feedback CHECK (feedback_type IN ('approved', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_feedback_log_audit_id ON feedback_log(audit_id);
CREATE INDEX IF NOT EXISTS idx_feedback_log_created_at ON feedback_log(created_at DESC);


-- ============================================================
-- 3. TABLA: daily_metrics
-- Métricas agregadas por día para el reporte diario
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_metrics (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Período
    metric_date                 DATE NOT NULL,
    agent_name                  TEXT NOT NULL DEFAULT 'elena',
    
    -- Volumen de llamadas
    total_calls                 INTEGER DEFAULT 0,
    calls_agendo                INTEGER DEFAULT 0,
    calls_no_agendo             INTEGER DEFAULT 0,
    calls_no_contesto           INTEGER DEFAULT 0,
    calls_llamar_luego          INTEGER DEFAULT 0,
    calls_error_tecnico         INTEGER DEFAULT 0,
    calls_no_interesado         INTEGER DEFAULT 0,
    
    -- Tasas de conversión
    conversion_rate             FLOAT,  -- agendo / total_calls
    contact_rate                FLOAT,  -- (total - no_contesto) / total_calls
    
    -- Métricas de calidad
    avg_call_duration_seconds   FLOAT,
    avg_playbook_adherence      FLOAT,
    calls_with_silence          INTEGER DEFAULT 0,
    calls_with_errors           INTEGER DEFAULT 0,
    
    -- Correcciones de ARIA
    aria_discrepancies_found    INTEGER DEFAULT 0,
    aria_corrections_applied    INTEGER DEFAULT 0,
    aria_corrections_approved   INTEGER DEFAULT 0,
    aria_corrections_rejected   INTEGER DEFAULT 0,
    
    -- Errores más frecuentes (JSON array de {type, count})
    top_errors                  JSONB DEFAULT '[]',
    
    -- Metadatos del reporte
    report_generated_at         TIMESTAMPTZ,
    report_sent_whatsapp        BOOLEAN DEFAULT FALSE,
    report_sent_email           BOOLEAN DEFAULT FALSE,
    
    UNIQUE(metric_date, agent_name)
);

CREATE INDEX IF NOT EXISTS idx_daily_metrics_date ON daily_metrics(metric_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_metrics_agent ON daily_metrics(agent_name);


-- ============================================================
-- 4. TABLA: aria_corrections
-- Log de cada corrección que ARIA hizo en GHL
-- ============================================================
CREATE TABLE IF NOT EXISTS aria_corrections (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Referencia
    audit_id            UUID NOT NULL REFERENCES call_audits(id) ON DELETE CASCADE,
    vapi_call_id        TEXT NOT NULL,
    ghl_contact_id      TEXT NOT NULL,
    
    -- Qué se corrigió
    field_name          TEXT NOT NULL,  -- elena_last_outcome, elena_success_eval, etc.
    old_value           TEXT,
    new_value           TEXT,
    
    -- Estado de la corrección
    correction_status   TEXT NOT NULL DEFAULT 'pending',
    -- pending: esperando aprobación de Juan
    -- applied: ya se aplicó en GHL
    -- reverted: Juan rechazó y se revirtió
    
    -- GHL response
    ghl_response_code   INTEGER,
    ghl_response_body   TEXT,
    
    CONSTRAINT valid_correction_status CHECK (
        correction_status IN ('pending', 'applied', 'reverted')
    )
);

CREATE INDEX IF NOT EXISTS idx_aria_corrections_audit_id ON aria_corrections(audit_id);
CREATE INDEX IF NOT EXISTS idx_aria_corrections_ghl_contact ON aria_corrections(ghl_contact_id);


-- ============================================================
-- 5. TABLA: aria_config
-- Configuración dinámica de ARIA (umbrales, prompts, etc.)
-- ============================================================
CREATE TABLE IF NOT EXISTS aria_config (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    config_key          TEXT NOT NULL UNIQUE,
    config_value        TEXT NOT NULL,
    config_type         TEXT DEFAULT 'string',  -- string, integer, float, boolean, json
    description         TEXT,
    
    -- Historial de cambios
    previous_value      TEXT,
    changed_by          TEXT DEFAULT 'system'
);

-- Insertar configuración inicial
INSERT INTO aria_config (config_key, config_value, config_type, description) VALUES
    ('audit_batch_size', '50', 'integer', 'Número máximo de llamadas a auditar por ejecución'),
    ('audit_lookback_hours', '25', 'integer', 'Horas hacia atrás para buscar llamadas nuevas'),
    ('confidence_threshold_correction', '0.85', 'float', 'Confianza mínima para proponer corrección automática'),
    ('confidence_threshold_auto_apply', '0.95', 'float', 'Confianza mínima para aplicar corrección sin aprobación'),
    ('report_time_utc', '12:00', 'string', 'Hora UTC de envío del reporte diario (12:00 = 7am EST)'),
    ('admin_whatsapp', '+17865533777', 'string', 'WhatsApp del administrador para reportes'),
    ('admin_email', 'vitusmediard@gmail.com', 'string', 'Email del administrador para reportes'),
    ('aria_version', '1.0.0', 'string', 'Versión actual de ARIA'),
    ('auto_correct_enabled', 'false', 'boolean', 'Si ARIA puede corregir GHL automáticamente sin aprobación'),
    ('audit_model', 'claude-3-5-sonnet-20241022', 'string', 'Modelo LLM usado para auditorías')
ON CONFLICT (config_key) DO NOTHING;


-- ============================================================
-- 6. VISTA: v_audit_summary
-- Vista para queries rápidas del estado de auditorías
-- ============================================================
CREATE OR REPLACE VIEW v_audit_summary AS
SELECT
    DATE(created_at AT TIME ZONE 'America/New_York') AS audit_date,
    agent_name,
    COUNT(*) AS total_audited,
    COUNT(*) FILTER (WHERE audit_status = 'discrepancy_found') AS discrepancies,
    COUNT(*) FILTER (WHERE audit_status = 'corrected') AS corrections_applied,
    COUNT(*) FILTER (WHERE audit_status = 'feedback_approved') AS approved_by_juan,
    COUNT(*) FILTER (WHERE audit_status = 'feedback_rejected') AS rejected_by_juan,
    ROUND(AVG(aria_confidence)::numeric, 3) AS avg_confidence,
    COUNT(*) FILTER (WHERE original_outcome = 'agendo') AS agendo_count,
    COUNT(*) FILTER (WHERE original_outcome = 'no_agendo') AS no_agendo_count,
    COUNT(*) FILTER (WHERE original_outcome = 'no_contesto') AS no_contesto_count,
    COUNT(*) FILTER (WHERE original_outcome = 'llamar_luego') AS llamar_luego_count,
    COUNT(*) FILTER (WHERE original_outcome = 'error_tecnico') AS error_tecnico_count
FROM call_audits
GROUP BY DATE(created_at AT TIME ZONE 'America/New_York'), agent_name
ORDER BY audit_date DESC;


-- ============================================================
-- 7. VISTA: v_pending_feedback
-- Correcciones que esperan feedback de Juan
-- ============================================================
CREATE OR REPLACE VIEW v_pending_feedback AS
SELECT
    ca.id,
    ca.vapi_call_id,
    ca.ghl_contact_id,
    ca.phone_number,
    ca.call_started_at AT TIME ZONE 'America/New_York' AS call_time_est,
    ca.call_duration_seconds,
    ca.original_outcome,
    ca.aria_outcome,
    ca.aria_confidence,
    ca.aria_reasoning,
    ca.created_at AT TIME ZONE 'America/New_York' AS audited_at_est
FROM call_audits ca
WHERE ca.audit_status = 'discrepancy_found'
ORDER BY ca.created_at DESC;


-- ============================================================
-- VERIFICACIÓN FINAL
-- ============================================================
SELECT 
    'Schema ARIA creado exitosamente' AS status,
    COUNT(*) AS tables_created
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('call_audits', 'feedback_log', 'daily_metrics', 'aria_corrections', 'aria_config');
