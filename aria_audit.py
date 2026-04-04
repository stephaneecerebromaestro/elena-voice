"""
ARIA — Auditoría y Revisión Inteligente Automatizada v3.1.1
Sistema de auditoría automática de llamadas para Elena AI Voice Agent
Fecha: 1 abril 2026

CAMBIOS v3.1.1:
  FIX /tendencia: requests directo con key explícita (bypass importlib.reload en threads)
  NUEVO: /backfill — pobla call_intelligence con llamadas históricas (152 llamadas ~$0.30-0.50)
  MEJORA /intel: fallback accionable con conteos reales cuando call_intelligence está vacío
  MEJORA /leads: usa call_intelligence cuando existe, fallback a call_audits con nota de backfill

CAMBIOS v3.0:
  BUG FIX #1: /errores usaba isoformat() → Supabase rechazaba → siempre "Sin errores"
              Fix: strftime("%Y-%m-%dT%H:%M:%SZ") en TODAS las queries con cutoff
  BUG FIX #2: calculate_daily_metrics usaba original_outcome (GHL, 18.6% None)
              Fix: usa aria_outcome como fuente primaria
  BUG FIX #3: /score usaba limit=200 → truncaba días con >200 llamadas
              Fix: limit=500 en todas las queries de métricas
  BUG FIX #4: /audit reportaba "0 llamadas procesadas" cuando todo estaba al día
              Fix: muestra "Nuevas: X | Ya auditadas: Y | Sin transcript: Z"
  NUEVO: Llamadas sin transcript registradas como no_contesto automático (cobertura 45%→100%)
  NUEVO: Vapi como fuente primaria de totales en todos los reportes
  NUEVO: Tabla call_intelligence con inteligencia de cliente por llamada
  NUEVO: Análisis de patrones en /audit 7d y /audit mes con Claude
  NUEVO: Comandos /reporte hoy|2d|7d|mes, /audit 24h|7d|mes
  NUEVO: Comandos /intel, /leads calientes, /status, /contacto, /tendencia, /ayuda
"""

import os
import json
import logging
import requests
import smtplib
import threading as _threading
import time as _time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from anthropic import Anthropic

# ============================================================
# CONFIGURACIÓN
# ============================================================

VAPI_API_KEY = os.getenv("VAPI_API_KEY", "")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
GHL_PIT = os.getenv("GHL_PIT", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://subzlfzuzcyqyfrzszjb.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "vitusmediard@gmail.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
RENDER_SERVER_URL = os.getenv("RENDER_SERVER_URL", "https://elena-pdem.onrender.com")

AUDIT_LOOKBACK_HOURS = int(os.getenv("AUDIT_LOOKBACK_HOURS", "25"))
AUDIT_BATCH_SIZE = int(os.getenv("AUDIT_BATCH_SIZE", "50"))
CONFIDENCE_THRESHOLD_CORRECTION = float(os.getenv("CONFIDENCE_THRESHOLD_CORRECTION", "0.85"))
ARIA_VERSION = "3.1.1"
AUDIT_MODEL = "claude-sonnet-4-5"

_calls_in_progress: set = set()
_calls_in_progress_lock = _threading.Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ARIA] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("aria")

anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

OUTCOME_LABELS = {
    "agendo": "✅ Agendó",
    "no_agendo": "📋 No agendó",
    "no_contesto": "📵 No contestó",
    "llamar_luego": "🔄 Llamar luego",
    "error_tecnico": "⚙️ Error técnico",
    "no_interesado": "🚫 No interesado",
    "numero_invalido": "🚫 Número inválido",
}

# ============================================================
# HELPERS DE FECHA — FIX #1: usar strftime, NUNCA isoformat()
# ============================================================

def _utc_cutoff(days: int = 0, hours: int = 0) -> str:
    """Retorna timestamp UTC en formato que Supabase acepta. FIX #1."""
    dt = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _edt_day_range(days_ago: int = 0):
    """
    Retorna (utc_start, utc_end) para un día calendario EDT.
    days_ago=0 → hoy, days_ago=1 → ayer.
    """
    import pytz
    edt = pytz.timezone("America/New_York")
    now_edt = datetime.now(edt)
    target = now_edt - timedelta(days=days_ago)
    day_start = target.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    utc_start = day_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    utc_end = day_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return utc_start, utc_end


def _edt_month_range(month_name: str = None):
    """
    Retorna (utc_start, utc_end, label) para un mes por nombre.
    month_name=None → mes en curso.
    """
    import pytz
    import calendar
    edt = pytz.timezone("America/New_York")
    now_edt = datetime.now(edt)

    MONTH_NAMES = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    MONTH_LABELS = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
    }

    if month_name:
        month_num = MONTH_NAMES.get(month_name.lower().strip())
        if not month_num:
            return None, None, f"Mes no reconocido: {month_name}"
        year = now_edt.year if month_num <= now_edt.month else now_edt.year - 1
    else:
        month_num = now_edt.month
        year = now_edt.year

    last_day = calendar.monthrange(year, month_num)[1]
    month_start = edt.localize(datetime(year, month_num, 1, 0, 0, 0))
    month_end = edt.localize(datetime(year, month_num, last_day, 23, 59, 59))
    utc_start = month_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    utc_end = month_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    label = f"{MONTH_LABELS[month_num]} {year}"
    return utc_start, utc_end, label


# ============================================================
# SUPABASE CLIENT
# ============================================================

def _get_supa_headers():
    key = os.environ.get("SUPABASE_SERVICE_KEY") or SUPABASE_SERVICE_KEY
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }, key


def supabase_insert(table: str, data: dict) -> Optional[dict]:
    headers, key = _get_supa_headers()
    if not key:
        log.warning(f"SUPABASE_SERVICE_KEY no configurado — saltando inserción en {table}")
        return None
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**headers, "Prefer": "return=representation"},
        json=data, timeout=10
    )
    if r.status_code in (200, 201):
        result = r.json()
        return result[0] if isinstance(result, list) else result
    log.error(f"Supabase insert error [{table}]: {r.status_code} — {r.text[:200]}")
    return None


def supabase_upsert(table: str, data: dict, on_conflict: str = "vapi_call_id") -> Optional[dict]:
    headers, key = _get_supa_headers()
    if not key:
        return None
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**headers, "Prefer": f"resolution=merge-duplicates,return=representation", "on_conflict": on_conflict},
        json=data, timeout=10
    )
    if r.status_code in (200, 201):
        result = r.json()
        return result[0] if isinstance(result, list) else result
    log.error(f"Supabase upsert error [{table}]: {r.status_code} — {r.text[:200]}")
    return None


def supabase_query(table: str, query_string: str) -> list:
    """FIX #1: Todas las queries de fecha usan _utc_cutoff() que genera formato correcto."""
    headers, key = _get_supa_headers()
    if not key:
        return []
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}?{query_string}",
        headers=headers, timeout=15
    )
    if r.status_code == 200:
        return r.json()
    log.error(f"Supabase query error [{table}]: {r.status_code} — {r.text[:200]}")
    return []


def supabase_update(table: str, filters: dict, data: dict) -> bool:
    headers, key = _get_supa_headers()
    if not key:
        return False
    params = {k: f"eq.{v}" for k, v in filters.items()}
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=headers, params=params, json=data, timeout=10
    )
    return r.status_code in (200, 204)


def supabase_select(table: str, filters: dict = None, limit: int = 100) -> list:
    headers, key = _get_supa_headers()
    if not key:
        return []
    params = {"limit": limit}
    if filters:
        for k, v in filters.items():
            params[k] = f"eq.{v}"
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=headers, params=params, timeout=10)
    if r.status_code == 200:
        return r.json()
    log.error(f"Supabase select error [{table}]: {r.status_code} — {r.text[:200]}")
    return []


# ============================================================
# VAPI API
# ============================================================

def fetch_vapi_calls(hours_back: int = 25, limit: int = 500) -> list:
    """Obtener llamadas de Vapi. FIX #3: limit default 500."""
    _vapi_key = os.environ.get("VAPI_API_KEY") or VAPI_API_KEY
    _assistant_id = os.environ.get("VAPI_ASSISTANT_ID") or VAPI_ASSISTANT_ID
    cutoff_str = _utc_cutoff(hours=hours_back).replace("Z", ".000Z")
    r = requests.get(
        "https://api.vapi.ai/call",
        headers={"Authorization": f"Bearer {_vapi_key}"},
        params={"limit": limit, "assistantId": _assistant_id, "createdAtGt": cutoff_str},
        timeout=30
    )
    if r.status_code != 200:
        log.error(f"Vapi API error: {r.status_code} — {r.text[:200]}")
        return []
    calls = r.json()
    log.info(f"Fetched {len(calls)} calls from Vapi")
    return calls


def fetch_vapi_calls_range(utc_start: str, utc_end: str, limit: int = 500) -> list:
    """Obtener llamadas de Vapi en un rango de fechas específico."""
    _vapi_key = os.environ.get("VAPI_API_KEY") or VAPI_API_KEY
    _assistant_id = os.environ.get("VAPI_ASSISTANT_ID") or VAPI_ASSISTANT_ID
    vapi_start = utc_start.replace("Z", ".000Z")
    vapi_end = utc_end.replace("Z", ".000Z")
    r = requests.get(
        "https://api.vapi.ai/call",
        headers={"Authorization": f"Bearer {_vapi_key}"},
        params={"limit": limit, "assistantId": _assistant_id, "createdAtGt": vapi_start, "createdAtLt": vapi_end},
        timeout=30
    )
    if r.status_code != 200:
        log.error(f"Vapi range fetch error: {r.status_code}")
        return []
    return r.json()


def fetch_vapi_call_by_id(call_id: str) -> Optional[dict]:
    _vapi_key = os.environ.get("VAPI_API_KEY") or VAPI_API_KEY
    r = requests.get(
        f"https://api.vapi.ai/call/{call_id}",
        headers={"Authorization": f"Bearer {_vapi_key}"},
        timeout=15
    )
    if r.status_code == 200:
        return r.json()
    log.error(f"Vapi call fetch error [{call_id}]: {r.status_code}")
    return None


def get_already_audited_ids(limit: int = 1000) -> set:
    """FIX #3: limit=1000 para no truncar."""
    records = supabase_select("call_audits", limit=limit)
    return {r["vapi_call_id"] for r in records if "vapi_call_id" in r}


def get_audited_ids_in_range(utc_start: str, utc_end: str) -> set:
    records = supabase_query(
        "call_audits",
        f"created_at=gte.{utc_start}&created_at=lt.{utc_end}&select=vapi_call_id&limit=1000"
    )
    return {r.get("vapi_call_id") for r in records if r.get("vapi_call_id")}


# ============================================================
# GHL API
# ============================================================

def get_ghl_contact_id_by_phone(phone: str) -> Optional[str]:
    _ghl_pit = os.environ.get("GHL_PIT") or GHL_PIT
    _location_id = os.environ.get("GHL_LOCATION_ID") or GHL_LOCATION_ID
    if not phone:
        return None
    try:
        r = requests.post(
            "https://services.leadconnectorhq.com/contacts/search",
            headers={"Authorization": f"Bearer {_ghl_pit}", "Version": "2021-07-28", "Content-Type": "application/json"},
            json={"locationId": _location_id, "filters": [{"field": "phone", "operator": "eq", "value": phone}], "pageLimit": 1},
            timeout=10
        )
        if r.status_code == 200:
            contacts = r.json().get("contacts", [])
            if contacts:
                return contacts[0].get("id")
    except Exception as e:
        log.warning(f"GHL phone search exception [{phone}]: {e}")
    return None


def get_ghl_contact_fields(contact_id: str) -> dict:
    _ghl_pit = os.environ.get("GHL_PIT") or GHL_PIT
    r = requests.get(
        f"https://services.leadconnectorhq.com/contacts/{contact_id}",
        headers={"Authorization": f"Bearer {_ghl_pit}", "Version": "2021-07-28"},
        timeout=10
    )
    if r.status_code != 200:
        return {}
    contact = r.json().get("contact", {})
    custom_fields = contact.get("customFields", [])
    ELENA_FIELD_IDS = {
        "ibrHOJBAON7gQpj9rT89": "elena_last_outcome",
        "oAs5Oga4qS7lGo0Kgt0S": "elena_call_duration",
        "z5E3DfytuVmJBy9QXCvD": "elena_ended_reason",
        "KbBNpjKFL3SErALyTFcM": "elena_success_eval",
        "cCd44bHm90pAn5q9fmux": "elena_summary",
        "Bb3FVz9jnWIbZkbjCDSw": "elena_vapi_call_id",
        "eQJVvxl128xm1P7LEo3v": "elena_outcome_display",
        "PudkAK9CqOKbDefRrCEF": "elena_stage",
        "s8beSvYXNMtzJRFENIUH": "elena_total_calls",
        "X0eYYBR1XN3r4Hhwa4aO": "elena_conversations",
    }
    elena_fields = {}
    for field in custom_fields:
        field_id = field.get("id", "")
        if field_id in ELENA_FIELD_IDS:
            elena_fields[ELENA_FIELD_IDS[field_id]] = field.get("value")
    first = (contact.get("firstName") or "").strip()
    last = (contact.get("lastName") or "").strip()
    elena_fields["contact_first_name"] = first or None
    elena_fields["contact_last_name"] = last or None
    elena_fields["contact_full_name"] = f"{first} {last}".strip() or None
    return elena_fields


def update_ghl_contact_outcome(contact_id: str, new_outcome: str) -> bool:
    _ghl_pit = os.environ.get("GHL_PIT") or GHL_PIT
    r = requests.put(
        f"https://services.leadconnectorhq.com/contacts/{contact_id}",
        headers={"Authorization": f"Bearer {_ghl_pit}", "Version": "2021-07-28", "Content-Type": "application/json"},
        json={"customFields": [{"key": "elena_last_outcome", "field_value": new_outcome}]},
        timeout=10
    )
    if r.status_code in (200, 201):
        log.info(f"GHL update successful for contact {contact_id}")
        return True
    log.error(f"GHL update failed [{contact_id}]: {r.status_code} — {r.text[:200]}")
    return False


def _to_bool(value) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ('true', '1', 'yes', 'si', 'si'):
            return True
        if v in ('false', '0', 'no'):
            return False
    return bool(value) if value is not None else None

# ============================================================
# TELEGRAM — NOTIFICACIONES
# ============================================================

def telegram_send(text: str, reply_markup: dict = None, chat_id: str = None) -> Optional[dict]:
    _token = os.environ.get("TELEGRAM_BOT_TOKEN") or TELEGRAM_BOT_TOKEN
    _chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID") or TELEGRAM_CHAT_ID
    if not _token or not _chat_id:
        log.warning("Telegram no configurado — saltando notificación")
        return None
    payload = {
        "chat_id": _chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"https://api.telegram.org/bot{_token}/sendMessage", json=payload, timeout=10)
        if r.status_code == 200:
            result = r.json()
            return result.get("result")
        log.error(f"Telegram send error: {r.status_code} — {r.text[:200]}")
        return None
    except Exception as e:
        log.error(f"Telegram exception: {e}")
        return None


def telegram_notify_call(
    call_id: str, phone: str, original_outcome: str, aria_outcome: str,
    confidence: float, reasoning: str, errors: list, playbook_score: float,
    contact_name: str = None, call_ended_at: str = None, duration_seconds: int = None,
    has_discrepancy: bool = False, correction_id: str = None,
) -> bool:
    import pytz
    confidence_pct = int((confidence or 0) * 100)
    playbook_text = f"{playbook_score*100:.0f}%" if playbook_score is not None else "N/A"
    phone_display = phone[-10:] if phone and len(phone) >= 10 else phone or "N/A"
    high_errors = [e for e in (errors or []) if e.get("severity", "").upper() in ("HIGH", "CRITICAL")]
    if has_discrepancy:
        level_icon, level_label = "🔴", "DISCREPANCIA DETECTADA"
    elif high_errors:
        level_icon, level_label = "🟡", "ALERTA DE CALIDAD"
    else:
        level_icon, level_label = "🟢", "LLAMADA OK"
    name_line = (f"👤 <b>{contact_name}</b>\n") if contact_name else ""
    datetime_line = ""
    if call_ended_at:
        try:
            edt = pytz.timezone("America/New_York")
            dt_utc = datetime.fromisoformat(call_ended_at.replace("Z", "+00:00"))
            dt_edt = dt_utc.astimezone(edt)
            datetime_line = "🕐 " + dt_edt.strftime("%d/%m/%Y %I:%M %p") + " EDT"
        except Exception:
            datetime_line = "🕐 " + call_ended_at[:16].replace("T", " ") + " UTC"
    dur_text = ""
    if duration_seconds:
        m, s = divmod(int(duration_seconds), 60)
        dur_text = f" · {m}m{s:02d}s"
    aria_label = OUTCOME_LABELS.get(aria_outcome or "", aria_outcome or "?")
    orig_label = OUTCOME_LABELS.get(original_outcome or "", original_outcome or "sin dato")
    errors_section = ""
    if errors:
        severity_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪"}
        lines = [
            "  " + severity_icon.get(e.get("severity", "").upper(), "•") + " " + e.get("type", "?") + ": " + e.get("description", "")[:80]
            for e in errors[:5]
        ]
        errors_section = "\n\n⚠️ <b>Errores de playbook:</b>\n" + "\n".join(lines)
    sep = "━" * 24
    header = level_icon + " <b>ARIA · " + level_label + "</b>\n" + sep
    meta = name_line + "📞 <code>+" + phone_display + "</code>  " + datetime_line + dur_text
    if has_discrepancy:
        outcome_block = (
            "📋 GHL: <b>" + orig_label + "</b>\n"
            "🤖 ARIA: <b>" + aria_label + "</b>  (" + str(confidence_pct) + "% confianza)\n"
            "📊 Playbook: " + playbook_text
        )
    else:
        outcome_block = (
            "🤖 Outcome: <b>" + aria_label + "</b>  (" + str(confidence_pct) + "% confianza)\n"
            "📊 Playbook: " + playbook_text
        )
    reasoning_block = "💬 <i>" + (reasoning or "")[:280] + "</i>" if reasoning else ""
    full_text = "\n".join(filter(None, [header, meta, outcome_block, reasoning_block])) + errors_section
    reply_markup = None
    if has_discrepancy and correction_id:
        reply_markup = {
            "inline_keyboard": [[
                {"text": "✅ APROBAR (" + orig_label + " → " + aria_label + ")", "callback_data": "approve:" + correction_id}
            ], [
                {"text": "❌ RECHAZAR (mantener GHL)", "callback_data": "reject:" + correction_id}
            ]]
        }
    result = telegram_send(full_text, reply_markup)
    return result is not None


# ============================================================
# ARIA AUDIT ENGINE — CLAUDE
# ============================================================

ARIA_SYSTEM_PROMPT = """Eres ARIA, el sistema de auditoría de llamadas para Elena, una agente de IA de ventas de Laser Place Miami que agenda citas de Botox.
Tu trabajo es analizar transcripts de llamadas telefónicas y determinar:
1. Si la clasificación del outcome es correcta
2. Si Elena siguió el playbook correctamente
3. Qué errores cometió Elena (si los hay)
4. La calidad general de la conversación
5. Inteligencia de cliente: zonas de interés, objeciones, nivel de interés, etapa de compra

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
- **missed_close**: Tenía oportunidad de agendar pero no la aprovechó. IMPORTANTE: Solo marca este error si el cliente expresó intención clara de agendar (dijo "quiero agendar", "me puedes dar una cita", "quiero reservar", "I want to book") Y Elena no ejecutó check_availability. NO marques missed_close si el cliente solo mostró interés general o hizo preguntas.
- **wrong_info**: Dio información incorrecta (precio, disponibilidad, etc.). IMPORTANTE: Solo marca este error si Elena confirmó verbalmente una hora o fecha DIFERENTE a la que aparece en el tool result de create_booking. NO marques wrong_info por diferencias de formato (ej: "11:30" vs "eleven thirty").
- **playbook_violation**: No siguió el playbook (ej: ofreció precio antes de la evaluación, intentó más de 2 pivots después de rechazo claro). IMPORTANTE: NO marques playbook_violation por el orden de las preguntas de exploración — el playbook permite flexibilidad en STATE 2.
- **premature_endcall**: Terminó la llamada cuando el cliente aún quería hablar o tenía intención de agendar. IMPORTANTE: NO marques premature_endcall si el outcome es "no_contesto" (el cliente no contestó o fue buzón de voz) — en esos casos Elena cuelga correctamente.
- **repeated_availability_check**: Llamó a check_availability más de 2 veces en la misma llamada SIN que el cliente haya pedido un día diferente entre llamadas. IMPORTANTE: NO marques este error si Elena llamó check_availability una vez y luego el cliente pidió ver disponibilidad para otro día diferente — eso es el flujo correcto.
- **language_switch**: Elena respondió en el idioma INCORRECTO después de que el cliente cambió de idioma. IMPORTANTE: NO marques language_switch si Elena empezó en español y el cliente respondió en inglés en el primer turno — Elena necesita al menos 1 turno para detectar el cambio de idioma. Solo es error si el cliente habló 3+ frases en inglés y Elena siguió en español.
- **confusion_created**: Elena dio información contradictoria en la misma llamada (ej: dijo que un día no tiene disponibilidad y luego ofreció slots para ese mismo día). IMPORTANTE: Solo marca este error si la contradicción ocurrió ANTES de ejecutar check_availability. Si Elena reportó correctamente lo que devolvió la herramienta, no es confusion_created.
- **premature_greeting**: Elena hizo su pitch completo antes de confirmar que hay una persona real (buzón de voz, IVR). IMPORTANTE: En llamadas OUTBOUND, Elena siempre habla primero — eso es el comportamiento correcto, NO es premature_greeting. Solo marca este error si el cliente claramente indicó ser un sistema automatizado Y Elena continuó el pitch.
- **missed_objection**: El cliente expresó una objeción que Elena no manejó
- **unnecessary_tool_call**: Elena llamó a una herramienta innecesariamente

## PLAYBOOK DE ELENA (resumen):
1. Saludo → confirmar que hay persona real → preguntar si tiene 2 minutos
2. Preguntar qué le llama la atención del tratamiento (descubrir necesidad)
3. Proponer evaluación gratuita (Skin Reveal Analysis)
4. Preguntar si los martes funcionan (día preferido de la clínica)
5. Si no → ofrecer otros días disponibles
6. check_availability → presentar 2 opciones máximo
7. Cuando el cliente elige y confirma → create_booking
8. Si el cliente pregunta precio → explicar que se personaliza, invitar a la evaluación gratuita
9. Despedida con confirmación de la cita

## INTELIGENCIA DE CLIENTE:
Solo para llamadas con conversación real (transcript >200 chars y no es buzón).
Si es buzón o rechazo muy corto, devuelve client_intelligence: null.

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
  "quality_notes": "Notas adicionales sobre la calidad de la llamada",
  "client_intelligence": {
    "call_type": "voicemail|short_rejection|real_conversation",
    "language": "es|en|mixed",
    "interest_level": 1-5,
    "zones_mentioned": ["frente", "patas_de_gallo", "papada", "labios", "nariz", "cuello", "otro"],
    "objections": ["texto literal de la objeción"],
    "questions_asked": ["pregunta literal del cliente"],
    "barriers": ["barrera logística o personal"],
    "outcome_reason": "por qué no agendó si no agendó",
    "best_callback_signal": "señal de cuándo llamar de vuelta o null",
    "engagement_quality": "low|medium|high",
    "trust_signals": ["señal de confianza o desconfianza"],
    "buying_stage": "awareness|consideration|intent|ready_to_book",
    "price_sensitivity": "high|medium|low|unknown",
    "treatment_knowledge": "novice|informed|experienced"
  }
}
IMPORTANTE: client_intelligence solo se llena si call_type = "real_conversation".
Si es "voicemail" o "short_rejection", devuelve client_intelligence: null.
"""


def get_recent_feedback(limit: int = 10) -> list:
    try:
        rows = supabase_query(
            "feedback_log",
            "select=feedback_type,original_outcome,aria_outcome,final_outcome,vapi_call_id"
            "&order=created_at.desc&limit=20"
        )
        examples = []
        for r in rows:
            if not all([r.get("original_outcome"), r.get("aria_outcome"), r.get("final_outcome")]):
                continue
            call_id = r.get("vapi_call_id", "") or ""
            if call_id.startswith("test-") or call_id.startswith("audit-test"):
                continue
            examples.append({
                "feedback_type": r.get("feedback_type"),
                "original_outcome": r.get("original_outcome"),
                "aria_outcome": r.get("aria_outcome"),
                "final_outcome": r.get("final_outcome"),
            })
            if len(examples) >= limit:
                break
        return examples
    except Exception as e:
        log.warning(f"Few-shot: no se pudo cargar feedback_log: {e}")
        return []


def build_fewshot_block(examples: list) -> str:
    if not examples:
        return ""
    OUTCOME_LABELS_FS = {
        "agendo": "agendó cita",
        "no_agendo": "no agendó (conversación real)",
        "no_contesto": "no contestó / llamada sin conversación",
        "llamar_luego": "pidió que lo llamen después",
        "no_interesado": "rechazó el servicio explícitamente",
    }
    lines = ["\n## DECISIONES PREVIAS DE JUAN (aprende de estos ejemplos):\n"]
    for i, ex in enumerate(examples, 1):
        fb_type = ex["feedback_type"]
        orig = ex["original_outcome"]
        aria = ex["aria_outcome"]
        final = ex["final_outcome"]
        orig_label = OUTCOME_LABELS_FS.get(orig, orig)
        aria_label = OUTCOME_LABELS_FS.get(aria, aria)
        if fb_type == "approved":
            decision = "APROBÓ → cambió a '" + OUTCOME_LABELS_FS.get(final, final) + "'"
            lesson = "Cuando GHL dice '" + orig_label + "', ARIA tiene razón al clasificar como '" + aria_label + "'."
        else:
            decision = "RECHAZÓ → mantuvo '" + orig_label + "'"
            lesson = "Cuando GHL dice '" + orig_label + "', NO cambiar a '" + aria_label + "' — mantener '" + orig_label + "'."
        lines.append("Ejemplo " + str(i) + ": GHL=" + orig_label + " | ARIA clasificó=" + aria_label + " | Juan: " + decision)
        lines.append("  → Lección: " + lesson)
    return "\n".join(lines)


def audit_call_with_claude(call_data: dict) -> dict:
    """Auditar una llamada con Claude. Produce outcome + errores + inteligencia de cliente."""
    call_id = call_data.get("id", "unknown")
    transcript = call_data.get("transcript", "") or ""
    summary = call_data.get("summary", "") or ""
    ended_reason = call_data.get("endedReason", "") or ""
    started_at = call_data.get("startedAt", "")
    ended_at = call_data.get("endedAt", "")

    duration_seconds = None
    if started_at and ended_at:
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            duration_seconds = int((end - start).total_seconds())
        except Exception:
            pass

    messages = call_data.get("messages", []) or []
    tool_calls_summary = []
    for msg in messages:
        if msg.get("role") == "tool_calls":
            for tc in msg.get("toolCalls", []):
                fn = tc.get("function", {})
                tool_calls_summary.append({"name": fn.get("name"), "args": fn.get("arguments", "{}")})
        elif msg.get("role") == "tool_call_result":
            if tool_calls_summary:
                tool_calls_summary[-1]["result"] = str(msg.get("result", ""))[:300]

    recent_feedback = get_recent_feedback(limit=10)
    fewshot_block = build_fewshot_block(recent_feedback)

    user_prompt = (
        "Analiza esta llamada de Elena y determina el outcome correcto.\n\n"
        "## DATOS DE LA LLAMADA:\n"
        "- ID: " + call_id + "\n"
        "- Duración: " + str(duration_seconds) + "s\n"
        "- Razón de fin: " + ended_reason + "\n"
        "- Inicio: " + started_at + "\n\n"
        "## TRANSCRIPT:\n" + (transcript[:3000] if transcript else "(sin transcript)") + "\n\n"
        "## RESUMEN GENERADO POR VAPI:\n" + (summary[:500] if summary else "(sin resumen)") + "\n\n"
        "## TOOL CALLS EJECUTADOS:\n" + (json.dumps(tool_calls_summary, ensure_ascii=False, indent=2)[:2000] if tool_calls_summary else "(ninguno)") + "\n"
        + fewshot_block +
        "\nAnaliza todo lo anterior y responde en JSON con el formato especificado.\n"
        "Recuerda incluir client_intelligence solo si hay conversación real (transcript >200 chars y no es buzón)."
    )

    try:
        response = anthropic_client.messages.create(
            model=AUDIT_MODEL,
            max_tokens=1500,
            system=ARIA_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )
        response_text = response.content[0].text.strip()
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        audit_result = json.loads(response_text)
        audit_result["duration_seconds"] = duration_seconds
        audit_result["call_id"] = call_id
        log.info("Audit complete [" + call_id + "]: outcome=" + str(audit_result.get("correct_outcome")) + " confidence=" + str(audit_result.get("confidence", 0)))
        return audit_result
    except json.JSONDecodeError as e:
        log.error("JSON parse error for call " + call_id + ": " + str(e))
        return {"correct_outcome": None, "confidence": 0.0, "reasoning": "Error parsing Claude response: " + str(e), "errors_detected": [], "playbook_adherence_score": None, "client_intelligence": None, "call_id": call_id, "duration_seconds": duration_seconds}
    except Exception as e:
        log.error("Claude audit error for call " + call_id + ": " + str(e))
        return {"correct_outcome": None, "confidence": 0.0, "reasoning": "Error: " + str(e), "errors_detected": [], "playbook_adherence_score": None, "client_intelligence": None, "call_id": call_id, "duration_seconds": duration_seconds}


# ============================================================
# REGISTRO DE NO_CONTESTO AUTOMÁTICO (sin gastar Claude)
# ============================================================

def register_no_contesto(call_data: dict) -> Optional[dict]:
    """
    Registrar una llamada sin transcript como no_contesto automático.
    No usa Claude. Cierra el gap de cobertura de 45% → 100%.
    FIX: usa createdAt como fallback cuando startedAt/endedAt están vacíos
    (llamadas con endedAt=None en Vapi — buzones, no-answer inmediatos).
    """
    call_id = call_data.get("id")
    customer = call_data.get("customer", {}) or {}
    phone = customer.get("number", "")
    # FIX A: fallback a createdAt cuando startedAt/endedAt están vacíos
    created_at = call_data.get("createdAt") or call_data.get("created_at")
    started_at = call_data.get("startedAt") or created_at
    ended_at = call_data.get("endedAt") or created_at
    ended_reason = call_data.get("endedReason", "")
    duration_seconds = 0  # default 0 para llamadas sin conexión real
    if started_at and ended_at and started_at != ended_at:
        try:
            s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            duration_seconds = max(0, int((e - s).total_seconds()))
        except Exception:
            pass

    record = {
        "vapi_call_id": call_id,
        "phone_number": phone,
        "agent_name": "elena",
        "call_started_at": started_at,
        "call_ended_at": ended_at,
        "call_duration_seconds": duration_seconds,
        "original_outcome": None,
        "original_ended_reason": ended_reason,
        "aria_outcome": "no_contesto",
        "aria_confidence": 1.0,
        "aria_reasoning": "Auto-clasificado: sin transcript (endedReason=" + ended_reason + ", dur=" + str(duration_seconds) + "s)",
        "audit_status": "audited",  # FIX 23514: auto_classified no está en constraint valid_status
        "errors_detected": [],
        "audit_model": "auto",
        "audit_version": ARIA_VERSION,
    }
    saved = supabase_upsert("call_audits", record)
    if saved:
        log.info("Auto-classified no_contesto [" + str(call_id) + "]: dur=" + str(duration_seconds) + "s reason=" + ended_reason)
    return saved


# ============================================================
# PROCESO PRINCIPAL DE AUDITORÍA
# ============================================================

def process_call(call_data: dict, already_audited: set, silent: bool = False) -> Optional[dict]:
    """silent=True: no enviar notificación Telegram individual (usado en re-audit manual)."""
    call_id = call_data.get("id")
    if call_id in already_audited:
        return None
    if call_data.get("status") != "ended":
        return None
    with _calls_in_progress_lock:
        if call_id in _calls_in_progress:
            log.info("Skipping call " + call_id + " — already being audited in another thread")
            return None
        _calls_in_progress.add(call_id)
    try:
        return _process_call_inner(call_data, already_audited, call_id, silent=silent)
    finally:
        with _calls_in_progress_lock:
            _calls_in_progress.discard(call_id)


def _process_call_inner(call_data: dict, already_audited: set, call_id: str, silent: bool = False) -> Optional[dict]:
    log.info("Processing call: " + call_id)
    customer = call_data.get("customer", {}) or {}
    phone = customer.get("number", "")
    ghl_contact_id = None
    original_outcome = None

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

    if not ghl_contact_id and phone:
        ghl_contact_id = get_ghl_contact_id_by_phone(phone)

    # Auditar con Claude PRIMERO (tarda ~30s), luego leer GHL para evitar race condition
    audit_result = audit_call_with_claude(call_data)
    aria_outcome = audit_result.get("correct_outcome")
    aria_confidence = audit_result.get("confidence", 0.0)

    # FIX 23514: sanitizar outcomes — solo valores permitidos por constraint valid_outcome
    _VALID_OUTCOMES = {'agendo', 'no_agendo', 'no_contesto', 'llamar_luego', 'error_tecnico', 'no_interesado', None}
    if aria_outcome not in _VALID_OUTCOMES:
        log.warning(f"aria_outcome inválido '{aria_outcome}' → mapeado a None")
        aria_outcome = None

    ghl_fields = {}
    if ghl_contact_id:
        ghl_fields = get_ghl_contact_fields(ghl_contact_id)
        original_outcome = ghl_fields.get("elena_last_outcome")
        if original_outcome not in _VALID_OUTCOMES:
            log.warning(f"original_outcome inválido '{original_outcome}' de GHL → mapeado a None")
            original_outcome = None

    has_discrepancy = (
        original_outcome is not None and
        aria_outcome is not None and
        original_outcome != aria_outcome and
        aria_confidence >= CONFIDENCE_THRESHOLD_CORRECTION
    )

    audit_status = "discrepancy_found" if has_discrepancy else "audited"
    duration_seconds = audit_result.get("duration_seconds")
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
        "original_success_eval": _to_bool(ghl_fields.get("elena_success_eval")),
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

    saved = supabase_upsert("call_audits", audit_record)

    # Guardar inteligencia de cliente en tabla separada
    client_intel = audit_result.get("client_intelligence")
    if saved and client_intel and client_intel.get("call_type") == "real_conversation":
        intel_record = {
            "vapi_call_id": call_id,
            "audit_id": saved.get("id"),
            "call_type": client_intel.get("call_type"),
            "language": client_intel.get("language"),
            "interest_level": client_intel.get("interest_level"),
            "zones_mentioned": client_intel.get("zones_mentioned"),
            "objections": client_intel.get("objections"),
            "questions_asked": client_intel.get("questions_asked"),
            "barriers": client_intel.get("barriers"),
            "outcome_reason": client_intel.get("outcome_reason"),
            "best_callback_signal": client_intel.get("best_callback_signal"),
            "engagement_quality": client_intel.get("engagement_quality"),
            "trust_signals": client_intel.get("trust_signals"),
            "buying_stage": client_intel.get("buying_stage"),
            "price_sensitivity": client_intel.get("price_sensitivity"),
            "treatment_knowledge": client_intel.get("treatment_knowledge"),
            "phone_number": phone,
            "ghl_contact_id": ghl_contact_id,
        }
        supabase_upsert("call_intelligence", intel_record, on_conflict="vapi_call_id")
        log.info("Client intelligence saved [" + call_id + "]: stage=" + str(client_intel.get("buying_stage")) + " interest=" + str(client_intel.get("interest_level")))

    _correction_id = None
    if saved and has_discrepancy and ghl_contact_id:
        correction_record = {
            "audit_id": saved.get("id"),
            "vapi_call_id": call_id,
            "ghl_contact_id": ghl_contact_id,
            "field_name": "elena_last_outcome",
            "old_value": original_outcome,
            "new_value": aria_outcome,
            "correction_status": "pending",
        }
        correction_saved = supabase_insert("aria_corrections", correction_record)
        if correction_saved:
            _correction_id = str(correction_saved.get("id"))

    contact_name = ghl_fields.get("contact_full_name") if ghl_fields else None
    if not silent:
        telegram_notify_call(
            call_id=call_id, phone=phone,
            original_outcome=original_outcome, aria_outcome=aria_outcome,
            confidence=aria_confidence, reasoning=audit_result.get("reasoning"),
            errors=audit_result.get("errors_detected", []),
            playbook_score=audit_result.get("playbook_adherence_score"),
            contact_name=contact_name, call_ended_at=ended_at,
            duration_seconds=duration_seconds,
            has_discrepancy=has_discrepancy, correction_id=_correction_id,
        )

    return {
        "call_id": call_id,
        "original_outcome": original_outcome,
        "aria_outcome": aria_outcome,
        "aria_confidence": aria_confidence,
        "has_discrepancy": has_discrepancy,
        "audit_status": audit_status,
        "errors_count": len(audit_result.get("errors_detected", [])),
        "errors_detected_types": [e.get("type") for e in audit_result.get("errors_detected", [])],
        "playbook_score": audit_result.get("playbook_adherence_score"),
        "duration_seconds": duration_seconds,
        "quality_notes": audit_result.get("quality_notes", ""),
        "correction_id": _correction_id,
    }


def process_single_call_realtime(call_data: dict) -> Optional[dict]:
    """Procesar una llamada individual en tiempo real (desde webhook)."""
    call_id = call_data.get("id", "?")
    status_in_payload = call_data.get("status", "")
    if status_in_payload and status_in_payload != "ended":
        log.info("process_single_call_realtime [" + call_id + "]: status en webhook = '" + status_in_payload + "'")
    already_audited = get_already_audited_ids()
    return process_call(call_data, already_audited)


# ============================================================
# APPLY CORRECTION
# ============================================================

def apply_correction(correction_id: str, approved: bool, feedback_notes: str = "") -> dict:
    _supabase_key = os.environ.get("SUPABASE_SERVICE_KEY") or SUPABASE_SERVICE_KEY
    _supabase_url = os.environ.get("SUPABASE_URL") or SUPABASE_URL
    if not _supabase_key:
        return {"success": False, "error": "SUPABASE_SERVICE_KEY no configurado"}
    r = requests.get(
        _supabase_url + "/rest/v1/aria_corrections",
        headers={"apikey": _supabase_key, "Authorization": "Bearer " + _supabase_key},
        params={"id": "eq." + correction_id, "select": "*"},
        timeout=10
    )
    if r.status_code != 200 or not r.json():
        return {"success": False, "error": "Corrección no encontrada: " + correction_id}
    correction = r.json()[0]
    current_status = correction.get("correction_status")
    if current_status != "pending":
        return {"success": False, "error": "Corrección ya procesada (status=" + current_status + ")", "correction_id": correction_id}
    ghl_contact_id = correction.get("ghl_contact_id")
    old_value = correction.get("old_value")
    new_value = correction.get("new_value")
    audit_id = correction.get("audit_id")
    vapi_call_id = correction.get("vapi_call_id")
    ghl_response_code = None
    ghl_response_body = None
    if approved:
        success = update_ghl_contact_outcome(ghl_contact_id, new_value)
        new_status = "applied" if success else "pending"
        ghl_response_code = 200 if success else 500
        ghl_response_body = "OK" if success else "GHL update failed"
        if success:
            supabase_update("call_audits", {"id": audit_id}, {"audit_status": "feedback_approved"})
    else:
        new_status = "reverted"
        success = True
        supabase_update("call_audits", {"id": audit_id}, {"audit_status": "feedback_rejected"})
    supabase_update("aria_corrections", {"id": correction_id}, {"correction_status": new_status, "ghl_response_code": ghl_response_code, "ghl_response_body": ghl_response_body})
    feedback_record = {
        "audit_id": audit_id, "vapi_call_id": vapi_call_id,
        "feedback_type": "approved" if approved else "rejected",
        "feedback_source": "telegram",
        "original_outcome": old_value, "aria_outcome": new_value,
        "final_outcome": new_value if approved else old_value,
        "notes": feedback_notes or ("Telegram: " + ("aprobado" if approved else "rechazado") + " por Juan")
    }
    supabase_insert("feedback_log", feedback_record)
    if approved and success:
        msg = "✅ <b>Corrección aplicada en GHL</b>\n<code>" + (vapi_call_id[:20] if vapi_call_id else "N/A") + "...</code>\nOutcome actualizado: <b>" + old_value + "</b> → <b>" + new_value + "</b>"
    elif approved and not success:
        msg = "⚠️ <b>Error al aplicar corrección en GHL</b>\nLa corrección fue aprobada pero GHL devolvió un error."
    else:
        msg = "❌ <b>Corrección rechazada</b>\n<code>" + (vapi_call_id[:20] if vapi_call_id else "N/A") + "...</code>\nSe mantiene la clasificación original: <b>" + old_value + "</b>"
    telegram_send(msg)
    return {"success": success, "correction_id": correction_id, "approved": approved, "new_status": new_status, "old_value": old_value, "new_value": new_value}

# ============================================================
# MÉTRICAS Y SCORES — FIX #2: usar aria_outcome como fuente primaria
# ============================================================

def _calculate_elena_score(metrics: dict) -> int:
    total = metrics.get("total_calls", 0)
    if total == 0:
        return 0
    conversion = metrics.get("conversion_rate", 0)
    playbook = metrics.get("avg_playbook_adherence") or 0
    calls_with_errors = metrics.get("calls_with_errors", 0)
    error_rate = 1 - (calls_with_errors / total) if total > 0 else 1
    score = (conversion * 40 + playbook * 35 + error_rate * 25)
    return min(100, max(0, int(score)))


def _records_to_results(records: list) -> list:
    """FIX #2: usa aria_outcome como fuente primaria."""
    results = []
    for r in records:
        outcome = r.get("aria_outcome") or r.get("original_outcome") or "unknown"
        results.append({
            "call_id": r.get("vapi_call_id"),
            "original_outcome": r.get("original_outcome"),
            "aria_outcome": r.get("aria_outcome"),
            "outcome": outcome,
            "aria_confidence": r.get("aria_confidence", 0),
            "has_discrepancy": r.get("audit_status") == "discrepancy_found",
            "audit_status": r.get("audit_status"),
            "errors_count": len(r.get("errors_detected") or []),
            "errors_detected_types": [
                e.get("type") if isinstance(e, dict) else str(e)
                for e in (r.get("errors_detected") or [])
            ],
            "playbook_score": r.get("playbook_adherence_score"),
            "duration_seconds": r.get("call_duration_seconds"),
            "ghl_contact_id": r.get("ghl_contact_id"),
            "phone_number": r.get("phone_number"),
            "created_at": r.get("created_at", ""),
        })
    return results


def calculate_daily_metrics(results: list, audit_date: str) -> dict:
    """FIX #2: usa aria_outcome (campo 'outcome') como fuente primaria."""
    if not results:
        return {"total_calls": 0, "metric_date": audit_date, "agent_name": "elena"}
    total = len(results)
    outcomes = {}
    for r in results:
        o = r.get("outcome") or "unknown"
        outcomes[o] = outcomes.get(o, 0) + 1
    agendo = outcomes.get("agendo", 0)
    no_contesto = outcomes.get("no_contesto", 0)
    discrepancies = sum(1 for r in results if r.get("has_discrepancy"))
    durations = [r.get("duration_seconds") for r in results if r.get("duration_seconds")]
    avg_duration = sum(durations) / len(durations) if durations else 0
    playbook_scores = [r.get("playbook_score") for r in results if r.get("playbook_score") is not None]
    avg_playbook = sum(playbook_scores) / len(playbook_scores) if playbook_scores else None
    unique_contacts = len(set(r.get("ghl_contact_id") or r.get("phone_number") for r in results if r.get("ghl_contact_id") or r.get("phone_number")))
    connected = total - no_contesto
    conversion_rate = agendo / connected if connected > 0 else 0
    contact_rate = connected / total if total > 0 else 0
    return {
        "metric_date": audit_date,
        "agent_name": "elena",
        "total_calls": total,
        "unique_contacts": unique_contacts,
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
        "report_generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    }


def _get_top_errors(records: list, limit: int = 5) -> list:
    """FIX: errors_detected son objetos {type, severity, description} o strings."""
    error_counts = {}
    for r in records:
        errs = r.get("errors_detected") or []
        if isinstance(errs, str):
            try:
                errs = json.loads(errs)
            except Exception:
                errs = []
        for err in errs:
            if isinstance(err, dict):
                t = err.get("type", "unknown")
            elif isinstance(err, str):
                t = err
            else:
                continue
            error_counts[t] = error_counts.get(t, 0) + 1
    sorted_errors = sorted(error_counts.items(), key=lambda x: x[1], reverse=True)
    return [{"type": t, "count": c} for t, c in sorted_errors[:limit]]


def _get_aria_efficacy(days: int = 1) -> dict:
    """FIX #1: usa _utc_cutoff()."""
    cutoff = _utc_cutoff(days=days)
    records = supabase_query("feedback_log", "created_at=gte." + cutoff + "&limit=200")
    approved = sum(1 for r in records if r.get("feedback_type") == "approved")
    rejected = sum(1 for r in records if r.get("feedback_type") == "rejected")
    return {"approved": approved, "rejected": rejected}


# ============================================================
# FUNCIÓN CENTRAL DE REPORTE — Vapi como fuente de totales
# ============================================================

def _build_report_from_vapi(utc_start: str, utc_end: str, label: str, chat_id: str = None) -> dict:
    """
    Construye métricas completas usando Vapi como fuente de totales.
    - Consulta Vapi para el total real de llamadas
    - Registra no_contesto automático para las sin transcript
    - Usa Supabase (aria_outcome) para outcomes y errores
    """
    vapi_calls = fetch_vapi_calls_range(utc_start, utc_end, limit=500)
    ended_calls = [c for c in vapi_calls if c.get("status") == "ended"]
    vapi_total = len(ended_calls)

    audited_ids = get_audited_ids_in_range(utc_start, utc_end)

    auto_classified = 0
    for call in ended_calls:
        call_id = call.get("id")
        if call_id in audited_ids:
            continue
        transcript = call.get("transcript", "") or ""
        if len(transcript) < 50:
            saved = register_no_contesto(call)
            if saved:
                auto_classified += 1
                audited_ids.add(call_id)

    records = supabase_query(
        "call_audits",
        "call_started_at=gte." + utc_start + "&call_started_at=lt." + utc_end + "&limit=1000"
    )

    results = _records_to_results(records)
    top_errors = _get_top_errors(records, limit=5)

    import pytz
    edt = pytz.timezone("America/New_York")
    audit_date = datetime.fromisoformat(utc_start.replace("Z", "+00:00")).astimezone(edt).strftime("%Y-%m-%d")
    metrics = calculate_daily_metrics(results, audit_date)
    score = _calculate_elena_score(metrics)

    return {
        "label": label,
        "vapi_total": vapi_total,
        "supabase_total": len(results),
        "auto_classified": auto_classified,
        "metrics": metrics,
        "top_errors": top_errors,
        "score": score,
        "coverage_pct": round(len(results) / vapi_total * 100) if vapi_total > 0 else 0,
    }


def _format_report_telegram(data: dict) -> str:
    """Formatear un bloque de reporte para Telegram."""
    m = data["metrics"]
    total = m.get("total_calls", 0)
    agendo = m.get("calls_agendo", 0)
    no_agendo = m.get("calls_no_agendo", 0)
    no_contesto = m.get("calls_no_contesto", 0)
    llamar_luego = m.get("calls_llamar_luego", 0)
    no_interesado = m.get("calls_no_interesado", 0)
    error_tecnico = m.get("calls_error_tecnico", 0)
    unique = m.get("unique_contacts", 0)
    conversion = m.get("conversion_rate", 0) * 100
    contact_rate = m.get("contact_rate", 0) * 100
    score = data["score"]
    vapi_total = data["vapi_total"]
    coverage = data["coverage_pct"]

    score_emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
    score_bar = "█" * (score // 10) + "░" * (10 - score // 10)

    connected = total - no_contesto
    lines = [
        "📊 <b>ARIA · " + data["label"] + "</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "📞 Llamadas totales: <b>" + str(vapi_total) + "</b> (Vapi) | Auditadas: " + str(total) + " (" + str(coverage) + "%)",
        "👥 Contactos únicos: <b>" + str(unique) + "</b>",
        "",
        "📋 <b>OUTCOMES</b>",
        "  ✅ Agendó:         <b>" + str(agendo) + "</b>",
        "  💬 No agendó:      <b>" + str(no_agendo) + "</b>",
        "  📵 No contestó:    <b>" + str(no_contesto) + "</b>",
        "  🔄 Llamar luego:   <b>" + str(llamar_luego) + "</b>",
        "  🚫 No interesado:  <b>" + str(no_interesado) + "</b>",
        "  ⚙️ Error técnico:  <b>" + str(error_tecnico) + "</b>",
        "",
        "📈 <b>MÉTRICAS</b>",
        "  Conversión:    <b>" + str(round(conversion, 1)) + "%</b>  <i>(citas / llamadas contestadas: " + str(agendo) + "/" + str(connected) + ")</i>",
        "  Contacto:      <b>" + str(round(contact_rate, 1)) + "%</b>  <i>(contestadas / totales: " + str(connected) + "/" + str(total) + ")</i>",
        "  Score Elena:   " + score_emoji + " <b>" + str(score) + "/100</b> " + score_bar,
    ]

    if data["top_errors"]:
        lines.append("")
        lines.append("⚠️ <b>TOP ERRORES</b>")
        for i, e in enumerate(data["top_errors"], 1):
            lines.append("  " + str(i) + ". " + e["type"] + " ×" + str(e["count"]))

    if data.get("auto_classified", 0) > 0:
        lines.append("")
        lines.append("ℹ️ <i>" + str(data["auto_classified"]) + " llamadas sin transcript registradas como no_contesto automático</i>")

    return "\n".join(lines)


# ============================================================
# COMANDOS TELEGRAM — MANEJADOR CENTRAL
# ============================================================

def handle_telegram_command(command: str, args: str = "", chat_id: str = None) -> bool:
    """Manejador central de todos los comandos Telegram de ARIA."""
    log.info("Telegram command: " + command + " " + args)
    try:
        if command == "/reporte":
            _handle_reporte(args.strip(), chat_id)
        elif command == "/audit":
            _handle_audit(args.strip(), chat_id)
        elif command == "/errores":
            # FIX D: parsear argumento numérico correctamente (acepta /errores 2, /errores 14, etc.)
            arg_clean = (args or "").strip()
            days = max(1, min(int(arg_clean), 90)) if arg_clean.isdigit() else 7
            _send_errors_report(chat_id, days=days)
        elif command == "/score":
            _send_score_report(chat_id)
        elif command == "/eficacia":
            _send_efficacy_report(chat_id)
        elif command == "/llamada":
            if args:
                _send_call_detail(chat_id, args.strip())
            else:
                telegram_send("⚠️ Uso: /llamada [call_id]", chat_id=chat_id)
        elif command in ("/intel",):
            days = int(args.strip()) if args.strip().isdigit() else 7
            _send_intel_report(chat_id, days=days)
        elif command in ("/leads", "/leads calientes"):
            _send_hot_leads(chat_id)
        elif command == "/status":
            _send_status(chat_id)
        elif command == "/contacto":
            if args:
                _send_contact_history(chat_id, args.strip())
            else:
                telegram_send("⚠️ Uso: /contacto [teléfono]", chat_id=chat_id)
        elif command == "/tendencia":
            _send_tendencia(chat_id)
        elif command == "/backfill":
            # Backfill call_intelligence para llamadas históricas
            arg_clean = (args or "").strip()
            days = int(arg_clean) if arg_clean.isdigit() else 90
            _t = _threading.Thread(target=_run_backfill_intelligence, args=(chat_id, days, 200), daemon=True)
            _t.start()
        elif command == "/ayuda":
            _send_ayuda(chat_id)
        else:
            _send_ayuda(chat_id)
        return True
    except Exception as e:
        log.error("Error handling command " + command + ": " + str(e))
        telegram_send("⚠️ Error procesando comando: " + str(e)[:100], chat_id=chat_id)
        return False


def _handle_reporte(args: str, chat_id: str):
    import pytz
    edt = pytz.timezone("America/New_York")
    now_edt = datetime.now(edt)

    if not args or args == "hoy":
        utc_start, utc_end = _edt_day_range(0)
        label = "Hoy — " + now_edt.strftime("%d/%m/%Y")
        telegram_send("🔄 Generando reporte de hoy...", chat_id=chat_id)
        data = _build_report_from_vapi(utc_start, utc_end, label, chat_id)
        telegram_send(_format_report_telegram(data), chat_id=chat_id)

    elif args == "2d":
        telegram_send("🔄 Generando reporte 2 días...", chat_id=chat_id)
        utc_start_y, utc_end_y = _edt_day_range(1)
        label_y = "Ayer — " + (now_edt - timedelta(days=1)).strftime("%d/%m/%Y")
        data_y = _build_report_from_vapi(utc_start_y, utc_end_y, label_y, chat_id)
        telegram_send(_format_report_telegram(data_y), chat_id=chat_id)
        utc_start_h, utc_end_h = _edt_day_range(0)
        label_h = "Hoy — " + now_edt.strftime("%d/%m/%Y")
        data_h = _build_report_from_vapi(utc_start_h, utc_end_h, label_h, chat_id)
        telegram_send(_format_report_telegram(data_h), chat_id=chat_id)

    elif args == "7d":
        telegram_send("🔄 Generando reporte 7 días...", chat_id=chat_id)
        _send_weekly_report_command(chat_id)

    elif args.startswith("mes"):
        month_arg = args.replace("mes", "").strip() or None
        utc_start, utc_end, label = _edt_month_range(month_arg)
        if utc_start is None:
            telegram_send("⚠️ " + label, chat_id=chat_id)
            return
        telegram_send("🔄 Generando reporte de " + label + "...", chat_id=chat_id)
        data = _build_report_from_vapi(utc_start, utc_end, "Mes de " + label, chat_id)
        telegram_send(_format_report_telegram(data), chat_id=chat_id)

    else:
        telegram_send("⚠️ Uso:\n/reporte hoy\n/reporte 2d\n/reporte 7d\n/reporte mes marzo", chat_id=chat_id)


def _send_weekly_report_command(chat_id: str):
    import pytz
    edt = pytz.timezone("America/New_York")
    now_edt = datetime.now(edt)

    daily_data = []
    totals = {"vapi": 0, "agendo": 0, "connected": 0}

    for i in range(7):
        day_edt = now_edt - timedelta(days=i)
        utc_start, utc_end = _edt_day_range(i)
        day_label = day_edt.strftime("%d/%m")
        data = _build_report_from_vapi(utc_start, utc_end, day_label)
        m = data["metrics"]
        daily_data.append({
            "date": day_label,
            "vapi": data["vapi_total"],
            "agendo": m.get("calls_agendo", 0),
            "no_contesto": m.get("calls_no_contesto", 0),
            "score": data["score"],
        })
        totals["vapi"] += data["vapi_total"]
        totals["agendo"] += m.get("calls_agendo", 0)
        totals["connected"] += data["vapi_total"] - m.get("calls_no_contesto", 0)

    avg_score = sum(d["score"] for d in daily_data) / len(daily_data) if daily_data else 0
    total_conv = totals["agendo"] / totals["connected"] * 100 if totals["connected"] > 0 else 0

    text = (
        "📊 <b>ARIA · Reporte 7 Días</b>\n"
        "📅 " + (now_edt - timedelta(days=6)).strftime("%d/%m") + " → " + now_edt.strftime("%d/%m/%Y") + "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📞 Total: <b>" + str(totals["vapi"]) + "</b> | ✅ Agendadas: <b>" + str(totals["agendo"]) + "</b>\n"
        "📈 Conversión: <b>" + str(round(total_conv, 1)) + "%</b> | Score prom: <b>" + str(round(avg_score)) + "/100</b>\n\n"
        "📅 <b>Por día:</b>\n"
    )

    for d in reversed(daily_data):
        s = d["score"]
        emoji = "🟢" if s >= 70 else "🟡" if s >= 50 else "🔴"
        text += "  " + emoji + " " + d["date"] + ": " + str(d["vapi"]) + " llamadas | " + str(d["agendo"]) + " citas | " + str(s) + "/100\n"

    telegram_send(text, chat_id=chat_id)


def _handle_audit(args: str, chat_id: str):
    if args == "24h" or not args:
        telegram_send("🔄 <b>Iniciando audit 24h...</b>\nProcesando llamadas desde Vapi.", chat_id=chat_id)
        summary = _run_audit_range(_utc_cutoff(hours=24), _utc_cutoff(hours=0), "24h")
        _send_audit_summary(summary, "24h", chat_id)

    elif args == "7d":
        telegram_send("🔄 <b>Iniciando audit 7 días...</b>\nEsto puede tomar 5-10 minutos.", chat_id=chat_id)
        summary = _run_audit_range(_utc_cutoff(days=7), _utc_cutoff(hours=0), "7d")
        _send_audit_summary(summary, "7d", chat_id)
        _send_pattern_analysis(chat_id, days=7)

    elif args.startswith("mes"):
        month_arg = args.replace("mes", "").strip() or None
        utc_start, utc_end, label = _edt_month_range(month_arg)
        if utc_start is None:
            telegram_send("⚠️ " + label, chat_id=chat_id)
            return
        telegram_send("🔄 <b>Iniciando audit de " + label + "...</b>\nEsto puede tomar 10-20 minutos.", chat_id=chat_id)
        summary = _run_audit_range(utc_start, utc_end, label)
        _send_audit_summary(summary, label, chat_id)
        _send_pattern_analysis(chat_id, utc_start=utc_start, utc_end=utc_end, label=label)

    else:
        telegram_send("⚠️ Uso:\n/audit 24h\n/audit 7d\n/audit mes marzo", chat_id=chat_id)


def _run_audit_range(utc_start: str, utc_end: str, label: str) -> dict:
    """FIX #4: reporta nuevas + ya auditadas + sin transcript. FIX #10: no spamea notificaciones por llamada."""
    vapi_calls = fetch_vapi_calls_range(utc_start, utc_end, limit=500)
    ended_calls = [c for c in vapi_calls if c.get("status") == "ended"]
    already_audited = get_audited_ids_in_range(utc_start, utc_end)

    new_audited = 0
    discrepancies = 0
    auto_classified = 0
    skipped_already = 0

    for call in ended_calls:
        call_id = call.get("id")
        if call_id in already_audited:
            skipped_already += 1
            continue
        transcript = call.get("transcript", "") or ""
        if len(transcript) < 50:
            saved = register_no_contesto(call)
            if saved:
                auto_classified += 1
                already_audited.add(call_id)
        else:
            # silent=True: no enviar notificación individual por llamada durante re-audit manual
            result = process_call(call, already_audited, silent=True)
            if result:
                new_audited += 1
                if result.get("has_discrepancy"):
                    discrepancies += 1
                already_audited.add(call_id)

    return {
        "label": label,
        "vapi_total": len(ended_calls),
        "new_audited": new_audited,
        "auto_classified": auto_classified,
        "skipped_already": skipped_already,
        "discrepancies": discrepancies,
    }


def _send_audit_summary(summary: dict, label: str, chat_id: str):
    """FIX: muestra resumen consolidado. No spamea notificaciones individuales por llamada."""
    disc = summary["discrepancies"]
    disc_note = (
        "\n🔍 Discrepancias: <b>" + str(disc) + "</b> — usa /reporte para ver outcomes actualizados"
        if disc > 0 else
        "\n✅ Sin discrepancias GHL vs ARIA"
    )
    text = (
        "✅ <b>Audit completado — " + label + "</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📞 Total en Vapi: <b>" + str(summary["vapi_total"]) + "</b>\n"
        "🆕 Nuevas auditadas con Claude: <b>" + str(summary["new_audited"]) + "</b>\n"
        "🤖 Auto-clasificadas (sin transcript): <b>" + str(summary["auto_classified"]) + "</b>\n"
        "✓ Ya estaban en Supabase: <b>" + str(summary["skipped_already"]) + "</b>"
        + disc_note
    )
    telegram_send(text, chat_id=chat_id)


def _send_pattern_analysis(chat_id: str, days: int = 7, utc_start: str = None, utc_end: str = None, label: str = None):
    """Análisis de patrones con Claude sobre datos agregados de Supabase."""
    if utc_start is None:
        utc_start = _utc_cutoff(days=days)
        utc_end = _utc_cutoff(hours=0)
        label = label or ("últimos " + str(days) + " días")

    records = supabase_query(
        "call_audits",
        "call_started_at=gte." + utc_start + "&call_started_at=lt." + utc_end + "&limit=1000"
    )

    if not records:
        telegram_send("📊 Sin datos suficientes para análisis de patrones (" + label + ").", chat_id=chat_id)
        return

    results = _records_to_results(records)
    metrics = calculate_daily_metrics(results, label)
    top_errors = _get_top_errors(records, limit=10)

    hour_dist = {}
    for r in records:
        started = r.get("call_started_at") or r.get("created_at") or ""
        if started:
            try:
                import pytz
                edt = pytz.timezone("America/New_York")
                dt = datetime.fromisoformat(started.replace("Z", "+00:00")).astimezone(edt)
                h = dt.hour
                hour_dist[h] = hour_dist.get(h, 0) + 1
            except Exception:
                pass

    durs = [r.get("call_duration_seconds") for r in records if r.get("call_duration_seconds")]
    dur_dist = {"<20s": 0, "20-60s": 0, "1-3min": 0, ">3min": 0}
    for d in durs:
        if d < 20:
            dur_dist["<20s"] += 1
        elif d < 60:
            dur_dist["20-60s"] += 1
        elif d < 180:
            dur_dist["1-3min"] += 1
        else:
            dur_dist[">3min"] += 1

    prompt = (
        "Analiza estos datos agregados de llamadas de Elena (agente de ventas de Botox) y proporciona:\n"
        "1. Los 3 patrones más importantes que identificas\n"
        "2. Una recomendación accionable concreta para mejorar el prompt de Elena\n\n"
        "DATOS (" + label + "):\n"
        "- Total llamadas: " + str(metrics.get("total_calls", 0)) + "\n"
        "- Agendadas: " + str(metrics.get("calls_agendo", 0)) + " (" + str(round(metrics.get("conversion_rate", 0) * 100, 1)) + "% conversión)\n"
        "- No agendó: " + str(metrics.get("calls_no_agendo", 0)) + "\n"
        "- No contestó: " + str(metrics.get("calls_no_contesto", 0)) + "\n"
        "- No interesado: " + str(metrics.get("calls_no_interesado", 0)) + "\n"
        "- Playbook promedio: " + (str(round(metrics.get("avg_playbook_adherence", 0) * 100)) + "%" if metrics.get("avg_playbook_adherence") else "N/A") + "\n\n"
        "TOP ERRORES:\n" + json.dumps([{"error": e["type"], "count": e["count"]} for e in top_errors], ensure_ascii=False) + "\n\n"
        "DISTRIBUCIÓN DE DURACIÓN:\n" + json.dumps(dur_dist, ensure_ascii=False) + "\n\n"
        "DISTRIBUCIÓN HORARIA (hora EDT):\n" + json.dumps({str(k): v for k, v in sorted(hour_dist.items())}, ensure_ascii=False) + "\n\n"
        "Responde en español, de forma concisa y accionable. Máximo 400 palabras."
    )

    try:
        response = anthropic_client.messages.create(
            model=AUDIT_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        analysis = response.content[0].text.strip()
        text = (
            "🧠 <b>Análisis de Patrones — " + label + "</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + analysis[:3800]
        )
        telegram_send(text, chat_id=chat_id)
    except Exception as e:
        log.error("Pattern analysis error: " + str(e))
        telegram_send("⚠️ Error en análisis de patrones: " + str(e)[:100], chat_id=chat_id)


# ============================================================
# COMANDOS DE DIAGNÓSTICO
# ============================================================

def _send_errors_report(chat_id: str, days: int = 7):
    """FIX: errors_detected son objetos {type, severity, description}. Soporta cualquier número de días."""
    cutoff = _utc_cutoff(days=days)
    # FIX: no incluir &select= dentro de supabase_query — usar query separada
    records = supabase_query(
        "call_audits",
        "created_at=gte." + cutoff + "&limit=1000"
    )
    error_counts = {}
    severity_map = {}  # type -> worst severity
    severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    for r in records:
        errs = r.get("errors_detected") or []
        if isinstance(errs, str):
            try:
                import json as _json
                errs = _json.loads(errs)
            except Exception:
                errs = []
        for err in errs:
            if isinstance(err, dict):
                t = err.get("type", "unknown")
                sev = err.get("severity", "low").lower()
            elif isinstance(err, str):
                t = err
                sev = "low"
            else:
                continue
            error_counts[t] = error_counts.get(t, 0) + 1
            prev_sev = severity_map.get(t, "low")
            if severity_order.get(sev, 0) > severity_order.get(prev_sev, 0):
                severity_map[t] = sev
    if not error_counts:
        telegram_send(
            "✅ Sin errores detectados en los últimos " + str(days) + " días.\n(Basado en " + str(len(records)) + " llamadas auditadas)",
            chat_id=chat_id
        )
        return
    sorted_errors = sorted(error_counts.items(), key=lambda x: x[1], reverse=True)
    sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}
    text = "⚠️ <b>Top errores de Elena (últimos " + str(days) + "d)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "📊 Basado en " + str(len(records)) + " llamadas auditadas\n\n"
    for i, (err_type, count) in enumerate(sorted_errors[:10], 1):
        bar = "█" * min(count, 10)
        sev = severity_map.get(err_type, "low")
        icon = sev_icon.get(sev, "⚪")
        text += str(i) + ". " + icon + " <b>" + err_type + "</b> ×" + str(count) + " " + bar + "\n"
    telegram_send(text, chat_id=chat_id)


def _send_score_report(chat_id: str):
    """FIX #3: limit=500 por día."""
    import pytz
    edt = pytz.timezone("America/New_York")
    now_edt = datetime.now(edt)
    records_7d = []
    for i in range(7):
        utc_start, utc_end = _edt_day_range(i)
        day_label = (now_edt - timedelta(days=i)).strftime("%Y-%m-%d")
        day_records = supabase_query(
            "call_audits",
            "call_started_at=gte." + utc_start + "&call_started_at=lt." + utc_end + "&limit=500"
        )
        if day_records:
            results = _records_to_results(day_records)
            metrics = calculate_daily_metrics(results, day_label)
            score = _calculate_elena_score(metrics)
            records_7d.append({"date": day_label, "score": score, "calls": metrics.get("total_calls", 0), "conversion": metrics.get("conversion_rate", 0)})
    if not records_7d:
        telegram_send("📊 Sin datos suficientes para calcular el score.", chat_id=chat_id)
        return
    text = "⭐ <b>Score Elena — Últimos 7 días</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "📌 Conv = citas agendadas / llamadas contestadas\n\n"
    for r in records_7d:
        score = r["score"]
        bar = "█" * (score // 10) + "░" * (10 - score // 10)
        emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
        conv = str(round(r["conversion"] * 100, 1)) + "%"
        text += emoji + " " + r["date"] + ": <b>" + str(score) + "/100</b> " + bar + " (" + str(r["calls"]) + " llamadas | " + conv + " conv)\n"
    if len(records_7d) >= 2:
        trend = records_7d[0]["score"] - records_7d[-1]["score"]
        trend_str = "↑ +" + str(round(trend)) if trend > 0 else "↓ " + str(round(trend)) if trend < 0 else "→ estable"
        text += "\n📈 Tendencia: <b>" + trend_str + "</b> vs hace 7 días"
    telegram_send(text, chat_id=chat_id)


def _send_efficacy_report(chat_id: str):
    records = supabase_query("feedback_log", "order=created_at.desc&limit=100")
    if not records:
        telegram_send("📊 Sin feedback registrado aún.", chat_id=chat_id)
        return
    approved = sum(1 for r in records if r.get("feedback_type") == "approved")
    rejected = sum(1 for r in records if r.get("feedback_type") == "rejected")
    total = approved + rejected
    acc = str(round(approved / total * 100, 1)) + "%" if total > 0 else "N/A"
    cutoff = _utc_cutoff(days=7)
    recent = [r for r in records if (r.get("created_at", "") or "") >= cutoff]
    r_approved = sum(1 for r in recent if r.get("feedback_type") == "approved")
    r_rejected = sum(1 for r in recent if r.get("feedback_type") == "rejected")
    r_total = r_approved + r_rejected
    r_acc = str(round(r_approved / r_total * 100, 1)) + "%" if r_total > 0 else "N/A"
    text = (
        "🎯 <b>Eficacia de ARIA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Histórico:</b> " + acc + " (" + str(approved) + " aprobadas / " + str(rejected) + " rechazadas)\n"
        "<b>Últimos 7d:</b> " + r_acc + " (" + str(r_approved) + " aprobadas / " + str(r_rejected) + " rechazadas)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ Eficacia = % de correcciones de ARIA que Juan aprobó"
    )
    telegram_send(text, chat_id=chat_id)


def _send_call_detail(chat_id: str, call_id: str):
    records = supabase_query("call_audits", "vapi_call_id=eq." + call_id + "&select=*")
    if not records:
        records = supabase_query("call_audits", "vapi_call_id=like." + call_id + "%&select=*&limit=1")
    if not records:
        telegram_send("⚠️ Llamada no encontrada: <code>" + call_id + "</code>", chat_id=chat_id)
        return
    r = records[0]
    errors = r.get("errors_detected") or []
    errors_text = ""
    if errors:
        errors_text = "\n\n⚠️ <b>Errores:</b>"
        for e in errors[:5]:
            errors_text += "\n  • [" + (e.get("severity", "?") or "?").upper() + "] " + e.get("type", "?") + ": " + e.get("description", "")[:60]
    playbook = r.get("playbook_adherence_score")
    pb_str = str(round(playbook * 100)) + "%" if playbook else "N/A"
    intel_records = supabase_query("call_intelligence", "vapi_call_id=eq." + (r.get("vapi_call_id") or "") + "&select=*")
    intel_text = ""
    if intel_records:
        ci = intel_records[0]
        intel_text = (
            "\n\n🧠 <b>Inteligencia:</b>"
            "\n  Interés: " + str(ci.get("interest_level", "N/A")) + "/5 | Etapa: " + str(ci.get("buying_stage", "N/A"))
            + "\n  Zonas: " + (", ".join(ci.get("zones_mentioned") or []) or "N/A")
        )
        if ci.get("objections"):
            intel_text += "\n  Objeciones: " + "; ".join(ci.get("objections", [])[:2])
        if ci.get("best_callback_signal"):
            intel_text += "\n  📅 Callback: " + ci.get("best_callback_signal")
    text = (
        "📞 <b>Detalle de Llamada</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ID: <code>" + (r.get("vapi_call_id") or "N/A")[:30] + "</code>\n"
        "📱 Teléfono: " + (r.get("phone_number") or "N/A") + "\n"
        "⏱ Duración: " + str(r.get("call_duration_seconds") or "N/A") + "s\n"
        "📋 GHL dice: <b>" + (r.get("original_outcome") or "N/A") + "</b>\n"
        "🤖 ARIA dice: <b>" + (r.get("aria_outcome") or "N/A") + "</b> (" + str(int((r.get("aria_confidence") or 0) * 100)) + "%)\n"
        "📊 Playbook: <b>" + pb_str + "</b>\n"
        "🔍 Estado: " + (r.get("audit_status") or "N/A")
        + errors_text + intel_text + "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💬 <i>" + (r.get("aria_reasoning") or "")[:200] + "</i>"
    )
    telegram_send(text, chat_id=chat_id)


# ============================================================
# BACKFILL CALL_INTELLIGENCE
# ============================================================

def _extract_intelligence_from_transcript(call_id: str, transcript: str, aria_outcome: str, phone: str) -> Optional[dict]:
    """Extrae client_intelligence de un transcript ya guardado en call_audits (backfill)."""
    if not transcript or len(transcript) < 200:
        return None
    prompt = (
        "Analiza este transcript de una llamada de Elena (agente de ventas de Botox/estética médica).\n"
        "Extrae la inteligencia de cliente en formato JSON.\n\n"
        "TRANSCRIPT:\n" + transcript[:3000] + "\n\n"
        "OUTCOME CONOCIDO: " + (aria_outcome or "desconocido") + "\n\n"
        "Responde SOLO con JSON válido, sin texto adicional:\n"
        "{\n"
        '  "call_type": "real_conversation",\n'
        '  "language": "es|en|mixed",\n'
        '  "interest_level": 1-5,\n'
        '  "zones_mentioned": ["frente", "patas_de_gallo", "papada", etc],\n'
        '  "objections": ["texto literal de la objección"],\n'
        '  "questions_asked": ["pregunta literal del cliente"],\n'
        '  "barriers": ["barrera logística o personal"],\n'
        '  "outcome_reason": "por qué no agendó si no agendó",\n'
        '  "best_callback_signal": "señal de cuándo llamar de vuelta o null",\n'
        '  "engagement_quality": "low|medium|high",\n'
        '  "trust_signals": ["señal de confianza"],\n'
        '  "buying_stage": "awareness|consideration|intent|ready_to_book",\n'
        '  "price_sensitivity": "high|medium|low|unknown",\n'
        '  "treatment_knowledge": "novice|informed|experienced"\n'
        "}"
    )
    try:
        response = anthropic_client.messages.create(
            model=AUDIT_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        intel = json.loads(text)
        intel["call_type"] = "real_conversation"  # garantizar
        return intel
    except Exception as e:
        log.warning("Backfill intel error [" + call_id + "]: " + str(e))
        return None


def _run_backfill_intelligence(chat_id: str, days: int = 90, max_calls: int = 200):
    """Backfill call_intelligence para llamadas históricas con transcript real pero sin inteligencia."""
    telegram_send(
        "🔄 <b>Iniciando backfill de inteligencia...</b>\n"
        "Buscando llamadas con transcript real sin inteligencia registrada.\n"
        "Esto puede tomar 5-15 minutos.",
        chat_id=chat_id
    )
    cutoff = _utc_cutoff(days=days)
    # Obtener call_audits con transcript real
    audit_records = supabase_query(
        "call_audits",
        "call_started_at=gte." + cutoff
        + "&aria_outcome=in.(no_agendo,agendo,llamar_luego,no_interesado)"
        + "&limit=" + str(max_calls)
        + "&order=call_started_at.desc"
        + "&select=vapi_call_id,transcript_text,aria_outcome,phone_number,ghl_contact_id"
    )
    if not audit_records:
        telegram_send("📊 Sin llamadas con conversación real en los últimos " + str(days) + " días.", chat_id=chat_id)
        return

    # Filtrar solo las que tienen transcript real (>200 chars)
    real_calls = [r for r in audit_records if r.get("transcript_text") and len(r.get("transcript_text") or "") > 200]
    if not real_calls:
        telegram_send("📊 Sin transcripts reales encontrados.", chat_id=chat_id)
        return

    # Obtener los que ya tienen inteligencia en call_intelligence
    existing_intel_ids = set()
    existing_records = supabase_query(
        "call_intelligence",
        "created_at=gte." + cutoff + "&select=vapi_call_id&limit=1000"
    )
    for r in existing_records:
        if r.get("vapi_call_id"):
            existing_intel_ids.add(r["vapi_call_id"])

    # Filtrar solo los que NO tienen inteligencia
    pending = [r for r in real_calls if r.get("vapi_call_id") not in existing_intel_ids]

    if not pending:
        telegram_send(
            "✅ Todas las llamadas con conversación real ya tienen inteligencia registrada.\n"
            "Total con intel: " + str(len(existing_intel_ids)),
            chat_id=chat_id
        )
        return

    telegram_send(
        "📊 <b>Backfill:</b> " + str(len(pending)) + " llamadas pendientes de " + str(len(real_calls)) + " con transcript real.\n"
        "Ya tienen inteligencia: " + str(len(existing_intel_ids)) + "\n"
        "Procesando con Claude...",
        chat_id=chat_id
    )

    processed = 0
    errors = 0
    for r in pending:
        call_id = r.get("vapi_call_id", "")
        transcript = r.get("transcript_text", "")
        aria_outcome = r.get("aria_outcome", "")
        phone = r.get("phone_number", "")
        ghl_contact_id = r.get("ghl_contact_id")

        intel = _extract_intelligence_from_transcript(call_id, transcript, aria_outcome, phone)
        if intel:
            intel_record = {
                "vapi_call_id": call_id,
                "call_type": intel.get("call_type", "real_conversation"),
                "language": intel.get("language"),
                "interest_level": intel.get("interest_level"),
                "zones_mentioned": intel.get("zones_mentioned"),
                "objections": intel.get("objections"),
                "questions_asked": intel.get("questions_asked"),
                "barriers": intel.get("barriers"),
                "outcome_reason": intel.get("outcome_reason"),
                "best_callback_signal": intel.get("best_callback_signal"),
                "engagement_quality": intel.get("engagement_quality"),
                "trust_signals": intel.get("trust_signals"),
                "buying_stage": intel.get("buying_stage"),
                "price_sensitivity": intel.get("price_sensitivity"),
                "treatment_knowledge": intel.get("treatment_knowledge"),
                "phone_number": phone,
                "ghl_contact_id": ghl_contact_id,
            }
            saved = supabase_upsert("call_intelligence", intel_record, on_conflict="vapi_call_id")
            if saved:
                processed += 1
            else:
                errors += 1
        else:
            errors += 1

        # Progreso cada 20 llamadas
        if (processed + errors) % 20 == 0 and (processed + errors) > 0:
            telegram_send(
                "🔄 Backfill en progreso: " + str(processed) + " guardadas, " + str(errors) + " errores...",
                chat_id=chat_id
            )

    telegram_send(
        "✅ <b>Backfill completado</b>\n"
        "────────────────────────\n"
        "📊 Procesadas: <b>" + str(processed) + "</b>\n"
        "⚠️ Errores: <b>" + str(errors) + "</b>\n"
        "Total con inteligencia ahora: <b>" + str(len(existing_intel_ids) + processed) + "</b>\n\n"
        "Usa /intel para ver el análisis actualizado.",
        chat_id=chat_id
    )


# ============================================================
# COMANDOS DE INTELIGENCIA
# ============================================================

def _send_intel_report(chat_id: str, days: int = 7):
    cutoff = _utc_cutoff(days=days)
    records = supabase_query("call_intelligence", "created_at=gte." + cutoff + "&limit=500")
    if not records:
        # Fallback mejorado: buscar en call_audits y mostrar resumen accionable
        audit_records = supabase_query(
            "call_audits",
            "call_started_at=gte." + cutoff + "&limit=1000"
        )
        real_convos = [r for r in audit_records if r.get("transcript_text") and len(r.get("transcript_text") or "") > 200]
        real_convos_count = len(real_convos)
        total_audits = len(audit_records)
        no_agendo_count = sum(1 for r in real_convos if r.get("aria_outcome") == "no_agendo")
        agendo_count = sum(1 for r in real_convos if r.get("aria_outcome") == "agendo")
        telegram_send(
            "🧠 <b>Inteligencia de Cliente — Últimos " + str(days) + "d</b>\n"
            "────────────────────────\n"
            "📊 Audits en período: <b>" + str(total_audits) + "</b>\n"
            "💬 Conversaciones reales: <b>" + str(real_convos_count) + "</b>\n"
            "✅ Agendaron: <b>" + str(agendo_count) + "</b> | 📋 No agendaron: <b>" + str(no_agendo_count) + "</b>\n\n"
            "⚠️ La tabla de inteligencia estructurada (zonas, objeciones, interés) aún no tiene datos.\n"
            "Ejecuta <b>/backfill</b> para poblarla con las llamadas históricas (~$0.30-0.50 en Claude).",
            chat_id=chat_id
        )
        return

    real_convos = [r for r in records if r.get("call_type") == "real_conversation"]
    total_real = len(real_convos)

    zone_counts = {}
    for r in real_convos:
        for z in (r.get("zones_mentioned") or []):
            zone_counts[z] = zone_counts.get(z, 0) + 1

    objection_counts = {}
    for r in real_convos:
        for o in (r.get("objections") or []):
            key = o[:50]
            objection_counts[key] = objection_counts.get(key, 0) + 1

    question_counts = {}
    for r in real_convos:
        for q in (r.get("questions_asked") or []):
            key = q[:50]
            question_counts[key] = question_counts.get(key, 0) + 1

    interests = [r.get("interest_level") for r in real_convos if r.get("interest_level")]
    avg_interest = sum(interests) / len(interests) if interests else 0

    stage_dist = {}
    for r in real_convos:
        s = r.get("buying_stage", "unknown")
        stage_dist[s] = stage_dist.get(s, 0) + 1

    text = (
        "🧠 <b>Inteligencia de Cliente — Últimos " + str(days) + "d</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📞 Conversaciones reales: <b>" + str(total_real) + "</b>\n"
        "⭐ Interés promedio: <b>" + str(round(avg_interest, 1)) + "/5</b>\n\n"
    )

    if zone_counts:
        text += "💉 <b>ZONAS MÁS MENCIONADAS:</b>\n"
        for z, c in sorted(zone_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            text += "  • " + z + ": ×" + str(c) + "\n"
        text += "\n"

    if objection_counts:
        text += "💬 <b>OBJECIONES MÁS FRECUENTES:</b>\n"
        for o, c in sorted(objection_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            text += '  • "' + o + '" ×' + str(c) + "\n"
        text += "\n"

    if question_counts:
        text += "❓ <b>PREGUNTAS MÁS FRECUENTES:</b>\n"
        for q, c in sorted(question_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            text += '  • "' + q + '" ×' + str(c) + "\n"
        text += "\n"

    if stage_dist:
        text += "🎯 <b>ETAPA DE COMPRA:</b>\n"
        for stage in ["ready_to_book", "intent", "consideration", "awareness"]:
            count = stage_dist.get(stage, 0)
            if count > 0:
                pct = round(count / total_real * 100) if total_real > 0 else 0
                text += "  • " + stage + ": " + str(count) + " (" + str(pct) + "%)\n"

    telegram_send(text[:4096], chat_id=chat_id)


def _send_hot_leads(chat_id: str):
    cutoff = _utc_cutoff(days=7)
    # Primero intentar con call_intelligence (datos estructurados)
    intel_records = supabase_query(
        "call_intelligence",
        "created_at=gte." + cutoff + "&buying_stage=in.(intent,ready_to_book)&limit=100"
    )
    # También buscar en call_intelligence sin filtro de stage para ver todos los no-agendaron
    all_intel = supabase_query(
        "call_intelligence",
        "created_at=gte." + cutoff + "&limit=200"
    )
    # Obtener outcomes de call_audits para filtrar los que no agendaron
    intel_not_booked = []
    for r in all_intel:
        call_id = r.get("vapi_call_id")
        if not call_id:
            continue
        audit_rec = supabase_query("call_audits", "vapi_call_id=eq." + call_id + "&select=aria_outcome,phone_number&limit=1")
        if audit_rec and audit_rec[0].get("aria_outcome") != "agendo":
            intel_not_booked.append({
                "phone": r.get("phone_number") or audit_rec[0].get("phone_number", "N/A"),
                "interest": r.get("interest_level", "?"),
                "stage": r.get("buying_stage", "?"),
                "zones": r.get("zones_mentioned") or [],
                "barriers": r.get("barriers") or [],
                "objections": r.get("objections") or [],
                "callback": r.get("best_callback_signal"),
                "outcome": audit_rec[0].get("aria_outcome"),
                "engagement": r.get("engagement_quality", "?"),
            })
    if intel_not_booked:
        # Ordenar por interés descendente
        intel_not_booked.sort(key=lambda x: x["interest"] if isinstance(x["interest"], int) else 0, reverse=True)
        text = (
            "🔥 <b>Leads Calientes — No agendaron</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Total: <b>" + str(len(intel_not_booked)) + "</b> leads con inteligencia\n\n"
        )
        for i, lead in enumerate(intel_not_booked[:10], 1):
            phone = lead["phone"] or "N/A"
            zones_str = ", ".join(lead["zones"][:3]) if lead["zones"] else "N/A"
            barrier_str = lead["barriers"][0] if lead["barriers"] else ""
            callback_str = "\n   📅 Llamar: " + lead["callback"] if lead["callback"] else ""
            objection_str = "\n   💬 Objección: " + lead["objections"][0][:60] if lead["objections"] else ""
            stage_emoji = "🔥" if lead["stage"] in ("intent", "ready_to_book") else "📊"
            text += (
                str(i) + ". <b>" + phone + "</b> | ⭐" + str(lead["interest"]) + "/5 | " + stage_emoji + lead["stage"] + "\n"
                "   Zonas: " + zones_str + " | Engagement: " + str(lead["engagement"])
                + ("  | Barrera: " + barrier_str[:50] if barrier_str else "")
                + callback_str + objection_str + "\n\n"
            )
        telegram_send(text[:4096], chat_id=chat_id)
        return

    # Fallback: call_intelligence vacío — usar call_audits directamente
    audit_cutoff = _utc_cutoff(days=7)
    hot_from_audits = supabase_query(
        "call_audits",
        "call_started_at=gte." + audit_cutoff + "&aria_outcome=eq.no_agendo&limit=100"
    )
    real_hot = [r for r in hot_from_audits if r.get("transcript_text") and len(r.get("transcript_text") or "") > 300]
    if not real_hot:
        telegram_send(
            "📊 Sin leads calientes en los últimos 7 días.\n"
            "Ejecuta <b>/backfill</b> para poblar la inteligencia histórica.",
            chat_id=chat_id
        )
        return
    text = (
        "🔥 <b>Leads No Agendaron (sin inteligencia estructurada)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Total: <b>" + str(len(real_hot)) + "</b> leads con conversación real\n"
        "<i>Ejecuta /backfill para ver zonas, objeciones e interés</i>\n\n"
    )
    for i, r in enumerate(real_hot[:10], 1):
        phone = r.get("phone_number", "N/A")
        dur = r.get("call_duration_seconds")
        dur_str = str(int(dur) // 60) + "m" + str(int(dur) % 60) + "s" if dur else "N/A"
        started = (r.get("call_started_at") or "")[:10]
        reasoning = (r.get("aria_reasoning") or "")[:80]
        text += str(i) + ". <b>" + phone + "</b> | " + started + " | " + dur_str + "\n   " + reasoning + "\n\n"
    telegram_send(text[:4096], chat_id=chat_id)


# ============================================================
# COMANDOS DE SISTEMA
# ============================================================

def _send_status(chat_id: str):
    last_supa = supabase_query("call_audits", "order=created_at.desc&limit=1&select=created_at,vapi_call_id,audit_status")
    last_supa_time = "N/A"
    last_supa_ago = "N/A"
    if last_supa:
        ts = last_supa[0].get("created_at", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ago = (datetime.now(timezone.utc) - dt).total_seconds()
                last_supa_ago = "hace " + str(int(ago / 60)) + "m" if ago < 3600 else "hace " + str(int(ago / 3600)) + "h"
                last_supa_time = ts[:16].replace("T", " ") + " UTC"
            except Exception:
                last_supa_time = ts[:16]

    vapi_calls = fetch_vapi_calls(hours_back=2, limit=5)
    last_vapi_ago = "N/A"
    if vapi_calls:
        last_call = vapi_calls[0]
        ended_at = last_call.get("endedAt") or last_call.get("createdAt") or ""
        if ended_at:
            try:
                dt = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
                ago = (datetime.now(timezone.utc) - dt).total_seconds()
                last_vapi_ago = "hace " + str(int(ago / 60)) + "m" if ago < 3600 else "hace " + str(int(ago / 3600)) + "h"
            except Exception:
                pass

    cutoff_2h = _utc_cutoff(hours=2)
    supa_recent = supabase_query("call_audits", "created_at=gte." + cutoff_2h + "&select=vapi_call_id&limit=200")
    supa_recent_ids = set(r.get("vapi_call_id") for r in supa_recent)
    vapi_recent_ended = [c for c in vapi_calls if c.get("status") == "ended"]
    gap = len([c for c in vapi_recent_ended if c.get("id") not in supa_recent_ids])

    text = (
        "🔧 <b>Estado del Sistema ARIA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🕐 Ahora: " + datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M") + " UTC\n\n"
        "📡 <b>Vapi:</b>\n"
        "  Última llamada: " + last_vapi_ago + "\n"
        "  Llamadas últimas 2h: " + str(len(vapi_recent_ended)) + "\n\n"
        "🗄️ <b>Supabase:</b>\n"
        "  Último audit: " + last_supa_ago + " (" + last_supa_time + ")\n"
        "  Audits últimas 2h: " + str(len(supa_recent)) + "\n\n"
        + ("🟢" if gap == 0 else "🟡" if gap < 5 else "🔴") + " <b>Gap actual:</b> " + str(gap) + " llamadas en Vapi sin auditar\n\n"
        "🤖 <b>ARIA v" + ARIA_VERSION + "</b>\n"
        "  Modelo: " + AUDIT_MODEL + "\n"
        "  Polling: activo (cada 3min)\n"
        "  Webhook: activo"
    )
    telegram_send(text, chat_id=chat_id)


def _send_contact_history(chat_id: str, phone: str):
    # FIX C: PostgREST usa 'like.*X*' para wildcard, no 'ilike.%25X'
    phone_clean = "".join(c for c in phone if c.isdigit())
    last10 = phone_clean[-10:] if len(phone_clean) >= 10 else phone_clean
    # Intentar con los últimos 10 dígitos (sin código de país)
    records = supabase_query(
        "call_audits",
        "phone_number=like.*" + last10 + "*&order=call_started_at.desc&limit=20"
    )
    # Fallback: número completo si no encontró
    if not records and len(phone_clean) > 10:
        records = supabase_query(
            "call_audits",
            "phone_number=like.*" + phone_clean + "*&order=call_started_at.desc&limit=20"
        )
    if not records:
        telegram_send("📊 Sin historial para el teléfono: [" + phone + "]\nVerifica que el número esté en formato +17865533777", chat_id=chat_id)
        return
    text = (
        "📱 <b>Historial de Contacto</b>\n"
        "📞 " + phone + "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Total llamadas: <b>" + str(len(records)) + "</b>\n\n"
    )
    agendo_count = sum(1 for r in records if r.get("aria_outcome") == "agendo")
    text += "✅ Agendó: " + str(agendo_count) + " vez/veces\n\n"
    for i, r in enumerate(records[:10], 1):
        ts = (r.get("call_started_at") or r.get("created_at") or "")[:10]
        outcome = r.get("aria_outcome") or r.get("original_outcome") or "?"
        dur = r.get("call_duration_seconds")
        m, s = divmod(int(dur), 60) if dur else (0, 0)
        dur_str = str(m) + "m" + str(s).zfill(2) + "s" if dur else "N/A"
        outcome_label = OUTCOME_LABELS.get(outcome, outcome)
        reasoning = (r.get("aria_reasoning") or "")[:60]
        text += str(i) + ". " + ts + " | <b>" + outcome_label + "</b> | " + dur_str
        if reasoning:
            text += "\n   <i>" + reasoning + "</i>"
        text += "\n"
    telegram_send(text[:4096], chat_id=chat_id)


def _send_tendencia(chat_id: str):
    try:
        _send_tendencia_inner(chat_id)
    except Exception as _te:
        log.error("_send_tendencia crash: " + str(_te), exc_info=True)
        telegram_send("⚠️ /tendencia error: " + str(_te)[:150], chat_id=chat_id)


def _send_tendencia_inner(chat_id: str):
    telegram_send("🔄 Calculando tendencia...", chat_id=chat_id)
    import pytz
    edt = pytz.timezone("America/New_York")
    now_edt = datetime.now(edt)
    cutoff_30d = (now_edt - timedelta(days=30)).astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _skey = os.environ.get("SUPABASE_SERVICE_KEY") or SUPABASE_SERVICE_KEY
    _surl = os.environ.get("SUPABASE_URL") or SUPABASE_URL
    if not _skey:
        telegram_send("⚠️ /tendencia: SUPABASE_SERVICE_KEY no disponible en env.", chat_id=chat_id)
        return
    _hdrs = {"apikey": _skey, "Authorization": f"Bearer {_skey}", "Content-Type": "application/json"}
    _r = requests.get(
        f"{_surl}/rest/v1/call_audits",
        headers=_hdrs,
        params={"created_at": f"gte.{cutoff_30d}", "limit": "2000",
                "select": "created_at,aria_outcome,errors_detected,call_duration_seconds,playbook_adherence_score,phone_number,ghl_contact_id"},
        timeout=15
    )
    if _r.status_code != 200:
        telegram_send(f"⚠️ /tendencia: Error Supabase {_r.status_code} — {_r.text[:80]}", chat_id=chat_id)
        return
    all_records = _r.json()
    if not all_records:
        telegram_send("📊 Sin datos en los últimos 30 días.", chat_id=chat_id)
        return
    # Agrupar por día EDT
    from collections import defaultdict
    day_buckets = defaultdict(list)
    for r in all_records:
        ts = r.get("created_at") or ""
        if not ts:
            continue
        try:
            dt_utc = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            dt_edt = dt_utc.astimezone(edt)
            day_key = dt_edt.strftime("%d/%m")
            day_buckets[day_key].append(r)
        except Exception:
            continue
    if not day_buckets:
        telegram_send("📊 Sin datos suficientes para mostrar tendencia.", chat_id=chat_id)
        return
    # Calcular score por día
    scores = []
    for i in range(30):
        day_edt = now_edt - timedelta(days=i)
        day_label = day_edt.strftime("%d/%m")
        records_day = day_buckets.get(day_label, [])
        if records_day:
            results = _records_to_results(records_day)
            metrics = calculate_daily_metrics(results, day_label)
            score = _calculate_elena_score(metrics)
            scores.append({"date": day_label, "score": score, "calls": metrics.get("total_calls", 0)})
    if not scores:
        telegram_send("📊 Sin datos suficientes para mostrar tendencia.", chat_id=chat_id)
        return
    scores_reversed = list(reversed(scores))
    best = max(scores_reversed, key=lambda x: x["score"])
    worst = min(scores_reversed, key=lambda x: x["score"])
    avg = sum(s["score"] for s in scores_reversed) / len(scores_reversed)
    text = (
        "📈 <b>Tendencia Score Elena — Últimos 30 días</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 ≥70 bueno | 🟡 50-69 regular | 🔴 &lt;50 bajo\n"
        "Mejor: <b>" + best["date"] + " " + str(best["score"]) + "/100</b> | "
        "Peor: <b>" + worst["date"] + " " + str(worst["score"]) + "/100</b> | "
        "Prom: <b>" + str(round(avg)) + "/100</b>\n\n"
    )
    for s in scores_reversed:
        score = s["score"]
        bar_len = score // 10
        bar = "█" * bar_len + "░" * (10 - bar_len)
        emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
        text += emoji + " " + s["date"] + ": <b>" + str(score) + "/100</b> " + bar + " (" + str(s["calls"]) + " llamadas)\n"
    telegram_send(text[:4096], chat_id=chat_id)


def _send_ayuda(chat_id: str):
    text = (
        "🤖 <b>ARIA v" + ARIA_VERSION + " — Comandos disponibles</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>REPORTES</b>\n"
        "  /reporte hoy — día en curso\n"
        "  /reporte 2d — ayer + hoy\n"
        "  /reporte 7d — últimos 7 días con tabla\n"
        "  /reporte mes marzo — mes completo\n\n"
        "🔍 <b>AUDITS</b>\n"
        "  /audit 24h — re-audita últimas 24h\n"
        "  /audit 7d — re-audita + análisis de patrones\n"
        "  /audit mes marzo — auditoría profunda\n\n"
        "📈 <b>DIAGNÓSTICO</b>\n"
        "  /errores [días] — top errores de Elena\n"
        "  /score — score últimos 7 días\n"
        "  /eficacia — precisión de ARIA\n"
        "  /llamada [id] — detalle de una llamada\n\n"
        "🧠 <b>INTELIGENCIA</b>\n"
        "  /intel [días] — zonas, objeciones, preguntas\n"
        "  /leads calientes — leads con intención sin agendar\n"
        "  /backfill [días] — poblar inteligencia histórica (~$0.30-0.50)\n\n"
        "🔧 <b>SISTEMA</b>\n"
        "  /status — estado en tiempo real\n"
        "  /contacto [teléfono] — historial de contacto\n"
        "  /tendencia — gráfico 30 días\n"
        "  /ayuda — este mensaje"
    )
    telegram_send(text, chat_id=chat_id)


# ============================================================
# TELEGRAM — REPORTES AUTOMÁTICOS
# ============================================================

def telegram_send_daily_report(metrics: dict, audit_date: str, top_errors: list, aria_efficacy: dict = None) -> bool:
    total = metrics.get("total_calls", 0)
    agendo = metrics.get("calls_agendo", 0)
    no_agendo = metrics.get("calls_no_agendo", 0)
    no_contesto = metrics.get("calls_no_contesto", 0)
    llamar_luego = metrics.get("calls_llamar_luego", 0)
    error_tecnico = metrics.get("calls_error_tecnico", 0)
    no_interesado = metrics.get("calls_no_interesado", 0)
    unique = metrics.get("unique_contacts", 0)
    conversion = metrics.get("conversion_rate", 0) * 100
    contact_rate = metrics.get("contact_rate", 0) * 100
    avg_playbook = metrics.get("avg_playbook_adherence")
    pb_str = str(round(avg_playbook * 100)) + "%" if avg_playbook else "N/A"
    elena_score = _calculate_elena_score(metrics)
    score_emoji = "🟢" if elena_score >= 70 else "🟡" if elena_score >= 50 else "🔴"
    score_bar = "█" * (elena_score // 10) + "░" * (10 - elena_score // 10)

    text = (
        "📊 <b>ARIA · Reporte Diario — " + audit_date + "</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📞 Total: <b>" + str(total) + "</b> | 👥 Únicos: <b>" + str(unique) + "</b>\n\n"
        "📋 <b>OUTCOMES</b>\n"
        "  ✅ Agendó:        <b>" + str(agendo) + "</b>\n"
        "  💬 No agendó:     <b>" + str(no_agendo) + "</b>\n"
        "  📵 No contestó:   <b>" + str(no_contesto) + "</b>\n"
        "  🔄 Llamar luego:  <b>" + str(llamar_luego) + "</b>\n"
        "  🚫 No interesado: <b>" + str(no_interesado) + "</b>\n"
        "  ⚙️ Error técnico: <b>" + str(error_tecnico) + "</b>\n\n"
        "📈 <b>MÉTRICAS</b>\n"
        "  Conversión:  <b>" + str(round(conversion, 1)) + "%</b>\n"
        "  Contacto:    <b>" + str(round(contact_rate, 1)) + "%</b>\n"
        "  Playbook:    <b>" + pb_str + "</b>\n"
        "  Score Elena: " + score_emoji + " <b>" + str(elena_score) + "/100</b> " + score_bar
    )

    if top_errors:
        text += "\n\n⚠️ <b>TOP ERRORES</b>\n"
        for i, e in enumerate(top_errors[:5], 1):
            text += "  " + str(i) + ". " + e["type"] + " ×" + str(e["count"]) + "\n"

    if aria_efficacy:
        approved = aria_efficacy.get("approved", 0)
        rejected = aria_efficacy.get("rejected", 0)
        total_fb = approved + rejected
        acc = str(round(approved / total_fb * 100)) + "%" if total_fb > 0 else "N/A"
        text += "\n🎯 <b>ARIA:</b> " + acc + " precisión (" + str(approved) + "✅ " + str(rejected) + "❌)"

    result = telegram_send(text)
    return result is not None


# ============================================================
# EMAIL
# ============================================================

def build_report_text(results: list, metrics: dict, audit_date: str, aria_efficacy: dict = None, report_type: str = "daily") -> str:
    total = metrics.get("total_calls", 0)
    agendo = metrics.get("calls_agendo", 0)
    conversion = metrics.get("conversion_rate", 0) * 100
    elena_score = _calculate_elena_score(metrics)
    lines = [
        "ARIA — Reporte " + report_type.capitalize() + " — " + audit_date,
        "=" * 50,
        "Total llamadas: " + str(total),
        "Agendadas: " + str(agendo) + " (" + str(round(conversion, 1)) + "% conversión)",
        "Score Elena: " + str(elena_score) + "/100",
    ]
    if aria_efficacy:
        approved = aria_efficacy.get("approved", 0)
        rejected = aria_efficacy.get("rejected", 0)
        lines.append("Eficacia ARIA: " + str(approved) + " aprobadas / " + str(rejected) + " rechazadas")
    return "\n".join(lines)


def send_email_report(report_text: str, audit_date: str, metrics: dict, subject_prefix: str = "Diario"):
    log.info("=" * 60)
    log.info("REPORTE " + subject_prefix.upper() + " ARIA")
    log.info("=" * 60)
    log.info(report_text)
    log.info("=" * 60)

    gmail_from = os.getenv("GMAIL_FROM", "")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", "")

    if not gmail_from or not gmail_app_password:
        log.warning("Email delivery skipped: GMAIL_FROM o GMAIL_APP_PASSWORD no configurados")
        return False

    try:
        total = metrics.get("total_calls", 0)
        agendo = metrics.get("calls_agendo", 0)
        conversion = metrics.get("conversion_rate", 0) * 100
        elena_score = _calculate_elena_score(metrics)

        subject = "ARIA | Elena " + subject_prefix + " " + audit_date + " — " + str(total) + " llamadas | " + str(agendo) + " citas | Conversión " + str(round(conversion, 1)) + "% | Score " + str(elena_score) + "/100"

        html_body = "<html><body style='font-family:monospace;background:#0f0f0f;color:#e0e0e0;padding:24px'><h2 style='color:#00d4aa'>ARIA — Reporte " + subject_prefix + " de Elena</h2><p style='color:#888'>Fecha: " + audit_date + "</p><pre style='background:#1a1a1a;padding:16px;border-radius:8px;font-size:13px;line-height:1.6'>" + report_text + "</pre><hr style='border-color:#333'><p style='color:#555;font-size:11px'>Generado automáticamente por ARIA v" + ARIA_VERSION + ".</p></body></html>"

        msg = MIMEMultipart("alternative")
        msg["From"] = "ARIA — Elena Monitor <" + gmail_from + ">"
        msg["To"] = ADMIN_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(report_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_from, gmail_app_password)
            server.send_message(msg)

        log.info("Email report sent to " + ADMIN_EMAIL)
        return True

    except Exception as e:
        log.error("Email send error: " + str(e))
        return False


# ============================================================
# ALERTAS AUTOMÁTICAS
# ============================================================

def check_error_pattern_alert(results: list, audit_date: str):
    error_counts = {}
    for r in results:
        for err_type in (r.get("errors_detected_types") or []):
            error_counts[err_type] = error_counts.get(err_type, 0) + 1
    for err_type, count in error_counts.items():
        if count >= 5:
            telegram_send(
                "🚨 <b>ALERTA DE PATRÓN</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Error <b>" + err_type + "</b> detectado <b>" + str(count) + " veces</b> en " + audit_date + ".\n"
                "Requiere corrección en el prompt de Elena."
            )


def check_degradation_alert():
    import pytz
    edt = pytz.timezone("America/New_York")
    now_edt = datetime.now(edt)
    scores = []
    for i in range(3):
        utc_start, utc_end = _edt_day_range(i)
        day_label = (now_edt - timedelta(days=i)).strftime("%Y-%m-%d")
        day_records = supabase_query("call_audits", "created_at=gte." + utc_start + "&created_at=lt." + utc_end + "&limit=500")
        if day_records:
            results = _records_to_results(day_records)
            metrics = calculate_daily_metrics(results, day_label)
            scores.append(_calculate_elena_score(metrics))
    if len(scores) >= 2:
        drop = scores[-1] - scores[0]
        if drop <= -10:
            telegram_send(
                "⚠️ <b>ALERTA DE DEGRADACIÓN</b>\n"
                "El score de Elena bajó " + str(abs(drop)) + " puntos en 3 días.\n"
                "Score actual: " + str(scores[0]) + "/100\n"
                "Usa /errores para ver qué está fallando."
            )


# ============================================================
# RUN_AUDIT — COMPATIBILIDAD CON app.py
# ============================================================

def run_audit(hours_back: int = None, dry_run: bool = False):
    if hours_back is None:
        hours_back = AUDIT_LOOKBACK_HOURS

    import pytz
    edt = pytz.timezone("America/New_York")
    audit_date = datetime.now(edt).strftime("%Y-%m-%d")
    log.info("Starting ARIA audit run — date=" + audit_date + " hours_back=" + str(hours_back) + " dry_run=" + str(dry_run))

    calls = fetch_vapi_calls(hours_back=hours_back, limit=AUDIT_BATCH_SIZE)

    if not calls:
        log.info("No calls to audit")
        return {"total": 0, "audited": 0, "discrepancies": 0, "conversion_rate": 0, "avg_playbook_score": None, "audit_date": audit_date}

    already_audited = get_already_audited_ids() if not dry_run else set()
    new_calls = [c for c in calls if c.get("id") not in already_audited]
    log.info("New calls to audit: " + str(len(new_calls)) + " (skipping " + str(len(calls) - len(new_calls)) + " already audited)")

    if not new_calls:
        log.info("All calls already audited")
        return {"total": len(calls), "audited": 0, "discrepancies": 0, "conversion_rate": 0, "avg_playbook_score": None, "audit_date": audit_date}

    results = []
    discrepancies = []

    for i, call in enumerate(new_calls):
        log.info("Auditing call " + str(i + 1) + "/" + str(len(new_calls)) + ": " + str(call.get("id")))
        if dry_run:
            audit_result = audit_call_with_claude(call)
            result = {
                "call_id": call.get("id"),
                "original_outcome": None,
                "aria_outcome": audit_result.get("correct_outcome"),
                "aria_confidence": audit_result.get("confidence", 0),
                "has_discrepancy": False,
                "audit_status": "dry_run",
                "errors_count": len(audit_result.get("errors_detected", [])),
                "errors_detected_types": [e.get("type") for e in audit_result.get("errors_detected", [])],
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

    metrics = calculate_daily_metrics(results, audit_date)

    if not dry_run and metrics:
        supabase_upsert("daily_metrics", metrics, on_conflict="metric_date,agent_name")

    if not dry_run and results:
        check_error_pattern_alert(results, audit_date)

    summary = {
        "total_fetched": len(calls),
        "new_audited": len(results),
        "discrepancies_found": len(discrepancies),
        "conversion_rate": metrics.get("conversion_rate", 0),
        "avg_playbook_score": metrics.get("avg_playbook_adherence"),
        "audit_date": audit_date
    }

    log.info("Audit complete: " + str(summary))
    return summary


def run_daily_report():
    import pytz
    edt = pytz.timezone("America/New_York")
    audit_date = datetime.now(edt).strftime("%Y-%m-%d")
    log.info("Generating daily report for " + audit_date)

    utc_start, utc_end = _edt_day_range(0)

    # Usar Vapi como fuente de totales
    data = _build_report_from_vapi(utc_start, utc_end, "Hoy — " + audit_date)
    metrics = data["metrics"]
    top_errors = data["top_errors"]
    aria_efficacy = _get_aria_efficacy(days=1)

    if metrics.get("total_calls", 0) > 0:
        supabase_upsert("daily_metrics", metrics, on_conflict="metric_date,agent_name")

    telegram_send_daily_report(metrics, audit_date, top_errors, aria_efficacy)

    report_text = build_report_text([], metrics, audit_date, aria_efficacy, report_type="daily")
    send_email_report(report_text, audit_date, metrics, subject_prefix="Diario")

    check_degradation_alert()

    log.info("Daily report sent for " + audit_date)
    return {"date": audit_date, "total_calls": metrics.get("total_calls", 0)}


def run_weekly_report():
    import pytz
    edt = pytz.timezone("America/New_York")
    now_edt = datetime.now(edt)
    start_date = (now_edt - timedelta(days=7)).strftime("%Y-%m-%d")
    end_date = now_edt.strftime("%Y-%m-%d")
    log.info("Generating weekly report: " + start_date + " → " + end_date)

    utc_start = _utc_cutoff(days=7)
    utc_end = _utc_cutoff(hours=0)
    data = _build_report_from_vapi(utc_start, utc_end, "Semana " + start_date + " → " + end_date)
    metrics = data["metrics"]
    top_errors = data["top_errors"]

    weekly = {
        "total_calls": metrics.get("total_calls", 0),
        "calls_agendo": metrics.get("calls_agendo", 0),
        "calls_no_contesto": metrics.get("calls_no_contesto", 0),
        "avg_conversion_rate": metrics.get("conversion_rate", 0),
        "avg_elena_score": data["score"],
        "top_errors": top_errors,
    }

    telegram_send_weekly_report(weekly, start_date, end_date)

    report_text = build_report_text([], metrics, start_date + " → " + end_date, report_type="weekly")
    send_email_report(report_text, start_date + "→" + end_date, metrics, subject_prefix="Semanal")

    return {"start_date": start_date, "end_date": end_date, "total_calls": metrics.get("total_calls", 0)}


def telegram_send_weekly_report(weekly: dict, start_date: str, end_date: str) -> bool:
    total = weekly.get("total_calls", 0)
    agendo = weekly.get("calls_agendo", 0)
    avg_score = weekly.get("avg_elena_score", 0)
    top_errors = weekly.get("top_errors", [])
    avg_conv = weekly.get("avg_conversion_rate", 0) * 100

    text = (
        "📊 <b>ARIA · Reporte Semanal</b>\n"
        "📅 " + start_date + " → " + end_date + "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📞 Total llamadas: <b>" + str(total) + "</b>\n"
        "✅ Agendadas: <b>" + str(agendo) + "</b> (" + str(round(avg_conv, 1)) + "% conversión)\n"
        "⭐ Score promedio: <b>" + str(round(avg_score)) + "/100</b>\n"
    )

    if top_errors:
        text += "\n⚠️ <b>Top errores semana:</b>\n"
        for i, e in enumerate(top_errors[:5], 1):
            text += "  " + str(i) + ". " + e["type"] + " ×" + str(e["count"]) + "\n"

    result = telegram_send(text)
    return result is not None


# ============================================================
# POLLING ACTIVO
# ============================================================

_polling_started = False
_polling_lock = _threading.Lock()


def _aria_polling_loop(interval_seconds: int = 180):
    log.info("ARIA Polling iniciado — intervalo: " + str(interval_seconds) + "s, primer ciclo en 30s")
    first_run = True
    while True:
        try:
            _time.sleep(30 if first_run else interval_seconds)
            first_run = False
            log.info("ARIA Polling: buscando llamadas no auditadas...")
            calls = fetch_vapi_calls(hours_back=1, limit=30)
            ended_calls = [c for c in calls if c.get("status") == "ended"]
            if not ended_calls:
                log.info("ARIA Polling: sin llamadas terminadas en la ultima hora")
                continue
            already_audited = get_already_audited_ids()
            pending = [c for c in ended_calls if c.get("id") not in already_audited]
            if not pending:
                log.info("ARIA Polling: " + str(len(ended_calls)) + " llamadas — todas ya auditadas")
                continue
            log.info("ARIA Polling: " + str(len(pending)) + " llamadas pendientes de auditar")
            for call_data in pending:
                call_id = call_data.get("id", "?")
                transcript = call_data.get("transcript", "") or ""
                if len(transcript) < 50:
                    # FIX B: registrar como no_contesto, verificar que guardó
                    try:
                        saved = register_no_contesto(call_data)
                        if saved:
                            log.info("ARIA Polling [" + call_id + "]: auto-clasificado como no_contesto (" + str(call_data.get("endedReason","?")) + ")")
                            already_audited.add(call_id)
                        else:
                            log.warning("ARIA Polling [" + call_id + "]: register_no_contesto retornó None — reintentará en próximo ciclo")
                    except Exception as e:
                        log.error("ARIA Polling [" + call_id + "]: error en register_no_contesto — " + str(e))
                    continue
                try:
                    log.info("ARIA Polling [" + call_id + "]: auditando...")
                    result = process_call(call_data, already_audited)
                    if result:
                        log.info("ARIA Polling [" + call_id + "]: auditado — " + str(result.get("audit_status")))
                        already_audited.add(call_id)
                    else:
                        log.info("ARIA Polling [" + call_id + "]: saltado por process_call")
                except Exception as e:
                    log.error("ARIA Polling [" + call_id + "]: error — " + str(e))
        except Exception as e:
            log.error("ARIA Polling loop error: " + str(e))


def start_aria_polling(interval_seconds: int = 180):
    global _polling_started
    with _polling_lock:
        if _polling_started:
            log.info("ARIA Polling: ya iniciado, ignorando llamada duplicada")
            return
        _polling_started = True
    t = _threading.Thread(
        target=_aria_polling_loop,
        args=(interval_seconds,),
        daemon=False,
        name="aria-polling"
    )
    t.start()
    log.info("ARIA Polling thread iniciado (non-daemon) — intervalo " + str(interval_seconds) + "s")


# ============================================================
# CRON SEMANAL DE ERRORES — SÁBADOS
# ============================================================

_weekly_cron_started = False
_weekly_cron_lock = _threading.Lock()


def run_weekly_error_report():
    from collections import Counter, defaultdict
    log.info("Iniciando reporte semanal de errores de Elena...")
    cutoff = _utc_cutoff(days=7)
    today_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    week_start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d/%m/%Y")

    rows = supabase_query(
        "call_audits",
        "created_at=gte." + cutoff + "&select=errors_detected,aria_outcome,transcript_text,vapi_call_id,call_started_at&limit=500"
    )

    if not rows:
        telegram_send(
            "ℹ️ <b>Reporte semanal de errores</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Período: " + week_start + " → " + today_str + "\n"
            "Sin llamadas auditadas esta semana."
        )
        return

    error_data = defaultdict(list)
    for row in rows:
        errs = row.get("errors_detected") or []
        if isinstance(errs, str):
            try:
                errs = json.loads(errs)
            except Exception:
                errs = []
        transcript = (row.get("transcript_text") or "")[:600]
        call_id = row.get("vapi_call_id", "")[:8]
        outcome = row.get("aria_outcome", "?")
        for e in errs:
            if not isinstance(e, dict):
                continue
            err_type = e.get("type", "?")
            severity = (e.get("severity") or "medium").upper()
            description = e.get("description", "")[:200]
            if err_type in ("technical_failure", "test_call"):
                continue
            error_data[err_type].append({
                "severity": severity,
                "description": description,
                "transcript_snippet": transcript[:300] if transcript else "",
                "call_id": call_id,
                "outcome": outcome,
            })

    if not error_data:
        telegram_send(
            "✅ <b>Reporte semanal de errores</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Período: " + week_start + " → " + today_str + "\n"
            "Llamadas auditadas: " + str(len(rows)) + "\n\n"
            "Sin errores detectados esta semana."
        )
        return

    sorted_errors = sorted(error_data.items(), key=lambda x: len(x[1]), reverse=True)
    recurring = [(t, data) for t, data in sorted_errors if len(data) >= 3]
    occasional = [(t, data) for t, data in sorted_errors if len(data) < 3]

    def dominant_severity(data):
        counts = Counter(d["severity"] for d in data)
        for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            if counts.get(s, 0) > 0:
                return s
        return "MEDIUM"

    total_calls = len(rows)
    total_errors = sum(len(v) for v in error_data.values())

    lines = [
        "📊 <b>ARIA · REPORTE SEMANAL DE ERRORES</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "📅 Período: " + week_start + " → " + today_str,
        "📞 Llamadas auditadas: " + str(total_calls) + " | Errores detectados: " + str(total_errors),
        "",
    ]

    if recurring:
        lines.append("🔴 <b>ERRORES RECURRENTES (≥3 veces) — REQUIEREN FIX:</b>")
        for err_type, data in recurring:
            sev = dominant_severity(data)
            count = len(data)
            sev_icon = "🔴" if sev in ("CRITICAL", "HIGH") else "🟡"
            best_desc = max(data, key=lambda d: len(d["description"]))["description"]
            with_transcript = [d for d in data if d["transcript_snippet"]]
            example_line = ""
            if with_transcript:
                snippet = with_transcript[0]["transcript_snippet"][:200].replace("\n", " ")
                example_line = "\n   💬 Ejemplo: <i>" + snippet + "...</i>"
            lines.append(
                "\n" + sev_icon + " <b>" + err_type + "</b> ×" + str(count) + " | " + sev
                + "\n   " + best_desc + example_line
            )
        lines.append("")

    if occasional:
        lines.append("🟡 <b>Errores ocasionales (1-2 veces):</b>")
        for err_type, data in occasional:
            sev = dominant_severity(data)
            count = len(data)
            lines.append("  • " + err_type + " ×" + str(count) + " [" + sev + "]")
        lines.append("")

    if recurring:
        lines.append(
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 Hay <b>" + str(len(recurring)) + " error(es) recurrente(s)</b> que requieren fix en el prompt de Elena.\n"
            "Compártelo con Manus para aplicar las correcciones."
        )
    else:
        lines.append(
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Sin errores recurrentes esta semana."
        )

    full_message = "\n".join(lines)
    if len(full_message) > 4000:
        full_message = full_message[:3950] + "\n\n... (mensaje truncado, ver Supabase para detalle completo)"

    telegram_send(full_message)


def _weekly_cron_loop():
    import pytz
    _last_run_week = None
    while True:
        try:
            now_edt = datetime.now(pytz.timezone("America/New_York"))
            week_number = now_edt.isocalendar()[1]
            if now_edt.weekday() == 5 and now_edt.hour == 9 and _last_run_week != week_number:
                log.info("Ejecutando reporte semanal de errores (semana " + str(week_number) + ")...")
                run_weekly_error_report()
                _last_run_week = week_number
        except Exception as e:
            log.error("Error en weekly cron loop: " + str(e))
        _threading.Event().wait(3600)


def start_weekly_cron():
    global _weekly_cron_started
    with _weekly_cron_lock:
        if _weekly_cron_started:
            return
        _weekly_cron_started = True
    t = _threading.Thread(target=_weekly_cron_loop, daemon=True, name="aria-weekly-cron")
    t.start()
    log.info("ARIA Weekly cron thread iniciado (daemon) — reporte cada sábado 9:00 AM EDT")


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "audit"

    if mode == "pilot":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        calls = fetch_vapi_calls(hours_back=72, limit=50)
        calls = [c for c in calls if len(c.get("transcript", "") or "") > 200][:n]
        for call in calls:
            result = audit_call_with_claude(call)
            print("\nCall: " + call.get("id"))
            print("ARIA: " + str(result.get("correct_outcome")) + " (" + str(round(result.get("confidence", 0) * 100)) + "%)")
            print("Errors: " + str(len(result.get("errors_detected", []))))

    elif mode == "audit":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else AUDIT_LOOKBACK_HOURS
        summary = run_audit(hours_back=hours)
        print("\nAudit complete: " + str(summary))

    elif mode == "dry-run":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        summary = run_audit(hours_back=hours, dry_run=True)
        print("\nDry-run complete: " + str(summary))

    elif mode == "daily-report":
        result = run_daily_report()
        print("\nDaily report sent: " + str(result))

    elif mode == "weekly-report":
        result = run_weekly_report()
        print("\nWeekly report sent: " + str(result))

    elif mode == "check-alerts":
        check_degradation_alert()
        print("Alert check complete")

    elif mode == "weekly-errors":
        run_weekly_error_report()
        print("Weekly error report sent")

    else:
        print("Unknown mode: " + mode)
        print("Usage: python3.11 aria_audit.py [pilot|audit|dry-run|daily-report|weekly-report|check-alerts|weekly-errors] [args]")
        sys.exit(1)

# ALIAS DE COMPATIBILIDAD — app.py usa telegram_handle_command
telegram_handle_command = handle_telegram_command
