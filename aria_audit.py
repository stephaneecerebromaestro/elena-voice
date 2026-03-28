#!/usr/bin/env python3.11
"""
ARIA — Auditoría y Revisión Inteligente Automatizada
Sistema de auditoría automática de llamadas para Elena AI Voice Agent
Versión: 1.0.0 — 28 marzo 2026

Arquitectura:
  1. Fetch de llamadas desde Vapi API (últimas 25 horas)
  2. Auditoría con Claude 3.5 Sonnet (clasificación + análisis de calidad)
  3. Detección de discrepancias vs clasificación original en GHL
  4. Almacenamiento en Supabase
  5. Corrección automática en GHL (con aprobación de Juan)
  6. Reporte diario via Email

IMPORTANTE: Este script es completamente independiente de app.py.
No modifica ningún archivo de Elena. Solo lee datos de Vapi/GHL y escribe en Supabase.
"""

import os
import json
import logging
import requests
import smtplib
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from anthropic import Anthropic

# ============================================================
# CONFIGURACIÓN
# ============================================================

# Credenciales (en producción, leer desde variables de entorno de Render)
# Todas las credenciales se leen EXCLUSIVAMENTE desde variables de entorno de Render
# NUNCA hardcodear credenciales en el código
VAPI_API_KEY = os.environ["VAPI_API_KEY"]
VAPI_ASSISTANT_ID = os.environ["VAPI_ASSISTANT_ID"]
GHL_PIT = os.environ["GHL_PIT"]
GHL_LOCATION_ID = os.environ["GHL_LOCATION_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://subzlfzuzcyqyfrzszjb.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")  # Requerido para escritura en Supabase

# Configuración de notificaciones
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "vitusmediard@gmail.com")
ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP", "+17865533777")

# Configuración de auditoría
AUDIT_LOOKBACK_HOURS = int(os.getenv("AUDIT_LOOKBACK_HOURS", "25"))
AUDIT_BATCH_SIZE = int(os.getenv("AUDIT_BATCH_SIZE", "50"))
CONFIDENCE_THRESHOLD_CORRECTION = float(os.getenv("CONFIDENCE_THRESHOLD_CORRECTION", "0.85"))
ARIA_VERSION = "1.0.0"
AUDIT_MODEL = "claude-sonnet-4-5-20250929"  # Claude Sonnet 4.5 — mejor disponible en esta cuenta

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ARIA] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("aria")

# ============================================================
# CLIENTE ANTHROPIC
# ============================================================
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)


# ============================================================
# SUPABASE CLIENT (via REST API)
# ============================================================

def supabase_insert(table: str, data: dict) -> Optional[dict]:
    """Insertar un registro en Supabase via REST API."""
    if not SUPABASE_SERVICE_KEY:
        log.warning(f"SUPABASE_SERVICE_KEY no configurado — saltando inserción en {table}")
        return None
    
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        },
        json=data,
        timeout=10
    )
    if r.status_code in (200, 201):
        result = r.json()
        return result[0] if isinstance(result, list) else result
    else:
        log.error(f"Supabase insert error [{table}]: {r.status_code} — {r.text[:200]}")
        return None


def supabase_upsert(table: str, data: dict, on_conflict: str = "vapi_call_id") -> Optional[dict]:
    """Upsert un registro en Supabase."""
    if not SUPABASE_SERVICE_KEY:
        log.warning(f"SUPABASE_SERVICE_KEY no configurado — saltando upsert en {table}")
        return None
    
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": f"resolution=merge-duplicates,return=representation",
            "on_conflict": on_conflict
        },
        json=data,
        timeout=10
    )
    if r.status_code in (200, 201):
        result = r.json()
        return result[0] if isinstance(result, list) else result
    else:
        log.error(f"Supabase upsert error [{table}]: {r.status_code} — {r.text[:200]}")
        return None


def supabase_select(table: str, filters: dict = None, limit: int = 100) -> list:
    """Seleccionar registros de Supabase."""
    if not SUPABASE_SERVICE_KEY:
        return []
    
    params = {"limit": limit}
    if filters:
        for k, v in filters.items():
            params[k] = f"eq.{v}"
    
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        },
        params=params,
        timeout=10
    )
    if r.status_code == 200:
        return r.json()
    else:
        log.error(f"Supabase select error [{table}]: {r.status_code} — {r.text[:200]}")
        return []


def supabase_update(table: str, filters: dict, data: dict) -> bool:
    """Actualizar registros en Supabase."""
    if not SUPABASE_SERVICE_KEY:
        return False
    
    params = {}
    for k, v in filters.items():
        params[k] = f"eq.{v}"
    
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
        },
        params=params,
        json=data,
        timeout=10
    )
    return r.status_code in (200, 204)


# ============================================================
# VAPI API
# ============================================================

def fetch_vapi_calls(hours_back: int = 25, limit: int = 50) -> list:
    """
    Obtener llamadas de Vapi de las últimas N horas.
    Retorna lista de objetos de llamada.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    log.info(f"Fetching Vapi calls since {cutoff_str} (last {hours_back}h)")
    
    r = requests.get(
        "https://api.vapi.ai/call",
        headers={"Authorization": f"Bearer {VAPI_API_KEY}"},
        params={
            "limit": limit,
            "assistantId": VAPI_ASSISTANT_ID,
            "createdAtGt": cutoff_str
        },
        timeout=30
    )
    
    if r.status_code != 200:
        log.error(f"Vapi API error: {r.status_code} — {r.text[:200]}")
        return []
    
    calls = r.json()
    log.info(f"Fetched {len(calls)} calls from Vapi")
    return calls


def get_already_audited_ids() -> set:
    """Obtener IDs de llamadas ya auditadas en Supabase para evitar duplicados."""
    if not SUPABASE_SERVICE_KEY:
        return set()
    
    records = supabase_select("call_audits", limit=500)
    return {r["vapi_call_id"] for r in records if "vapi_call_id" in r}


# ============================================================
# GHL API
# ============================================================

def get_ghl_contact_fields(contact_id: str) -> dict:
    """
    Obtener los campos de Elena del contacto en GHL.
    Retorna dict con los campos elena_*.
    """
    r = requests.get(
        f"https://services.leadconnectorhq.com/contacts/{contact_id}",
        headers={
            "Authorization": f"Bearer {GHL_PIT}",
            "Version": "2021-07-28"
        },
        timeout=10
    )
    
    if r.status_code != 200:
        log.warning(f"GHL contact fetch error [{contact_id}]: {r.status_code}")
        return {}
    
    contact = r.json().get("contact", {})
    custom_fields = contact.get("customFields", [])
    
    # Extraer campos de Elena
    elena_fields = {}
    field_map = {
        "elena_last_outcome": "elena_last_outcome",
        "elena_call_duration": "elena_call_duration",
        "elena_ended_reason": "elena_ended_reason",
        "elena_success_eval": "elena_success_eval",
        "elena_summary": "elena_summary",
        "elena_appointment_id": "elena_appointment_id",
        "elena_appointment_date": "elena_appointment_date",
    }
    
    for field in custom_fields:
        field_key = field.get("fieldKey", "")
        for ghl_key, aria_key in field_map.items():
            if ghl_key in field_key:
                elena_fields[aria_key] = field.get("value")
    
    return elena_fields


def update_ghl_contact_outcome(contact_id: str, new_outcome: str) -> bool:
    """
    Actualizar el outcome de Elena en GHL.
    Solo se llama cuando ARIA detecta una discrepancia y Juan aprueba la corrección.
    """
    log.info(f"Updating GHL contact {contact_id} outcome to: {new_outcome}")
    
    r = requests.put(
        f"https://services.leadconnectorhq.com/contacts/{contact_id}",
        headers={
            "Authorization": f"Bearer {GHL_PIT}",
            "Version": "2021-07-28",
            "Content-Type": "application/json"
        },
        json={
            "customFields": [
                {"key": "elena_last_outcome", "field_value": new_outcome}
            ]
        },
        timeout=10
    )
    
    if r.status_code in (200, 201):
        log.info(f"GHL update successful for contact {contact_id}")
        return True
    else:
        log.error(f"GHL update failed [{contact_id}]: {r.status_code} — {r.text[:200]}")
        return False


# ============================================================
# ARIA AUDIT ENGINE — CLAUDE 3.5 SONNET
# ============================================================

ARIA_SYSTEM_PROMPT = """Eres ARIA, el sistema de auditoría de llamadas para Elena, una agente de IA de ventas de Laser Place Miami que agenda citas de Botox.

Tu trabajo es analizar transcripts de llamadas telefónicas y determinar:
1. Si la clasificación del outcome es correcta
2. Si Elena siguió el playbook correctamente
3. Qué errores cometió Elena (si los hay)
4. La calidad general de la conversación

## OUTCOMES POSIBLES:
- **agendo**: Se creó una cita exitosamente (create_booking o reschedule_appointment exitoso)
- **no_agendo**: Hubo conversación real pero no se agendó cita
- **no_contesto**: El cliente no contestó, buzón de voz, IVR, o conversación <20s sin contenido real
- **llamar_luego**: El cliente pidió que lo llamen después (schedule_callback exitoso)
- **error_tecnico**: Error técnico que impidió la llamada
- **no_interesado**: El cliente explícitamente rechazó el servicio

## REGLAS DE CLASIFICACIÓN:
1. Si hay un create_booking o reschedule_appointment exitoso en los tool calls → SIEMPRE es "agendo"
2. Si la llamada duró <20s o el cliente no habló → "no_contesto"
3. Si el cliente dijo frases de buzón de voz (inglés o español) → "no_contesto"
4. Si schedule_callback fue exitoso → "llamar_luego"
5. Si el cliente dijo "no me interesa", "no quiero", "no me llames más" → "no_interesado"
6. Todo lo demás con conversación real → "no_agendo"

## ERRORES DE ELENA A DETECTAR:
- **missed_close**: Tenía oportunidad de agendar pero no la aprovechó
- **wrong_info**: Dio información incorrecta (precio, disponibilidad, etc.)
- **playbook_violation**: No siguió el playbook (ej: ofreció precio antes de la evaluación)
- **premature_endcall**: Terminó la llamada cuando el cliente aún quería hablar
- **repeated_availability_check**: Llamó a check_availability más de 2 veces innecesariamente
- **language_switch**: El cliente habló en inglés pero Elena respondió en español (o viceversa)
- **confusion_created**: Elena confundió al cliente con información contradictoria

## PLAYBOOK DE ELENA (resumen):
1. Saludo → preguntar si tiene 2 minutos
2. Preguntar si los martes funcionan (día preferido de la clínica)
3. Si no → ofrecer otros días disponibles
4. check_availability → presentar 2 opciones máximo
5. Cuando el cliente elige → create_booking
6. Si el cliente pregunta precio → explicar que se personaliza, invitar a la evaluación gratuita
7. Despedida con confirmación de la cita

## FORMATO DE RESPUESTA:
Responde SIEMPRE en JSON válido con esta estructura exacta:
{
  "correct_outcome": "agendo|no_agendo|no_contesto|llamar_luego|error_tecnico|no_interesado",
  "confidence": 0.0-1.0,
  "reasoning": "Explicación breve de por qué este es el outcome correcto",
  "playbook_adherence_score": 0.0-1.0,
  "errors_detected": [
    {
      "type": "tipo_de_error",
      "description": "descripción específica",
      "severity": "low|medium|high",
      "timestamp_approx": "momento en la conversación"
    }
  ],
  "silence_detected": true|false,
  "language_switch_detected": true|false,
  "appointment_offered": true|false,
  "objection_handled": true|false,
  "quality_notes": "Notas adicionales sobre la calidad de la llamada"
}"""


def audit_call_with_claude(call_data: dict) -> dict:
    """
    Auditar una llamada usando Claude 3.5 Sonnet.
    Retorna el resultado del análisis.
    """
    call_id = call_data.get("id", "unknown")
    transcript = call_data.get("transcript", "") or ""
    summary = call_data.get("summary", "") or ""
    ended_reason = call_data.get("endedReason", "") or ""
    started_at = call_data.get("startedAt", "")
    ended_at = call_data.get("endedAt", "")
    
    # Calcular duración
    duration_seconds = None
    if started_at and ended_at:
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            duration_seconds = int((end - start).total_seconds())
        except Exception:
            pass
    
    # Extraer tool calls del artifact
    artifact = call_data.get("artifact", {}) or {}
    messages = call_data.get("messages", []) or []
    
    tool_calls_summary = []
    for msg in messages:
        if msg.get("role") == "tool_calls":
            for tc in msg.get("toolCalls", []):
                fn = tc.get("function", {})
                tool_calls_summary.append({
                    "name": fn.get("name"),
                    "args": fn.get("arguments", "{}")
                })
        elif msg.get("role") == "tool_call_result":
            # Agregar resultado al último tool call
            if tool_calls_summary:
                result_str = str(msg.get("result", ""))[:300]
                tool_calls_summary[-1]["result"] = result_str
    
    # Construir el prompt de auditoría
    user_prompt = f"""Analiza esta llamada de Elena y determina el outcome correcto.

## DATOS DE LA LLAMADA:
- ID: {call_id}
- Duración: {duration_seconds}s
- Razón de fin: {ended_reason}
- Inicio: {started_at}

## TRANSCRIPT:
{transcript[:3000] if transcript else "(sin transcript)"}

## RESUMEN GENERADO POR VAPI:
{summary[:500] if summary else "(sin resumen)"}

## TOOL CALLS EJECUTADOS:
{json.dumps(tool_calls_summary, ensure_ascii=False, indent=2)[:2000] if tool_calls_summary else "(ninguno)"}

Analiza todo lo anterior y responde en JSON con el formato especificado."""

    try:
        response = anthropic_client.messages.create(
            model=AUDIT_MODEL,
            max_tokens=1024,
            system=ARIA_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        response_text = response.content[0].text.strip()
        
        # Extraer JSON de la respuesta
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        audit_result = json.loads(response_text)
        audit_result["duration_seconds"] = duration_seconds
        audit_result["call_id"] = call_id
        
        log.info(f"Audit complete [{call_id}]: outcome={audit_result.get('correct_outcome')} confidence={audit_result.get('confidence'):.2f}")
        return audit_result
        
    except json.JSONDecodeError as e:
        log.error(f"JSON parse error for call {call_id}: {e}")
        log.error(f"Response was: {response_text[:500]}")
        return {
            "correct_outcome": None,
            "confidence": 0.0,
            "reasoning": f"Error parsing Claude response: {str(e)}",
            "errors_detected": [],
            "playbook_adherence_score": None,
            "call_id": call_id,
            "duration_seconds": duration_seconds
        }
    except Exception as e:
        log.error(f"Claude audit error for call {call_id}: {e}")
        return {
            "correct_outcome": None,
            "confidence": 0.0,
            "reasoning": f"Error: {str(e)}",
            "errors_detected": [],
            "playbook_adherence_score": None,
            "call_id": call_id,
            "duration_seconds": duration_seconds
        }


# ============================================================
# PROCESO PRINCIPAL DE AUDITORÍA
# ============================================================

def process_call(call_data: dict, already_audited: set) -> Optional[dict]:
    """
    Procesar una llamada individual:
    1. Verificar si ya fue auditada
    2. Obtener datos del contacto en GHL
    3. Auditar con Claude
    4. Comparar con clasificación original
    5. Guardar en Supabase
    6. Si hay discrepancia, registrar corrección pendiente
    """
    call_id = call_data.get("id")
    
    if call_id in already_audited:
        log.debug(f"Skipping already audited call: {call_id}")
        return None
    
    # Solo procesar llamadas terminadas
    if call_data.get("status") != "ended":
        return None
    
    log.info(f"Processing call: {call_id}")
    
    # Obtener datos del contacto en GHL
    customer = call_data.get("customer", {}) or {}
    phone = customer.get("number", "")
    ghl_contact_id = None
    original_outcome = None
    
    # Intentar obtener el contacto de GHL via el transcript/messages
    # (el contactId se guarda en el tool call result de get_contact)
    messages = call_data.get("messages", []) or []
    for msg in messages:
        if msg.get("role") == "tool_call_result" and msg.get("name") == "get_contact":
            result_str = msg.get("result", "{}")
            try:
                result = json.loads(result_str) if isinstance(result_str, str) else result_str
                if result.get("found"):
                    ghl_contact_id = result.get("contactId")
                    break
            except Exception:
                pass
    
    # Si tenemos el contactId, obtener los campos de Elena
    ghl_fields = {}
    if ghl_contact_id:
        ghl_fields = get_ghl_contact_fields(ghl_contact_id)
        original_outcome = ghl_fields.get("elena_last_outcome")
    
    # Auditar con Claude
    audit_result = audit_call_with_claude(call_data)
    
    aria_outcome = audit_result.get("correct_outcome")
    aria_confidence = audit_result.get("confidence", 0.0)
    
    # Determinar si hay discrepancia
    has_discrepancy = (
        original_outcome is not None and
        aria_outcome is not None and
        original_outcome != aria_outcome and
        aria_confidence >= CONFIDENCE_THRESHOLD_CORRECTION
    )
    
    # Determinar estado de la auditoría
    if has_discrepancy:
        audit_status = "discrepancy_found"
        log.warning(f"DISCREPANCY [{call_id}]: original={original_outcome} aria={aria_outcome} confidence={aria_confidence:.2f}")
    else:
        audit_status = "audited"
    
    # Calcular duración
    duration_seconds = audit_result.get("duration_seconds")
    
    # Preparar registro para Supabase
    started_at = call_data.get("startedAt")
    ended_at = call_data.get("endedAt")
    
    audit_record = {
        "vapi_call_id": call_id,
        "ghl_contact_id": ghl_contact_id,
        "phone_number": phone,
        "agent_name": "elena",
        "call_started_at": started_at,
        "call_ended_at": ended_at,
        "call_duration_seconds": duration_seconds,
        "original_outcome": original_outcome,
        "original_ended_reason": call_data.get("endedReason"),
        "original_success_eval": ghl_fields.get("elena_success_eval"),
        "original_summary": call_data.get("summary"),
        "aria_outcome": aria_outcome,
        "aria_confidence": aria_confidence,
        "aria_reasoning": audit_result.get("reasoning"),
        "audit_status": audit_status,
        "playbook_adherence_score": audit_result.get("playbook_adherence_score"),
        "silence_detected": audit_result.get("silence_detected", False),
        "language_switch_detected": audit_result.get("language_switch_detected", False),
        "objection_handled": audit_result.get("objection_handled"),
        "appointment_offered": audit_result.get("appointment_offered"),
        "errors_detected": audit_result.get("errors_detected", []),
        "transcript_text": call_data.get("transcript", "")[:5000] if call_data.get("transcript") else None,
        "audio_url": call_data.get("recordingUrl"),
        "audit_model": AUDIT_MODEL,
        "audit_version": ARIA_VERSION,
        "raw_vapi_data": {
            "id": call_id,
            "endedReason": call_data.get("endedReason"),
            "status": call_data.get("status"),
            "cost": call_data.get("cost"),
        }
    }
    
    # Guardar en Supabase
    saved = supabase_upsert("call_audits", audit_record)
    
    if saved and has_discrepancy and ghl_contact_id:
        # Registrar corrección pendiente
        correction_record = {
            "audit_id": saved.get("id"),
            "vapi_call_id": call_id,
            "ghl_contact_id": ghl_contact_id,
            "field_name": "elena_last_outcome",
            "old_value": original_outcome,
            "new_value": aria_outcome,
            "correction_status": "pending"
        }
        supabase_insert("aria_corrections", correction_record)
    
    return {
        "call_id": call_id,
        "original_outcome": original_outcome,
        "aria_outcome": aria_outcome,
        "aria_confidence": aria_confidence,
        "has_discrepancy": has_discrepancy,
        "audit_status": audit_status,
        "errors_count": len(audit_result.get("errors_detected", [])),
        "playbook_score": audit_result.get("playbook_adherence_score"),
        "duration_seconds": duration_seconds,
        "ghl_contact_id": ghl_contact_id,
        "quality_notes": audit_result.get("quality_notes", "")
    }


# ============================================================
# MÉTRICAS DIARIAS
# ============================================================

def calculate_daily_metrics(results: list, audit_date: str) -> dict:
    """Calcular métricas agregadas del día."""
    if not results:
        return {}
    
    total = len(results)
    outcomes = {}
    for r in results:
        o = r.get("original_outcome") or "unknown"
        outcomes[o] = outcomes.get(o, 0) + 1
    
    agendo = outcomes.get("agendo", 0)
    no_contesto = outcomes.get("no_contesto", 0)
    discrepancies = sum(1 for r in results if r.get("has_discrepancy"))
    errors_total = sum(r.get("errors_count", 0) for r in results)
    
    durations = [r.get("duration_seconds") for r in results if r.get("duration_seconds")]
    avg_duration = sum(durations) / len(durations) if durations else 0
    
    playbook_scores = [r.get("playbook_score") for r in results if r.get("playbook_score") is not None]
    avg_playbook = sum(playbook_scores) / len(playbook_scores) if playbook_scores else None
    
    conversion_rate = agendo / total if total > 0 else 0
    contact_rate = (total - no_contesto) / total if total > 0 else 0
    
    metrics = {
        "metric_date": audit_date,
        "agent_name": "elena",
        "total_calls": total,
        "calls_agendo": agendo,
        "calls_no_agendo": outcomes.get("no_agendo", 0),
        "calls_no_contesto": no_contesto,
        "calls_llamar_luego": outcomes.get("llamar_luego", 0),
        "calls_error_tecnico": outcomes.get("error_tecnico", 0),
        "calls_no_interesado": outcomes.get("no_interesado", 0),
        "conversion_rate": round(conversion_rate, 4),
        "contact_rate": round(contact_rate, 4),
        "avg_call_duration_seconds": round(avg_duration, 1),
        "avg_playbook_adherence": round(avg_playbook, 3) if avg_playbook else None,
        "calls_with_errors": sum(1 for r in results if r.get("errors_count", 0) > 0),
        "aria_discrepancies_found": discrepancies,
        "report_generated_at": datetime.now(timezone.utc).isoformat()
    }
    
    return metrics


# ============================================================
# REPORTE DIARIO
# ============================================================

def build_report_text(results: list, metrics: dict, audit_date: str) -> str:
    """Construir el texto del reporte diario."""
    
    total = metrics.get("total_calls", 0)
    agendo = metrics.get("calls_agendo", 0)
    no_agendo = metrics.get("calls_no_agendo", 0)
    no_contesto = metrics.get("calls_no_contesto", 0)
    llamar_luego = metrics.get("calls_llamar_luego", 0)
    error_tecnico = metrics.get("calls_error_tecnico", 0)
    no_interesado = metrics.get("calls_no_interesado", 0)
    conversion = metrics.get("conversion_rate", 0) * 100
    contact_rate = metrics.get("contact_rate", 0) * 100
    discrepancies = metrics.get("aria_discrepancies_found", 0)
    avg_duration = metrics.get("avg_call_duration_seconds", 0)
    avg_playbook = metrics.get("avg_playbook_adherence")
    
    # Llamadas con discrepancias
    discrepancy_details = [r for r in results if r.get("has_discrepancy")]
    
    # Errores más frecuentes
    all_errors = []
    for r in results:
        all_errors.extend(r.get("errors_detected_types", []))
    
    report = f"""
╔══════════════════════════════════════════════════════════╗
║         ARIA — REPORTE DIARIO DE ELENA                  ║
║         Fecha: {audit_date}                             
╚══════════════════════════════════════════════════════════╝

📊 RESUMEN EJECUTIVO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total llamadas auditadas:  {total}
Tasa de conversión:        {conversion:.1f}% ({agendo} citas agendadas)
Tasa de contacto:          {contact_rate:.1f}%
Duración promedio:         {avg_duration:.0f}s
Playbook adherence:        {f"{avg_playbook*100:.0f}%" if avg_playbook else "N/A"}

📋 DISTRIBUCIÓN DE OUTCOMES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Agendó:          {agendo:3d}  ({agendo/total*100:.0f}% del total)
💬 No agendó:       {no_agendo:3d}  ({no_agendo/total*100:.0f}% del total)
📵 No contestó:     {no_contesto:3d}  ({no_contesto/total*100:.0f}% del total)
📅 Llamar luego:    {llamar_luego:3d}  ({llamar_luego/total*100:.0f}% del total)
🚫 No interesado:   {no_interesado:3d}  ({no_interesado/total*100:.0f}% del total)
⚠️  Error técnico:   {error_tecnico:3d}  ({error_tecnico/total*100:.0f}% del total)
""" if total > 0 else f"\n📊 Sin llamadas para auditar en {audit_date}\n"

    if discrepancies > 0:
        report += f"""
🔍 CORRECCIONES DE ARIA ({discrepancies} discrepancias detectadas)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(discrepancy_details[:10], 1):
            report += f"""
{i}. Llamada: {d.get('call_id', 'N/A')[:20]}...
   Clasificado como: {d.get('original_outcome', 'N/A')}
   ARIA propone:     {d.get('aria_outcome', 'N/A')} (confianza: {d.get('aria_confidence', 0)*100:.0f}%)
   Contacto GHL:     {d.get('ghl_contact_id', 'N/A')}
"""
    
    report += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generado por ARIA v{ARIA_VERSION} a las {datetime.now().strftime('%H:%M')} EST
Para ver el detalle completo, consulta el dashboard de Supabase.
"""
    
    return report


def send_email_report(report_text: str, audit_date: str, metrics: dict):
    """
    Enviar reporte diario por email usando Gmail SMTP con App Password.
    Variables de entorno requeridas:
      - GMAIL_FROM: dirección de Gmail remitente (ej: vitusmediard@gmail.com)
      - GMAIL_APP_PASSWORD: App Password de 16 caracteres generada en myaccount.google.com/apppasswords
      - ADMIN_EMAIL: destinatario del reporte
    """
    # Siempre loggear el reporte en los logs de Render
    log.info("=" * 60)
    log.info("REPORTE DIARIO ARIA")
    log.info("=" * 60)
    log.info(report_text)
    log.info("=" * 60)

    gmail_from = os.getenv("GMAIL_FROM", "")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", "")

    if not gmail_from or not gmail_app_password:
        log.warning("Email delivery skipped: GMAIL_FROM o GMAIL_APP_PASSWORD no configurados")
        return False

    try:
        total        = metrics.get("total_audited", 0)
        agendo       = metrics.get("calls_agendo", 0)
        conversion   = metrics.get("conversion_rate", 0) * 100
        avg_playbook = metrics.get("avg_playbook_adherence")
        pb_str       = f"{avg_playbook*100:.0f}%" if avg_playbook else "N/A"

        # ── Asunto dinámico con KPIs clave ──────────────────────────────
        subject = (
            f"ARIA | Elena {audit_date} — "
            f"{total} llamadas | {agendo} citas | "
            f"Conversión {conversion:.1f}% | Playbook {pb_str}"
        )

        # ── Cuerpo HTML ─────────────────────────────────────────────────
        html_body = f"""\
<html><body style="font-family:monospace;background:#0f0f0f;color:#e0e0e0;padding:24px">
<h2 style="color:#00d4aa">ARIA — Reporte Diario de Elena</h2>
<p style="color:#888">Fecha: {audit_date}</p>
<pre style="background:#1a1a1a;padding:16px;border-radius:8px;font-size:13px;line-height:1.6">{report_text}</pre>
<hr style="border-color:#333">
<p style="color:#555;font-size:11px">Generado automáticamente por ARIA v{ARIA_VERSION}.<br>
Para ver el detalle completo, consulta el <a href="https://supabase.com/dashboard/project/subzlfzuzcyqyfrzszjb/editor" style="color:#00d4aa">dashboard de Supabase</a>.</p>
</body></html>"""

        # ── Construir mensaje MIME ───────────────────────────────────────
        msg = MIMEMultipart("alternative")
        msg["From"]    = f"ARIA — Elena Monitor <{gmail_from}>"
        msg["To"]      = ADMIN_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(report_text, "plain",  "utf-8"))
        msg.attach(MIMEText(html_body,   "html",   "utf-8"))

        # ── Enviar via Gmail SMTP SSL ────────────────────────────────────
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_from, gmail_app_password)
            server.send_message(msg)

        log.info(f"Email report sent to {ADMIN_EMAIL} — subject: {subject}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        log.error(f"Gmail auth error (verifica GMAIL_APP_PASSWORD): {e}")
        return False
    except Exception as e:
        log.error(f"Email send error: {e}")
        return False


# ============================================================
# PUNTO DE ENTRADA PRINCIPAL
# ============================================================

def run_audit(hours_back: int = None, dry_run: bool = False):
    """
    Ejecutar el ciclo completo de auditoría.
    
    Args:
        hours_back: Horas hacia atrás para buscar llamadas (default: AUDIT_LOOKBACK_HOURS)
        dry_run: Si True, no guarda en Supabase ni modifica GHL
    """
    if hours_back is None:
        hours_back = AUDIT_LOOKBACK_HOURS
    
    audit_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info(f"Starting ARIA audit run — date={audit_date} hours_back={hours_back} dry_run={dry_run}")
    
    # 1. Obtener llamadas de Vapi
    calls = fetch_vapi_calls(hours_back=hours_back, limit=AUDIT_BATCH_SIZE)
    
    if not calls:
        log.info("No calls to audit")
        return {"total": 0, "audited": 0, "discrepancies": 0}
    
    # 2. Obtener IDs ya auditados
    already_audited = get_already_audited_ids() if not dry_run else set()
    
    new_calls = [c for c in calls if c.get("id") not in already_audited]
    log.info(f"New calls to audit: {len(new_calls)} (skipping {len(calls) - len(new_calls)} already audited)")
    
    if not new_calls:
        log.info("All calls already audited")
        return {"total": len(calls), "audited": 0, "discrepancies": 0}
    
    # 3. Auditar cada llamada
    results = []
    discrepancies = []
    
    for i, call in enumerate(new_calls):
        log.info(f"Auditing call {i+1}/{len(new_calls)}: {call.get('id')}")
        
        if dry_run:
            # En dry_run, solo auditar sin guardar
            audit_result = audit_call_with_claude(call)
            result = {
                "call_id": call.get("id"),
                "original_outcome": None,
                "aria_outcome": audit_result.get("correct_outcome"),
                "aria_confidence": audit_result.get("confidence", 0),
                "has_discrepancy": False,
                "audit_status": "dry_run",
                "errors_count": len(audit_result.get("errors_detected", [])),
                "playbook_score": audit_result.get("playbook_adherence_score"),
                "duration_seconds": audit_result.get("duration_seconds"),
                "quality_notes": audit_result.get("quality_notes", "")
            }
        else:
            result = process_call(call, already_audited)
        
        if result:
            results.append(result)
            if result.get("has_discrepancy"):
                discrepancies.append(result)
    
    # 4. Calcular métricas del día
    metrics = calculate_daily_metrics(results, audit_date)
    
    if not dry_run and metrics:
        supabase_upsert("daily_metrics", metrics, on_conflict="metric_date,agent_name")
    
    # 5. Generar y enviar reporte
    report_text = build_report_text(results, metrics, audit_date)
    
    if not dry_run:
        send_email_report(report_text, audit_date, metrics)
    
    # 6. Resumen final
    summary = {
        "total_fetched": len(calls),
        "new_audited": len(results),
        "discrepancies_found": len(discrepancies),
        "conversion_rate": metrics.get("conversion_rate", 0),
        "avg_playbook_score": metrics.get("avg_playbook_adherence"),
        "audit_date": audit_date
    }
    
    log.info(f"Audit complete: {summary}")
    return summary


def run_pilot(call_ids: list = None, n_calls: int = 3):
    """
    Ejecutar una auditoría piloto en N llamadas recientes.
    Muestra los resultados sin guardar en Supabase.
    Útil para testing y validación.
    """
    log.info(f"Running ARIA pilot audit on {n_calls} recent calls...")
    
    if call_ids:
        # Auditar llamadas específicas
        calls = []
        for call_id in call_ids:
            r = requests.get(
                f"https://api.vapi.ai/call/{call_id}",
                headers={"Authorization": f"Bearer {VAPI_API_KEY}"},
                timeout=15
            )
            if r.status_code == 200:
                calls.append(r.json())
    else:
        # Obtener las N llamadas más recientes con conversación real
        all_calls = fetch_vapi_calls(hours_back=72, limit=50)
        calls = [c for c in all_calls if len(c.get("transcript", "") or "") > 200][:n_calls]
    
    if not calls:
        log.warning("No calls found for pilot")
        return []
    
    log.info(f"Auditing {len(calls)} calls in pilot mode...")
    
    pilot_results = []
    for call in calls:
        call_id = call.get("id")
        log.info(f"\n{'='*60}")
        log.info(f"PILOT AUDIT: {call_id}")
        log.info(f"Ended reason: {call.get('endedReason')}")
        log.info(f"Duration: ~{len(call.get('transcript', '') or '')} chars transcript")
        
        audit_result = audit_call_with_claude(call)
        
        log.info(f"ARIA says: {audit_result.get('correct_outcome')} (confidence: {audit_result.get('confidence', 0)*100:.0f}%)")
        log.info(f"Reasoning: {audit_result.get('reasoning', '')[:200]}")
        log.info(f"Errors detected: {len(audit_result.get('errors_detected', []))}")
        log.info(f"Playbook score: {audit_result.get('playbook_adherence_score')}")
        
        if audit_result.get("errors_detected"):
            for err in audit_result["errors_detected"]:
                log.info(f"  ERROR [{err.get('severity', 'N/A')}]: {err.get('type')} — {err.get('description', '')[:100]}")
        
        pilot_results.append({
            "call_id": call_id,
            "transcript_preview": (call.get("transcript", "") or "")[:200],
            "audit": audit_result
        })
    
    return pilot_results


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys
    
    mode = sys.argv[1] if len(sys.argv) > 1 else "audit"
    
    if mode == "pilot":
        # Modo piloto: auditar 3 llamadas recientes sin guardar
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        results = run_pilot(n_calls=n)
        print(f"\nPilot complete: {len(results)} calls audited")
        
    elif mode == "audit":
        # Modo normal: auditar todas las llamadas nuevas
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else AUDIT_LOOKBACK_HOURS
        summary = run_audit(hours_back=hours)
        print(f"\nAudit complete: {summary}")
        
    elif mode == "dry-run":
        # Modo dry-run: auditar sin guardar en Supabase
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        summary = run_audit(hours_back=hours, dry_run=True)
        print(f"\nDry-run complete: {summary}")
        
    elif mode == "report":
        # Generar reporte del día sin auditar
        calls = fetch_vapi_calls(hours_back=25)
        audit_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        metrics = calculate_daily_metrics([], audit_date)
        report = build_report_text([], metrics, audit_date)
        print(report)
        
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python3.11 aria_audit.py [pilot|audit|dry-run|report] [hours_back|n_calls]")
        sys.exit(1)
