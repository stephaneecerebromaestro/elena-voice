"""
Elena AI - Vapi Tool Server for GoHighLevel
Standalone Flask server for deployment on Render.

Uses GHL V2 API (services.leadconnectorhq.com) with Private Integration Token (PIT).
ALL endpoints use V2 API — V1 is NOT used anywhere.

VERSION: v17.40 — All fixes applied:
  FIX A: get_contact uses caller's real phone (from call.customer.number) as fallback
  FIX B: Phone number validation — rejects obviously fake numbers (< 7 digits after cleaning)
  FIX C: Duplicate appointment detection before create_booking
  FIX D: check_availability returns empty-slots message with suggestion when no slots found
  FIX E: get_appointment_by_contact handles multiple upcoming appointments
  FIX F: Email basic validation before creating contact
  FIX G: update-date endpoint retries on failure and updates health version string
  FIX H: Caller phone is injected into every tool call via the webhook handler
  FIX I: end-of-call-report detects booking success across all Vapi message formats
  FIX J: Post-call processing uses GHL tags (5 distinct outcomes):
          agendo        = booking confirmed
          no_contesto   = no answer / voicemail / silence
          no_agendo     = answered but did not book
          llamar_luego  = client asked to be called back (schedule_callback tool used)
          error_tecnico = technical failure (5 scenarios: silence/no-tool, wrong contact,
                          no availability, create failure, mid-booking drop)
  FIX K: schedule_callback tool — Elena picks 2h/4h/12h/120h based on client's state
          writes elena_callback_time (ISO string) and elena_callback_hours to GHL contact
  FIX M: get_current_time tool — returns real Miami time so Elena can calculate callback windows
  FIX N: llamar_luego detection now uses schedule_callback tool result as ground truth (priority 2)
          Keyword fallback expanded with natural speech variations (llamar más tarde, me puedes llamar, etc.)
          Eliminates double-outcome bug where no_agendo overwrote llamar_luego
          Call counters — elena_total_calls (all calls) and elena_conversations
          (calls where client spoke) are incremented on every end-of-call
  FIX P: end-of-call-report runs in background thread — worker returns 200 OK immediately,
          GHL writes happen async. Handles 100+ simultaneous calls without blocking.
  AUDIT: M4 reschedule_appointment tool definition fixed (startTime→newStartTime in restore script)
         M5 check_availability capped at 10 slots total (5 tuesday + 5 other) to reduce LLM context
         M6 VERIFICANDO_PHRASES refactored as module-level constant (was duplicated 3x)
         M8 check_availability tool description simplified (removed Gilberto reference)
  v17.35 AUDIT FIXES:
  B1: Docstring version synced to match SERVER_VERSION
  B2: FIX BUG3 window reduced from 7 days to 30 minutes to prevent pre-existing appts as new bookings
  B3: success_eval normalized to lowercase string before GHL write
  B4: elena_call_duration written to GHL contact after every call
  B5: update-date endpoint now preserves analysisPlan in Vapi PATCH (was deleting it daily)
  B6: create_booking title configurable via BOOKING_TITLE env var
  B7: ghl_v2_get/post/put retry once on 429/503 with 500ms delay
  B8: _create_contact_locks cleaned up with TTL alongside _create_contact_results
  B9: schedule_callback no longer writes tag/outcome fields — deferred to end-of-call
  B10: VERIFICANDO_PHRASES extended with English equivalents for bilingual calls
  v17.39 CRITICAL FIXES:
  FIX-v17.39-A: voicemail_by_elena now requires `not user_spoke` — prevents real conversations
                from being classified as voicemail when Vapi omits startedAt/endedAt timestamps
                (duration_reliable=False was making not duration_reliable=True, activating
                voicemail_by_elena even for 3-minute real conversations with assistant-ended-call)
  FIX-v17.39-B: system_prompt PASO 7 now has explicit PROHIBIDO ABSOLUTO rule — create_booking
                must NEVER be called before client has chosen a specific slot from check_availability
                (LLM was calling create_booking during qualification phase, not booking phase)
  v17.40 FIXES:
  FIX-v17.40-A: system_prompt v17.40 — 8 conversational improvements:
                (1) Shorter firstMessage (20 words vs 32)
                (2) Martes rejection: ask preferred day immediately, never repeat martes
                (3) check_availability day validation: if slot day != requested day, say so
                (4) Fill silence during check_availability (same as get_contact)
                (5) 25-word limit in PASOS 5-7
                (6) Skip SRA pitch if client already said they want to book
                (7) Vary filler words, max 2x same word per call
                (8) PREGUNTA 2 removed as martes-retry barrier
  FIX-v17.40-B: elena_call_duration fallback — if call.startedAt/endedAt missing, calculate
                duration from first/last message timestamps in artifact.messages
                (fixes duration=0 on inbound calls where Vapi omits timestamps in webhook)

Handles tool calls from Vapi during live phone conversations:
- check_availability: Check calendar availability (next 30 days)
- get_contact: Search contact by phone number (V2 query search)
- create_contact: Create a new contact (V2, with duplicate check + phone/email validation)
- create_booking: Create a new appointment (V2, with duplicate check)
- reschedule_appointment: Reschedule an existing appointment (V2)
- cancel_appointment: Cancel an existing appointment (V2)
- get_appointment_by_contact: Find upcoming appointments for a contact (V2)

Also exposes:
- /update-date: Updates Vapi assistant system prompt with today's date (call daily)
- /health: Health check endpoint
"""

from flask import Flask, request, jsonify
import requests as http_requests
import json
import os
import re
import threading
import logging
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────
GHL_PIT = os.environ.get("GHL_PIT", "")
CALENDAR_ID = os.environ.get("GHL_CALENDAR_ID", "hYHvVwjKPykvcPkrsQWT")
LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "hzRj7DV9erP8tnPiTv7D")
VAPI_KEY = os.environ.get("VAPI_API_KEY", "88453266-9413-4275-babf-8e2481a9a1d6")
VAPI_ASSISTANT_ID = os.environ.get("VAPI_ASSISTANT_ID", "1631c7cf-2914-45f9-bf82-6635cdf00aba")

GHL_V2_BASE = "https://services.leadconnectorhq.com"
TZ = pytz.timezone("America/New_York")
# B6 FIX: Configurable booking title — override via env var for non-Botox agents
BOOKING_TITLE = os.environ.get("BOOKING_TITLE", "Evaluación Botox - Laser Place Miami")

DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# M6 FIX: Single source of truth for verificando phrases (used in 3 outcome detection branches)
# B10 FIX: Extended with English equivalents for bilingual calls
VERIFICANDO_PHRASES = [
    # Spanish
    "verificando", "momentito", "un momento", "ya casi",
    "déjame ver", "déjame verificar", "estoy revisando", "solo un segundo",
    "dame un momento", "un segundito", "ya reviso",
    # English
    "one moment", "one second", "just a second", "let me check",
    "let me look", "hold on", "give me a moment", "checking now",
    "one moment please", "just a moment"
]

SERVER_VERSION = "v17.49"  # FIX C1: ARIA thread daemon=False — sobrevive reciclado de worker Gunicorn (cobertura 100%)
                           # FIX C2: Telegram independiente de Supabase — ARIA notifica aunque upsert falle

# ─── Idempotency lock for create_contact ──────────────────────────────────────
# Prevents duplicate GHL contacts when LLM calls create_contact twice in parallel
_create_contact_locks = {}  # phone_normalized -> threading.Lock()
_create_contact_results = {}  # phone_normalized -> (result dict, timestamp float)
_create_contact_lock_meta = threading.Lock()  # protects the dicts above
CREATE_CONTACT_CACHE_TTL = 3600  # 1 hour — clear cached results after this many seconds


def v2_headers():
    """Headers for GHL V2 API using PIT."""
    return {
        "Authorization": f"Bearer {GHL_PIT}",
        "Content-Type": "application/json",
        "Version": "2021-04-15"
    }


def v2_headers_contacts():
    """Headers for GHL V2 Contacts API (uses different version)."""
    return {
        "Authorization": f"Bearer {GHL_PIT}",
        "Content-Type": "application/json",
        "Version": "2021-07-28"
    }


def ghl_v2_get(path, params=None, contacts_version=False):
    """B7 FIX: Retries once on 429 (rate limit) or 503 (service unavailable) with 500ms delay."""
    import time as _time
    url = f"{GHL_V2_BASE}{path}"
    headers = v2_headers_contacts() if contacts_version else v2_headers()
    for attempt in range(2):
        resp = http_requests.get(url, headers=headers, params=params, timeout=12)
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code in (429, 503) and attempt == 0:
            print(f"[GHL RETRY] GET {path} → HTTP {resp.status_code}, retrying in 500ms")
            _time.sleep(0.5)
            continue
        print(f"[GHL ERROR] GET {path} → HTTP {resp.status_code}: {resp.text[:200]}")
        return {}
    return {}


def ghl_v2_post(path, data, contacts_version=False):
    """B7 FIX: Retries once on 429 (rate limit) or 503 (service unavailable) with 500ms delay."""
    import time as _time
    url = f"{GHL_V2_BASE}{path}"
    headers = v2_headers_contacts() if contacts_version else v2_headers()
    for attempt in range(2):
        resp = http_requests.post(url, headers=headers, json=data, timeout=12)
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code in (429, 503) and attempt == 0:
            print(f"[GHL RETRY] POST {path} → HTTP {resp.status_code}, retrying in 500ms")
            _time.sleep(0.5)
            continue
        print(f"[GHL ERROR] POST {path} → HTTP {resp.status_code}: {resp.text[:200]}")
        return {}
    return {}


def ghl_v2_put(path, data):
    """B7 FIX: Retries once on 429 (rate limit) or 503 (service unavailable) with 500ms delay."""
    import time as _time
    url = f"{GHL_V2_BASE}{path}"
    for attempt in range(2):
        resp = http_requests.put(url, headers=v2_headers(), json=data, timeout=12)
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code in (429, 503) and attempt == 0:
            print(f"[GHL RETRY] PUT {path} → HTTP {resp.status_code}, retrying in 500ms")
            _time.sleep(0.5)
            continue
        print(f"[GHL ERROR] PUT {path} → HTTP {resp.status_code}: {resp.text[:200]}")
        return {}
    return {}


def normalize_phone(phone):
    """
    Normalize a phone number to E.164 format (+1XXXXXXXXXX).
    Returns (normalized_phone, is_valid) tuple.
    
    FIX B: Validates that the number has at least 7 digits after cleaning.
    A number like '123456789' (9 digits, no country code) is treated as invalid
    because it's likely a test/fake number given by the caller.
    """
    if not phone:
        return "", False
    
    # Remove all non-digit characters except leading +
    cleaned = re.sub(r"[^\d+]", "", phone)
    digits_only = re.sub(r"\D", "", cleaned)
    
    # Reject obviously fake/short numbers (less than 10 digits)
    # A real US number needs 10 digits (area code + number)
    if len(digits_only) < 10:
        return phone, False
    
    # Reject numbers that are clearly sequential/fake
    if digits_only in ("1234567890", "0000000000", "1111111111", "9999999999",
                       "11234567890", "10000000000", "11111111111"):
        return phone, False
    
    # Reject 9-digit numbers (not a valid US number format)
    if len(digits_only) == 9:
        return phone, False
    
    # Normalize to E.164
    if cleaned.startswith("+"):
        return cleaned, True
    
    if len(digits_only) == 10:
        return f"+1{digits_only}", True
    elif len(digits_only) == 11 and digits_only.startswith("1"):
        return f"+{digits_only}", True
    else:
        return f"+{digits_only}", True


def validate_email(email):
    """Basic email validation. Returns True if email looks valid."""
    if not email:
        return True  # Email is optional
    pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email.strip()))


def _format_local_time(iso_str):
    """Convert an ISO datetime string to a human-readable Miami time string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_local = dt.astimezone(TZ)
        time_str = dt_local.strftime("%I:%M %p").lstrip("0").lower()
        return f"{DAYS_ES[dt_local.weekday()]} {dt_local.day} de {MONTHS_ES[dt_local.month-1]} a las {time_str}"
    except Exception:
        return iso_str


# ─── Tool Handlers ────────────────────────────────────────────────────────────

def handle_check_availability(args):
    """Check available appointment slots for the next 30 days using V2 API.
    
    Returns a single flat list of slots with exact 'time' field and human 'label'.
    Tuesdays are prioritized (shown first).
    
    FIX D: Returns helpful message when no slots found, suggesting to try different dates.
    """
    now = datetime.now(TZ)
    # RISK FIX: Add 2-hour buffer so Elena never offers a slot that's about to start
    start_ms = int((now + timedelta(hours=2)).timestamp() * 1000)
    end_ms = int((now + timedelta(days=30)).timestamp() * 1000)

    result = ghl_v2_get(
        f"/calendars/{CALENDAR_ID}/free-slots",
        params={
            "startDate": start_ms,
            "endDate": end_ms,
            "timezone": "America/New_York"
        }
    )

    slots = []

    if isinstance(result, dict):
        for date_key, day_data in sorted(result.items()):
            # Skip non-date keys like 'traceId'
            if not date_key.startswith("20"):
                continue

            if isinstance(day_data, dict) and "slots" in day_data:
                slots_list = day_data["slots"]
            elif isinstance(day_data, list):
                slots_list = day_data
            else:
                continue

            for slot in slots_list[:5]:  # max 5 per day
                try:
                    dt = datetime.fromisoformat(slot.replace("Z", "+00:00"))
                    dt_local = dt.astimezone(TZ)
                    days_from_now = (dt_local.date() - now.date()).days
                    # Build human label: "mañana (martes 24 de marzo)"
                    if days_from_now == 0:
                        label = f"hoy ({DAYS_ES[dt_local.weekday()]} {dt_local.day} de {MONTHS_ES[dt_local.month-1]})"
                    elif days_from_now == 1:
                        label = f"mañana ({DAYS_ES[dt_local.weekday()]} {dt_local.day} de {MONTHS_ES[dt_local.month-1]})"
                    else:
                        label = f"{DAYS_ES[dt_local.weekday()]} {dt_local.day} de {MONTHS_ES[dt_local.month-1]}"
                    time_str = dt_local.strftime("%I:%M %p").lstrip("0").lower()
                    is_tuesday = dt_local.weekday() == 1

                    slots.append({
                        "time": slot,  # EXACT ISO timestamp — use verbatim for create_booking/reschedule
                        "label": f"{label} a las {time_str}",
                        "is_tuesday": is_tuesday,
                        "date": date_key
                    })
                except Exception:
                    slots.append({"time": slot, "label": slot, "is_tuesday": False, "date": date_key})

    if slots:
        # FIX A: 2 slots per unique day — ensures LLM sees ALL available days, not just the 2 earliest.
        # Old algo (5 tuesday + 5 other) hid wednesday/thursday/friday when sat/mon filled the 5 'other' spots.
        # New algo: group by date, take 2 slots per date, prioritize tuesdays first, cap at 14 total.
        from collections import defaultdict
        slots_by_date = defaultdict(list)
        for s in slots:
            slots_by_date[s["date"]].append(s)
        tuesday_dates = sorted([d for d in slots_by_date if any(s["is_tuesday"] for s in slots_by_date[d])])
        other_dates = sorted([d for d in slots_by_date if d not in tuesday_dates])
        ordered = []
        for date in tuesday_dates:
            ordered.extend(slots_by_date[date][:2])
        for date in other_dates:
            ordered.extend(slots_by_date[date][:2])
        ordered = ordered[:14]  # cap at 14 (7 days × 2 slots)
        return {
            "available": True,
            "slots": ordered,
            "total_available": len(slots),
            "BOOKING_RULE": (
                "REGLA CRÍTICA: Para create_booking o reschedule_appointment, "
                "copia el campo 'time' del slot EXACTAMENTE como aparece — "
                "incluyendo fecha y hora completas. NUNCA cambies la fecha. "
                "NUNCA construyas el startTime manualmente."
            ),
            "message": (
                "Horarios disponibles. Prioriza martes (is_tuesday=true). "
                "Usa el 'label' para hablar con el cliente y el 'time' exacto para las herramientas. "
                "Si el cliente pide un día que NO aparece en esta lista, dile exactamente qué días SÍ hay disponibles."
            )
        }
    else:
        # FIX D: Helpful message when no slots available
        return {
            "available": False,
            "slots": [],
            "total_available": 0,
            "message": (
                "No hay horarios disponibles en los próximos 14 días. "
                "Informa al cliente que el calendario está lleno por el momento y "
                "ofrécele que te deje sus datos para contactarle cuando haya disponibilidad, "
                "o sugiérele llamar directamente al 786-743-0129."
            )
        }


def handle_get_contact(args):
    """Search for a contact by phone number using V2 API (query search).
    
    FIX A: Also accepts 'callerPhone' (the real caller number from Vapi) as fallback.
    If 'phone' argument is fake/invalid, uses 'callerPhone' instead.
    FIX B: Validates phone number before searching.
    """
    phone = args.get("phone", "")
    caller_phone = args.get("callerPhone", "")  # Real caller phone injected by server

    # FIX B1 (INBOUND OVERRIDE): If callerPhone is available, it ALWAYS wins.
    # In an inbound call Elena cannot know the caller's number — any phone she
    # provides is a hallucination. The server enforces the real callerPhone.
    if caller_phone:
        caller_normalized, caller_valid = normalize_phone(caller_phone)
        if caller_valid:
            phone = caller_phone
            phone_normalized = caller_normalized
            phone_valid = True
        else:
            # callerPhone itself is invalid — fall back to Elena's provided phone
            phone_normalized, phone_valid = normalize_phone(phone)
    else:
        # No callerPhone (outbound or missing) — use Elena's provided phone
        phone_normalized, phone_valid = normalize_phone(phone)
    
    if not phone_normalized:
        return {"found": False, "message": "Número de teléfono no proporcionado."}

    if not phone_valid:
        return {
            "found": False, 
            "phone_invalid": True,
            "message": f"El número '{phone}' parece inválido. Por favor pídele al cliente su número de teléfono completo con código de área."
        }

    result = ghl_v2_get(
        "/contacts/",
        params={"locationId": LOCATION_ID, "query": phone_normalized},
        contacts_version=True
    )

    contacts = result.get("contacts", [])
    # FIX BUG1: GHL query search is fuzzy — it may return contacts with similar
    # but different phone numbers. We must verify the returned contact's phone
    # matches the number we searched for before using it.
    # Without this check, Elena could book an appointment under the wrong contact.
    if contacts:
        for contact in contacts:
            contact_phone_raw = contact.get("phone", "")
            contact_phone_norm, _ = normalize_phone(contact_phone_raw)
            if contact_phone_norm == phone_normalized:
                return {
                    "found": True,
                    "contactId": contact.get("id"),
                    "firstName": contact.get("firstName", ""),
                    "lastName": contact.get("lastName", ""),
                    "email": contact.get("email", ""),
                    "phone": contact.get("phone", ""),
                    "message": f"Contacto encontrado: {contact.get('firstName', '')} {contact.get('lastName', '')}"
                }
    return {"found": False, "message": "Contacto no encontrado. Necesitamos crear uno nuevo."}


def handle_create_contact(args):
    """Create a new contact in GHL using V2 API.
    
    FIX B: Validates phone number — rejects fake/short numbers.
    FIX F: Validates email format before saving.
    Checks for existing contact first to avoid duplicates.
    """
    phone = args.get("phone", "")
    caller_phone = args.get("callerPhone", "")  # FIX A: real caller phone
    email = args.get("email", "")
    
    # FIX F: Validate email
    if email and not validate_email(email):
        return {
            "success": False,
            "message": f"El email '{email}' no parece válido. Por favor pídele al cliente que lo deletree de nuevo."
        }
    
    # FIX B1: callerPhone ALWAYS has priority (inbound override)
    if caller_phone:
        caller_normalized, caller_valid = normalize_phone(caller_phone)
        if caller_valid:
            phone = caller_phone
            phone_normalized = caller_normalized
            phone_valid = True
        else:
            phone_normalized, phone_valid = normalize_phone(phone)
    else:
        phone_normalized, phone_valid = normalize_phone(phone)
    
    if not phone_valid:
        return {
            "success": False,
            "phone_invalid": True,
            "message": f"El número '{phone}' parece inválido o de prueba. Pídele al cliente su número real con código de área (10 dígitos)."
        }
    
    # ── Idempotency lock: prevent duplicate creation when LLM calls twice in parallel ──
    with _create_contact_lock_meta:
        if phone_normalized not in _create_contact_locks:
            _create_contact_locks[phone_normalized] = threading.Lock()
        phone_lock = _create_contact_locks[phone_normalized]

    with phone_lock:
        # If a parallel call already completed for this phone, return cached result
        # M7 FIX: Check TTL — discard cache entry AND lock when older than 1 hour to prevent memory leak
        import time as _time
        if phone_normalized in _create_contact_results:
            cached_result, cached_ts = _create_contact_results[phone_normalized]
            if _time.time() - cached_ts < CREATE_CONTACT_CACHE_TTL:
                return {**cached_result, "message": cached_result.get("message", "") + " (cached)"}
            else:
                # B8 FIX: TTL expired — remove BOTH result AND lock to prevent memory leak.
                # Previously only result was removed; lock dict grew unbounded over 100+ calls.
                del _create_contact_results[phone_normalized]
                with _create_contact_lock_meta:
                    _create_contact_locks.pop(phone_normalized, None)

        # Check if contact already exists to avoid duplicates
        existing = handle_get_contact({"phone": phone_normalized, "callerPhone": caller_phone})
        if existing.get("found"):
            result = {
                "success": True,
                "contactId": existing["contactId"],
                "message": f"Contacto ya existe: {existing.get('firstName','')} {existing.get('lastName','')}. Usando contacto existente."
            }
            import time as _time
            _create_contact_results[phone_normalized] = (result, _time.time())
            return result

        data = {
            "locationId": LOCATION_ID,
            "firstName": args.get("firstName", ""),
            "lastName": args.get("lastName", ""),
            "phone": phone_normalized,
            "source": "AI Elena - Llamada"
        }
        if email:
            data["email"] = email.strip()

        api_result = ghl_v2_post("/contacts/", data, contacts_version=True)

        if "contact" in api_result:
            contact = api_result["contact"]
            result = {
                "success": True,
                "contactId": contact.get("id"),
                "message": f"Contacto creado: {contact.get('firstName', '')} {contact.get('lastName', '')}"
            }
            import time as _time
            _create_contact_results[phone_normalized] = (result, _time.time())
            return result
        return {"success": False, "message": f"No se pudo crear el contacto: {str(api_result)[:200]}"}


def handle_create_booking(args):
    """Create a new appointment in GHL calendar using V2 API with PIT.
    
    FIX C: Checks for duplicate appointments before creating.
    """
    contact_id = args.get("contactId", "")
    start_time = args.get("startTime", "")
    title = args.get("title", BOOKING_TITLE)  # B6 FIX: uses configurable BOOKING_TITLE env var

    if not contact_id or not start_time:
        return {"success": False, "message": "Se necesita contactId y startTime para agendar."}

    # FIX C: Check for duplicate appointments
    existing_appt = handle_get_appointment_by_contact({"contactId": contact_id})
    if existing_appt.get("found"):
        existing_time = existing_appt.get("humanTime", "")
        existing_id = existing_appt.get("appointmentId", "")
        # Check if it's the same slot
        if existing_appt.get("startTime", "") == start_time:
            return {
                "success": True,
                "appointmentId": existing_id,
                "duplicate": True,
                "message": f"El cliente ya tiene una cita para {existing_time}. No se creó duplicado."
            }
        # Different slot — warn Elena
        return {
            "success": False,
            "has_existing": True,
            "existingAppointmentId": existing_id,
            "existingTime": existing_time,
            "message": (
                f"El cliente ya tiene una cita para {existing_time} (ID: {existing_id}). "
                "Si quiere cambiarla, usa reschedule_appointment. "
                "Si quiere una cita adicional, cancela la anterior primero con cancel_appointment."
            )
        }

    try:
        dt_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        dt_end = dt_start + timedelta(minutes=30)
        # FIX TZ: GHL ignores the UTC offset and treats the time as UTC directly.
        # Convert to UTC before sending so the appointment lands at the correct local time.
        dt_start_utc = dt_start.astimezone(pytz.utc)
        dt_end_utc = dt_end.astimezone(pytz.utc)
        start_time_utc = dt_start_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end_time_utc = dt_end_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    except Exception:
        start_time_utc = start_time
        end_time_utc = start_time
    data = {
        "calendarId": CALENDAR_ID,
        "locationId": LOCATION_ID,
        "contactId": contact_id,
        "startTime": start_time_utc,
        "endTime": end_time_utc,
        "title": title,
        "appointmentStatus": "confirmed",
        "selectedTimezone": "America/New_York",
        "selectedSlot": start_time_utc
    }

    result = ghl_v2_post("/calendars/events/appointments", data)

    if "id" in result or "appointment" in result:
        appt = result if "id" in result else result.get("appointment", {})
        human_time = _format_local_time(start_time)
        appt_id = appt.get("id", "")
        return {
            "success": True,
            "appointmentId": appt_id,
            "message": f"Cita agendada exitosamente para el {human_time} (hora de Miami). ID: {appt_id}"
        }
    return {"success": False, "message": f"No se pudo agendar: {str(result)[:300]}"}


def handle_reschedule_appointment(args):
    """Reschedule an existing appointment to a new time using V2 API with PIT."""
    appointment_id = args.get("appointmentId", "")
    new_start_time = args.get("newStartTime", "")

    if not appointment_id:
        return {"success": False, "message": "Se necesita el appointmentId para reagendar."}
    if not new_start_time:
        return {"success": False, "message": "Se necesita el nuevo horario (newStartTime) para reagendar."}

    try:
        dt_start = datetime.fromisoformat(new_start_time.replace("Z", "+00:00"))
        dt_end = dt_start + timedelta(minutes=30)
        # FIX TZ: Convert to UTC before sending — GHL ignores offset and treats as UTC directly.
        dt_start_utc = dt_start.astimezone(pytz.utc)
        dt_end_utc = dt_end.astimezone(pytz.utc)
        new_start_utc = dt_start_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end_time_utc = dt_end_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    except Exception:
        new_start_utc = new_start_time
        end_time_utc = new_start_time
    data = {
        "startTime": new_start_utc,
        "endTime": end_time_utc,
        "calendarId": CALENDAR_ID,
        "selectedTimezone": "America/New_York",
        "selectedSlot": new_start_utc
    }

    result = ghl_v2_put(f"/calendars/events/appointments/{appointment_id}", data)

    if "id" in result:
        human_time = _format_local_time(new_start_time)
        return {
            "success": True,
            "appointmentId": appointment_id,
            "newStartTime": new_start_time,
            "message": f"Cita reagendada exitosamente para el {human_time} (hora de Miami)."
        }
    return {"success": False, "message": f"No se pudo reagendar: {str(result)[:300]}"}


def handle_cancel_appointment(args):
    """Cancel an existing appointment using V2 API with PIT."""
    appointment_id = args.get("appointmentId", "")
    if not appointment_id:
        return {"success": False, "message": "Se necesita el appointmentId para cancelar."}

    result = ghl_v2_put(
        f"/calendars/events/appointments/{appointment_id}",
        {"appointmentStatus": "cancelled"}
    )

    if "id" in result:
        return {"success": True, "appointmentId": appointment_id, "message": "Cita cancelada exitosamente."}
    return {"success": False, "message": f"No se pudo cancelar: {str(result)[:200]}"}


def handle_get_appointment_by_contact(args):
    """Get upcoming appointments for a contact using V2 API with PIT.
    
    FIX E: Returns ALL upcoming appointments (not just the first), so Elena can
    ask the client which one to cancel/reschedule if there are multiple.
    """
    contact_id = args.get("contactId", "")
    if not contact_id:
        return {"found": False, "message": "Se necesita el contactId para buscar citas. Por favor usa get_contact primero para obtener el contactId."}

    result = ghl_v2_get(f"/contacts/{contact_id}/appointments")

    # GHL V2 returns 'events' key (not 'appointments')
    appointments = result.get("events", result.get("appointments", []))

    now = datetime.now(TZ)
    upcoming = []
    for a in appointments:
        if a.get("calendarId") != CALENDAR_ID:
            continue
        # Skip cancelled and noshow appointments
        if a.get("appointmentStatus") in ("cancelled", "noshow"):
            continue
        start_str = a.get("startTime", "")
        try:
            if " " in start_str:
                # GHL returns "YYYY-MM-DD HH:MM:SS" in the calendar's local timezone (America/New_York)
                start_str_iso = start_str.replace(" ", "T")
            else:
                start_str_iso = start_str
            dt = datetime.fromisoformat(start_str_iso)
            if dt.tzinfo is None:
                # Localize to Miami time (TZ), not UTC
                dt = TZ.localize(dt)
            if dt > now:
                upcoming.append((dt, a))
        except Exception:
            pass

    upcoming.sort(key=lambda x: x[0])

    if not upcoming:
        return {"found": False, "message": "No se encontraron citas próximas para este contacto en el calendario de Botox."}

    # FIX E: Return all upcoming appointments
    appt_list = []
    for dt_appt, appt in upcoming:
        start_raw = appt.get("startTime", "")
        if " " in start_raw:
            # Keep the original format but with T, don't add +00:00 as it's not UTC
            start_raw = start_raw.replace(" ", "T")
        dt_local = dt_appt.astimezone(TZ)
        time_str = dt_local.strftime("%I:%M %p").lstrip("0").lower()
        human_time = f"{DAYS_ES[dt_local.weekday()]} {dt_local.day} de {MONTHS_ES[dt_local.month-1]} a las {time_str}"
        appt_list.append({
            "appointmentId": appt.get("id", ""),
            "startTime": start_raw,
            "humanTime": human_time,
            "title": appt.get("title", ""),
            "status": appt.get("appointmentStatus", ""),
            "createdAt": appt.get("createdAt", "")  # FIX BUG3-v2: needed to detect if appt was created THIS call
        })

    # Primary appointment (soonest)
    primary = appt_list[0]
    
    if len(appt_list) == 1:
        return {
            "found": True,
            "appointmentId": primary["appointmentId"],
            "startTime": primary["startTime"],
            "humanTime": primary["humanTime"],
            "title": primary["title"],
            "status": primary["status"],
            "createdAt": primary["createdAt"],  # FIX BUG3-v2
            "total_appointments": 1,
            "message": f"Cita encontrada: {primary['humanTime']} (hora de Miami). ID: {primary['appointmentId']}"
        }
    else:
        # Multiple appointments — list them all
        descriptions = [f"{a['humanTime']} (ID: {a['appointmentId']})" for a in appt_list]
        return {
            "found": True,
            "appointmentId": primary["appointmentId"],
            "startTime": primary["startTime"],
            "humanTime": primary["humanTime"],
            "title": primary["title"],
            "status": primary["status"],
            "createdAt": primary["createdAt"],  # FIX BUG3-v2
            "total_appointments": len(appt_list),
            "all_appointments": appt_list,
            "message": (
                f"El cliente tiene {len(appt_list)} citas próximas: "
                + "; ".join(descriptions)
                + ". Pregunta al cliente cuál desea modificar/cancelar."
            )
        }


def handle_get_current_time(args):
    """FIX M: Return the current Miami date and time so Elena can calculate callback windows.

    Elena MUST call this tool as soon as the client says they can't talk right now,
    BEFORE asking about callback preference. This gives Elena the real local time
    so she can correctly map expressions like 'after 5:30pm' or 'tomorrow morning'
    to the right hours option (2 / 4 / 12 / 120).

    No parameters needed.
    """
    now = datetime.now(TZ)
    return {
        "time": now.strftime("%I:%M %p").lstrip("0"),   # e.g. "4:34 PM"
        "date": (
            f"{DAYS_ES[now.weekday()]} {now.day} de "
            f"{MONTHS_ES[now.month-1]} de {now.year}"
        ),
        "dayOfWeek": DAYS_ES[now.weekday()],              # e.g. "jueves"
        "hour24": now.hour,                               # 0–23 integer, useful for math
        "minute": now.minute,                             # 0–59 integer
        "isoNow": now.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def handle_schedule_callback(args):
    """
    FIX K+M: Schedule a callback in 2 / 4 / 12 / 120 hours from now (Miami time).

    Elena calls this when the client says they can't talk right now.
    Accepted values for `hours`:
      2   → ~2 hours   ("en 2 horas", "ahorita", "más tarde" vague)
      4   → ~4 hours   ("en 4 horas", "esta tarde", same-day specific time <6h away)
      12  → ~12 hours  ("mañana", "mañana en la mañana/tarde", "pasado mañana")
      120 → 5 days     ("la próxima semana", "en unos días", "más adelante")

    The server:
    1. Validates and normalises hours to one of the four accepted values
    2. Calculates the exact callback timestamp (now + hours, Miami time)
    3. Writes elena_callback_time (ISO) and elena_callback_hours ('2','4','12','120') to GHL
    4. Writes elena_last_outcome = 'llamar_luego' and adds the GHL trigger tag
    """
    hours_raw = args.get("hours", 2)
    caller_phone = args.get("callerPhone", "")

    # Validate — only 2, 4, 12, 120 are accepted; snap to nearest if unexpected value arrives
    VALID_HOURS = (2, 4, 12, 120)
    try:
        hours = int(hours_raw)
    except (ValueError, TypeError):
        hours = 2
    # T10 FIX: Snap to nearest valid value.
    # The LLM may pass any integer (e.g. 3 for "after 3pm" when it's 1pm).
    # Snap to nearest: <=3 → 2, 3-8 → 4, 8-66 → 12, >66 → 120
    if hours not in VALID_HOURS:
        if hours <= 3:
            hours = 2
        elif hours <= 8:
            hours = 4
        elif hours <= 66:
            hours = 12
        else:
            hours = 120

    # Calculate callback time
    now_miami = datetime.now(TZ)
    callback_dt = now_miami + timedelta(hours=hours)
    callback_iso = callback_dt.strftime("%Y-%m-%dT%H:%M:%S%z")  # e.g. 2026-03-26T17:30:00-0400
    callback_human = (
        f"{DAYS_ES[callback_dt.weekday()]} {callback_dt.day} de "
        f"{MONTHS_ES[callback_dt.month-1]} a las "
        f"{callback_dt.strftime('%I:%M %p').lstrip('0').lower()}"
    )

    # Find GHL contact by caller phone and write fields
    contact_id = ""
    if caller_phone:
        phone_norm, phone_valid = normalize_phone(caller_phone)
        if phone_valid:
            contact_result = handle_get_contact({"callerPhone": caller_phone})
            if contact_result.get("found"):
                contact_id = contact_result.get("contactId", "")

    if contact_id:
        # B9 FIX: Only write callback-specific fields here (timing data).
        # elena_last_outcome, elena_outcome, elena_stage and the trigger tag are
        # intentionally NOT written here — they are written by _process_end_of_call
        # after the call ends. Writing them here caused a race condition where the
        # GHL workflow fired before the end-of-call fields were ready.
        _update_contact_custom_field(contact_id, "elena_callback_time", callback_iso)
        _update_contact_custom_field(contact_id, "elena_callback_hours", str(hours))  # FIX L: GHL workflow reads this to decide 2h vs 4h wait
        print(f"[schedule_callback] Contact {contact_id} callback fields written: {callback_iso} (hours={hours})")
        return {
            "success": True,
            "hours": hours,
            "callbackTime": callback_iso,
            "callbackHuman": callback_human,
            "message": f"Perfecto. Te llamo el {callback_human} (hora de Miami)."
        }
    else:
        # No contact found — still return success so Elena can confirm to client
        # The end-of-call-report will handle the GHL update as llamar_luego
        print(f"[schedule_callback] No contact found for {caller_phone} — callback at {callback_iso} not written to GHL")
        return {
            "success": True,
            "hours": hours,
            "callbackTime": callback_iso,
            "callbackHuman": callback_human,
            "message": f"Perfecto. Te llamo el {callback_human} (hora de Miami)."
        }


def _add_tag_to_contact(contact_id, tag):
    """Add a tag to a GHL contact via V2 API."""
    try:
        url = f"{GHL_V2_BASE}/contacts/{contact_id}/tags"
        resp = http_requests.post(
            url,
            headers=v2_headers_contacts(),
            json={"tags": [tag]},
            timeout=10
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def _update_contact_custom_field(contact_id, field_key, value):
    """Update a custom field on a GHL contact via V2 API."""
    try:
        url = f"{GHL_V2_BASE}/contacts/{contact_id}"
        resp = http_requests.put(
            url,
            headers=v2_headers_contacts(),
            json={"customFields": [{"key": field_key, "field_value": value}]},
            timeout=10
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def _add_note_to_contact(contact_id, note_body):
    """Add a note to a GHL contact via V2 API."""
    try:
        url = f"{GHL_V2_BASE}/contacts/{contact_id}/notes"
        resp = http_requests.post(
            url,
            headers=v2_headers_contacts(),
            json={"body": note_body},
            timeout=15
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False

def _process_end_of_call(message):
    """
    Process end-of-call-report from Vapi.
    
    Detects if create_booking was called successfully, then applies the
    appropriate tag to the GHL contact so the 'Vapi - Procesador' workflow
    can handle Google Sheets writes and pipeline stage updates.
    
    Outcome logic (written to elena_last_outcome custom field):
    - agendo      → create_booking was called successfully (100% reliable)
    - no_contesto → call < 20s, voicemail, no-answer, or silence with no user speech
    - no_agendo   → user spoke but did not book (hung up, dropped, rejected)

    Workflow trigger: adds generic tag 'elena_resultado_botox' AFTER writing all
    custom fields. The GHL workflow reads elena_last_outcome (not the tag) to
    decide which branch to take. The workflow removes the tag at the end of each
    branch so it can re-trigger on the next call.

    Also stores call metadata as custom fields and full transcript as a GHL note.
    """
    try:
        call = message.get("call", {})
        artifact = message.get("artifact", {})
        messages_list = artifact.get("messages", call.get("messages", []))
        ended_reason = call.get("endedReason", message.get("endedReason", ""))
        analysis_obj = message.get("analysis", {})
        summary = analysis_obj.get("summary", "") or artifact.get("summary", call.get("summary", ""))
        structured_data = analysis_obj.get("structuredData", {})
        success_eval = analysis_obj.get("successEvaluation", None)  # "true" / "false" / None
        transcript = artifact.get("transcript", "")
        call_id = call.get("id", "")
        customer_phone = call.get("customer", {}).get("number", "")
        # ── Call duration: 3-level fallback chain (inbound + outbound) ──────────
        # FIX-v17.40-B: Guaranteed non-zero duration for every call type.
        # Level 1: call.startedAt / call.endedAt  (most accurate — ISO strings)
        # Level 2: message.startedAt / message.endedAt  (webhook root-level fallback)
        # Level 3: first/last message.time Unix-ms timestamps (always present if conversation happened)
        # If all three fail (e.g. 0-message voicemail), duration stays 0 — that's correct.
        call_duration_secs = 0
        call_started_at_str = ""  # FIX F3: preserved for BUG3-v3 appointment comparison
        try:
            # Level 1 + 2: ISO timestamp strings
            started_at = (call.get("startedAt") or call.get("createdAt") or
                          message.get("startedAt") or message.get("createdAt") or "")
            ended_at = (call.get("endedAt") or call.get("updatedAt") or
                        message.get("endedAt") or message.get("updatedAt") or "")
            call_started_at_str = started_at  # save for FIX F3
            if started_at and ended_at:
                _started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                _ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
                _dur = (_ended - _started).total_seconds()
                if _dur > 0:
                    call_duration_secs = _dur
        except Exception:
            pass
        # Level 3: Unix-ms timestamps from messages list (most reliable for inbound calls)
        if call_duration_secs == 0 and messages_list:
            try:
                msg_times_ms = []
                for _m in messages_list:
                    # Vapi uses 'time' field (Unix ms integer) on every message
                    _ts = _m.get("time")
                    if _ts and isinstance(_ts, (int, float)) and _ts > 1_000_000_000_000:
                        msg_times_ms.append(_ts)
                if len(msg_times_ms) >= 2:
                    call_duration_secs = (max(msg_times_ms) - min(msg_times_ms)) / 1000.0
            except Exception:
                pass
        # Detect inbound vs outbound
        raw_call_type = call.get("type", "")
        if "inbound" in raw_call_type.lower():
            call_type = "Inbound"
        elif "outbound" in raw_call_type.lower():
            call_type = "Outbound"
        else:
            call_type = "Outbound"  # default for backwards compatibility

        # ── Step 1: Detect tool call outcomes (create_booking + schedule_callback) ──
        # FIX N: scan messages for BOTH create_booking and schedule_callback results.
        # schedule_callback success is the ground truth for llamar_luego — more reliable
        # than keyword matching, which misses natural speech variations.
        # Priority: agendo > llamar_luego_confirmed > keyword fallback.
        agendo = False
        appointment_id = ""
        booked_time = ""
        llamar_luego_confirmed = False  # FIX N: set True when schedule_callback succeeded
        callback_hours_confirmed = 0    # FIX N: hours value from schedule_callback result
        has_any_tool_call = False       # CRITICAL FIX: initialize at top scope — used in multiple
                                        # outcome branches. Without this, Python raises UnboundLocalError
                                        # in branches that don't go through silence-timed-out,
                                        # crashing the entire end-of-call handler before writing to GHL.

        for msg in messages_list:
            # ── Helper: parse a tool result dict from any message format ──────
            def _parse_tool_result(parsed):
                """Given a parsed dict from a tool result, update agendo/llamar_luego flags.
                FIX B: reschedule_appointment success (has newStartTime) also counts as agendo.
                Returns True if agendo was set (caller should break)."""
                nonlocal agendo, appointment_id, booked_time, llamar_luego_confirmed, callback_hours_confirmed
                if not isinstance(parsed, dict) or not parsed.get("success"):
                    return False
                # create_booking success: has appointmentId (new booking)
                # reschedule_appointment success: has appointmentId + newStartTime
                if parsed.get("appointmentId"):
                    agendo = True
                    appointment_id = parsed.get("appointmentId", "")
                    booked_time = parsed.get("message", "")
                    return True
                # schedule_callback success
                if parsed.get("hours") and parsed.get("callbackTime"):
                    llamar_luego_confirmed = True
                    callback_hours_confirmed = int(parsed.get("hours", 2))
                return False

            # Format 1: role=tool with JSON content (older Vapi format)
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for item in content:
                        text = item.get("text", "") if isinstance(item, dict) else ""
                        try:
                            if _parse_tool_result(json.loads(text)):
                                break
                        except Exception:
                            pass
                elif isinstance(content, str):
                    try:
                        _parse_tool_result(json.loads(content))
                    except Exception:
                        pass

            # Format 2: role=tool_call_result (Vapi current format — confirmed in prod logs)
            if msg.get("role") == "tool_call_result":
                result_str = msg.get("result", "")
                try:
                    _parse_tool_result(json.loads(result_str) if isinstance(result_str, str) else result_str)
                except Exception:
                    pass

            # Format 3: type=tool-call-result (older Vapi format variant)
            if msg.get("type") == "tool-call-result":
                result_str = msg.get("result", "")
                try:
                    _parse_tool_result(json.loads(result_str) if isinstance(result_str, str) else result_str)
                except Exception:
                    pass

            if agendo:
                break

        # ── Step 2: Determine outcome label ──────────────────────────────────
        # Detect if the user actually spoke during the call (vs pure no-answer).
        # IMPORTANT: Vapi transcribes voicemail audio as role=user (e.g. "Tu mensaje después del toll...").
        # So user_spoke=True does NOT reliably mean a real human spoke. We use it only as a secondary signal.
        user_spoke = any(
            m.get("role") == "user" or m.get("role") == "human"
            for m in messages_list
        )

        # FIX F2: Detect voicemail phrases transcribed as user speech.
        # Vapi transcribes voicemail audio as role=user. If ALL user messages are
        # voicemail phrases, treat user_spoke as False for outcome classification.
        VOICEMAIL_PHRASES = [
            # English voicemail system phrases
            "at the tone", "please record your message", "please leave a message",
            "leave a message", "leave your message", "after the tone", "after the beep",
            "press pound", "press star", "press 1", "press one",
            "when you have finished", "when you've finished", "when you are finished",
            "simply hang up", "hang up or press", "further options", "further option",
            "not available", "is not available", "cannot take your call",
            "you have reached", "you've reached", "this is the voicemail",
            "the person you are calling", "the number you have dialed",
            "mailbox is full", "mailbox is not set up",
            # Spanish voicemail system phrases
            "deje su mensaje", "deja tu mensaje", "deje un mensaje",
            "después del tono", "después de la señal", "al escuchar el tono",
            "marque la tecla", "oprima el", "oprima la",
            "no está disponible", "no se encuentra disponible",
            "ha llamado a", "usted ha llamado",
            "el buzón de voz", "el correo de voz",
            "deje su nombre", "deje sus datos",
            # IVR / hold system phrases (not voicemail but also not a real human)
            "please stay on the line", "stay on the line", "please hold",
            "your call is important", "all agents are", "please wait",
            "thank you for calling", "thank you for holding", "thanks for calling",
            "thanks for holding", "thanks. please", "please continue to hold",
            "estimated wait time", "hold time", "next available",
            "press 1 for", "press 2 for", "press 3 for",
            "para español", "for english", "for spanish",
            "dial extension", "enter your", "if you know your party",
        ]
        user_msgs_content = [
            str(m.get("message", m.get("content", ""))).lower()
            for m in messages_list
            if m.get("role") in ("user", "human")
        ]
        # user_spoke_real = True only if at least one user message is NOT a voicemail phrase
        user_spoke_real = False
        if user_msgs_content:
            for _uc in user_msgs_content:
                if _uc and not any(vp in _uc for vp in VOICEMAIL_PHRASES):
                    user_spoke_real = True
                    break
        # Override user_spoke with the real-human check
        if user_spoke and not user_spoke_real:
            print(f"[FIX F2] user_spoke overridden to False — all user messages are voicemail phrases")
            user_spoke = False

        # Calls under 20 seconds are treated as no_contesto regardless of who spoke.
        # Exception: if duration is 0 (startedAt/endedAt missing — common in inbound calls)
        # AND user actually spoke, the duration is unreliable — do NOT treat as short call.
        duration_reliable = call_duration_secs > 0
        short_call = duration_reliable and call_duration_secs < 20

        # FIX F1: Extend no_contesto to calls 20-45s where client said only 1 word/phrase
        # and hung up (customer-ended-call) without Elena executing any tool.
        # This covers: "Hello?", "Hola?", "Sí?", "¿Quién es?" — not a real conversation.
        # ONLY applies when: customer-ended-call + no tool calls + <=2 user messages + <=5 words total
        _has_any_tool_f1 = any(
            m.get("role") in ("tool_calls", "tool_call") or
            m.get("type") in ("tool-call", "tool-call-result") or
            m.get("role") == "tool_call_result"
            for m in messages_list
        )
        _user_word_count = sum(len(str(m.get("message", m.get("content", ""))).split()) for m in messages_list if m.get("role") in ("user", "human"))
        _user_msg_count = len([m for m in messages_list if m.get("role") in ("user", "human")])
        # FIX v17.44: Extended shallow_call to also cover assistant-ended-call variants.
        # Bug: When user said "quiero agendar" and Elena misinterpreted the incomplete phrase
        # as a goodbye signal, the call ended via assistant-ended-call in ~26s with no tool calls.
        # The old check only covered customer-ended-call, so this scenario fell through to no_agendo.
        # Fix: Include all assistant-ended-call variants so any <45s call with no tool calls and
        # minimal user speech is classified as no_contesto (shallow), not no_agendo.
        _SHALLOW_ENDED_REASONS = (
            "customer-ended-call",
            "assistant-ended-call",
            "assistant-ended-call-after-message-spoken",
            "assistant-said-end-call-phrase",
        )
        shallow_call = (
            not short_call  # not already caught by short_call
            and duration_reliable
            and call_duration_secs < 45
            and ended_reason in _SHALLOW_ENDED_REASONS
            and not _has_any_tool_f1
            and _user_msg_count <= 2
            and _user_word_count <= 12  # FIX B3: raised from 8 to 12 to cover background noise transcribed as words
        )

        # Voicemail / no-answer detection: Elena ended the call AND duration < 45s.
        # We use 45s because voicemail greetings + Elena's goodbye can last up to ~40s.
        # CRITICAL FIX v17.39: voicemail_by_elena MUST be False if user_spoke=True.
        # A voicemail system does not have a real conversation. If the user spoke, it's a real call.
        # Previously: when Vapi omitted startedAt/endedAt (duration=0), duration_reliable=False,
        # which made (not duration_reliable) = True, activating voicemail_by_elena even for
        # real conversations that lasted several minutes. This caused outcome=no_contesto
        # for real calls where Elena ended the call (assistant-ended-call) after a full conversation.
        # Fix: add `and not user_spoke` — if the user spoke, it's never a voicemail.
        voicemail_by_elena = (
            ended_reason in (
                "assistant-ended-call",
                "assistant-ended-call-after-message-spoken",
                "assistant-said-end-call-phrase"
            )
            and not user_spoke  # CRITICAL: real humans speak; voicemails don't
            and (not duration_reliable or call_duration_secs < 45)
        )

        # Voicemail that hangs up by itself: customer-ended-call with no user speech.
        # This happens when a voicemail system picks up and then disconnects after Elena speaks.
        # Duration can be up to ~60s (voicemail greeting + Elena talking + voicemail timeout).
        # We do NOT apply this if user_spoke=True because that means a real person answered.
        # FIX1: duration_reliable is no longer required for the same reason as above.
        voicemail_by_customer = (
            ended_reason == "customer-ended-call"
            and not user_spoke
            and (not duration_reliable or call_duration_secs < 60)
        )

        if agendo:
            # Booking confirmed — highest priority, overrides everything
            outcome = "agendo"
            outcome_label = "Agendó"
            stage = "Consulta Agendada"
        elif llamar_luego_confirmed:
            # FIX N: schedule_callback tool executed successfully — second highest priority.
            # This is the ground truth: Elena programmed a callback. No keyword matching needed.
            # Overrides any ended_reason or transcript analysis.
            outcome = "llamar_luego"
            outcome_label = "Llamar Luego"
            stage = "Llamar Luego"
            print(f"[outcome] llamar_luego_confirmed via schedule_callback (hours={callback_hours_confirmed})")
        elif short_call:
            # Call too short to have had a real conversation — treat as no answer
            outcome = "no_contesto"
            outcome_label = "No Contestó"
            stage = "Llamada 1"
        elif shallow_call:
            # FIX F1: Call 20-45s, customer hung up, no tool calls, <=2 user msgs, <=8 words.
            # Client said 'Hello?' or 'Hola?' and hung up before a real conversation started.
            outcome = "no_contesto"
            outcome_label = "No Contestó"
            stage = "Llamada 1"
            print(f"[FIX F1] shallow_call no_contesto: {call_duration_secs:.0f}s, {_user_msg_count} user msgs, {_user_word_count} words")
        elif ended_reason in (
            "voicemail", "no-answer",
            "customer-did-not-answer", "customer-busy"
        ):
            # Pure no-answer: voicemail, no-answer, busy
            outcome = "no_contesto"
            outcome_label = "No Contestó"
            stage = "Llamada 1"
        elif ended_reason == "silence-timed-out" and not user_spoke:
            # FIX2: silence-timed-out with NO user speech = pure no-answer (buzón o nadie contestó)
            outcome = "no_contesto"
            outcome_label = "No Contestó"
            stage = "Llamada 1"
        elif ended_reason == "silence-timed-out" and user_spoke:
            # silence-timed-out AFTER user spoke — could be:
            # A) error_tecnico: Elena said "verificando" but tool call never fired (server timeout)
            # B) llamar_luego: client asked to call back and Elena went silent
            # C) no_agendo: normal conversation that dropped
            # Detect error_tecnico: Elena said "verificando/momentito" in last bot message
            # AND no tool call was ever executed in this call
            last_bot_msg = ""
            has_any_tool_call = False
            for _m in messages_list:
                if _m.get("role") in ("bot", "assistant"):
                    last_bot_msg = _m.get("message", _m.get("content", "")) or ""
                if _m.get("role") in ("tool_calls", "tool_call") or _m.get("type") in ("tool-call", "tool-call-result"):
                    has_any_tool_call = True
            elena_was_verifying = any(ph in last_bot_msg.lower() for ph in VERIFICANDO_PHRASES)
            if elena_was_verifying and not has_any_tool_call:
                outcome = "error_tecnico"
                outcome_label = "Error Técnico"
                stage = "Error Técnico"
            else:
                outcome = "no_agendo"
                outcome_label = "No Agendó"
                stage = "Poco Interes"
        elif voicemail_by_elena:
            # Elena ended the call within 45s — almost certainly a voicemail/answering machine
            # even if Vapi transcribed the voicemail audio as a user message.
            # FIX O — Scenario E: BUT if user actually spoke AND Elena was verifying without
            # executing any tool, this is a technical error, not a voicemail.
            if user_spoke and not has_any_tool_call:
                # Re-use verificando detection computed above (silence-timed-out block)
                # We need to recompute it here since it may not have been computed yet.
                _last_bot_e = ""
                _has_tool_e = False
                for _m in messages_list:
                    if _m.get("role") in ("bot", "assistant"):
                        _last_bot_e = _m.get("message", _m.get("content", "")) or ""
                    if _m.get("role") in ("tool_calls", "tool_call") or _m.get("type") in ("tool-call", "tool-call-result"):
                        _has_tool_e = True
                _elena_verifying_e = any(ph in _last_bot_e.lower() for ph in VERIFICANDO_PHRASES)
                if _elena_verifying_e and not _has_tool_e:
                    outcome = "error_tecnico"
                    outcome_label = "Error Técnico"
                    stage = "Error Técnico"
                    print("[outcome] error_tecnico — Scenario E: assistant-ended-call, user spoke, Elena verifying, no tool calls")
                else:
                    outcome = "no_contesto"
                    outcome_label = "No Contestó"
                    stage = "Llamada 1"
            else:
                outcome = "no_contesto"
                outcome_label = "No Contestó"
                stage = "Llamada 1"
        elif voicemail_by_customer:
            # Voicemail system picked up and disconnected — customer-ended-call, no user speech, < 60s
            outcome = "no_contesto"
            outcome_label = "No Contestó"
            stage = "Llamada 1"
        elif ended_reason in (
            "assistant-ended-call",
            "assistant-ended-call-after-message-spoken",
            "assistant-said-end-call-phrase"
        ) and not user_spoke:
            # Elena ended the call (longer call) and nobody spoke — treat as no answer
            outcome = "no_contesto"
            outcome_label = "No Contestó"
            stage = "Llamada 1"
        else:
            # All other cases: user spoke but didn't book, call dropped mid-conversation,
            # client hung up, Elena hung up after real conversation, or unrecognized endedReason.
            #
            # FIX O — Scenario D: customer-ended-call + user spoke + Elena was verifying + no tool calls
            # = client hung up while Elena was frozen waiting for a tool response.
            # This is a technical error, not a normal no_agendo.
            _last_bot_d = ""
            _has_tool_d = False
            for _m in messages_list:
                if _m.get("role") in ("bot", "assistant"):
                    _last_bot_d = _m.get("message", _m.get("content", "")) or ""
                if _m.get("role") in ("tool_calls", "tool_call") or _m.get("type") in ("tool-call", "tool-call-result"):
                    _has_tool_d = True
            _elena_verifying_d = any(ph in _last_bot_d.lower() for ph in VERIFICANDO_PHRASES)
            if (
                ended_reason == "customer-ended-call"
                and user_spoke
                and _elena_verifying_d
                and not _has_tool_d
            ):
                outcome = "error_tecnico"
                outcome_label = "Error Técnico"
                stage = "Error Técnico"
                print("[outcome] error_tecnico — Scenario D: customer-ended-call, user spoke, Elena verifying, no tool calls")
            else:
                # Check for llamar_luego: client explicitly asked to be called back later
                # FIX N: Keyword fallback — only used when schedule_callback was NOT detected in messages.
                # Expanded keyword list to cover natural speech variations.
                llamar_luego_keywords = [
                    # Direct requests
                    "llámame luego", "llama luego", "llámame después", "llama después",
                    "llámame más tarde", "llama más tarde", "llamar más tarde",
                    "llámame mañana", "llama mañana",
                    # Variations with 'me'
                    "llamarme después", "llamarme más tarde", "llamarme luego",
                    "me llamas después", "me llamas más tarde", "me llamas luego",
                    "me llamas mañana", "me puedes llamar", "puedes llamarme",
                    "me puedes marcar", "puedes marcarme", "me marcas después",
                    "me marcas más tarde", "me marcas luego", "me marcas mañana",
                    # Availability expressions
                    "en otro momento", "ahora no puedo", "ahora mismo no",
                    "no es buen momento", "no tengo tiempo ahora",
                    "estoy ocupada", "estoy ocupado",
                    "ahora estoy ocupada", "ahora estoy ocupado",
                    "ahorita no puedo", "ahorita no", "ahorita estoy",
                    "ocupadita", "ocupadito",
                    "estoy en algo", "estoy en una reunión", "estoy en el trabajo",
                    "estoy manejando", "estoy conduciendo", "estoy en clase",
                    "no puedo hablar ahora", "no puedo hablar en este momento",
                    "este no es buen momento", "no es un buen momento",
                    # Indirect / soft refusals
                    "después te llamo", "te marco después", "yo te llamo",
                    "luego te llamo", "te llamo yo", "te llamo después",
                    "te llamo más tarde", "te llamo mañana",
                    "déjame llamarte", "déjame marcarte",
                    # Future time references
                    "la semana que viene", "la próxima semana", "llámame la próxima",
                    "en unos días", "más adelante", "en otro rato",
                    "pasado mañana", "esta tarde", "esta noche",
                    # Time-based (client accepted callback offer)
                    "en 2 horas", "en dos horas", "en 4 horas", "en cuatro horas",
                    "en 12 horas", "mañana me llamas", "mañana en la mañana", "mañana en la tarde",
                    # Spanglish / Miami Spanish
                    "call me later", "call me back", "call me tomorrow",
                    "llámame back", "llama me back", "call me after",
                    "i\'m busy", "i am busy", "i can\'t talk", "can\'t talk right now",
                    "text me", "mándame un text",
                ]
                transcript_lower = transcript.lower() if transcript else ""
                if any(kw in transcript_lower for kw in llamar_luego_keywords):
                    outcome = "llamar_luego"
                    outcome_label = "Llamar Luego"
                    stage = "Llamar Luego"
                    print("[outcome] llamar_luego via keyword fallback (schedule_callback not in messages)")
                else:
                    outcome = "no_agendo"
                    outcome_label = "No Agendó"
                    stage = "Poco Interes"

        # ── Step 3: Find the GHL contact by phone ─────────────────────────────
        contact_id = ""
        if customer_phone:
            phone_norm, phone_valid = normalize_phone(customer_phone)
            if phone_valid:
                contact_result = handle_get_contact({"phone": phone_norm})
                if contact_result.get("found"):
                    contact_id = contact_result.get("contactId", "")

        # FIX BUG3-v3: Vapi does not always include tool-call results in artifact.messages.
        # If agendo is still False after scanning messages, do a direct GHL lookup:
        # check if an appointment was created for this contact DURING this specific call.
        #
        # FIX F3: Use call.startedAt (the actual call start time) to compare against appt.createdAt.
        # Previous BUG3-v2 used (now_utc - 10min) which caused false positives when:
        # - Contact had a prior call that booked an appointment <10 minutes earlier
        # - New call arrives, BUG3-v2 sees the recent appointment and marks this call as agendo
        # Fix: appointment.createdAt must be AFTER call.startedAt (not just within 10 min of now).
        #
        # FIX B1: Skip BUG3-v3 entirely for calls that never connected to a real human.
        # customer-did-not-answer / no-answer / voicemail / customer-busy = no conversation happened.
        # These calls can NEVER produce a new appointment, so checking GHL is both wrong and
        # dangerous (it picks up appointments from prior calls on the same contact).
        _skip_bug3 = ended_reason in (
            "customer-did-not-answer", "no-answer", "voicemail",
            "customer-busy", "twilio-failed-to-connect-call",
        ) or (outcome == "no_contesto" and not agendo)
        if not agendo and contact_id and not _skip_bug3:
            try:
                appt_check = handle_get_appointment_by_contact({"contactId": contact_id})
                if appt_check.get("found"):
                    appt_start_raw = appt_check.get("startTime", "")
                    appt_id_check = appt_check.get("appointmentId", "")
                    appt_created_raw = appt_check.get("createdAt", "")
                    if appt_id_check:
                        try:
                            now_utc = datetime.now(pytz.utc)
                            appt_dt = datetime.fromisoformat(appt_start_raw.replace("Z", "+00:00")) if appt_start_raw else None
                            # FIX F3: Primary check — appointment.createdAt must be AFTER call.startedAt.
                            # This prevents appointments from prior calls from triggering agendo.
                            if appt_created_raw and call_started_at_str:
                                appt_created_dt = datetime.fromisoformat(appt_created_raw.replace("Z", "+00:00"))
                                call_started_dt = datetime.fromisoformat(call_started_at_str.replace("Z", "+00:00"))
                                # Appointment must have been created AFTER this call started
                                # and the appointment must be in the future (not a past appointment)
                                if appt_dt and appt_dt > now_utc and appt_created_dt >= call_started_dt:
                                    agendo = True
                                    appointment_id = appt_id_check
                                    booked_time = appt_check.get("humanTime", "")
                                    outcome = "agendo"
                                    outcome_label = "Agendó"
                                    stage = "Consulta Agendada"
                                    print(f"[FIX BUG3-v3] agendo via appt.createdAt >= call.startedAt")
                            elif appt_created_raw and not call_started_at_str:
                                # No call.startedAt available — fall back to 5-minute window (tighter than before)
                                appt_created_dt = datetime.fromisoformat(appt_created_raw.replace("Z", "+00:00"))
                                minutes_since_created = (now_utc - appt_created_dt).total_seconds() / 60
                                if appt_dt and appt_dt > now_utc and minutes_since_created <= 5:
                                    agendo = True
                                    appointment_id = appt_id_check
                                    booked_time = appt_check.get("humanTime", "")
                                    outcome = "agendo"
                                    outcome_label = "Agendó"
                                    stage = "Consulta Agendada"
                                    print(f"[FIX BUG3-v3] agendo via createdAt fallback (created {minutes_since_created:.1f}min ago, no call.startedAt)")
                            # If neither condition met: appointment exists but was pre-existing — skip
                        except Exception as _bug3_err:
                            print(f"[WARN] FIX BUG3-v3 parse error: {_bug3_err}")
            except Exception:
                pass

        # ── Step 4: Write custom fields, then apply outcome tag ─────────────────
        # CRITICAL ORDER: custom fields MUST be written BEFORE the tag is added.
        # The GHL workflow ('Vapi - Procesador') fires the instant it detects the tag.
        # If the tag is added first, the workflow reads empty fields — race condition.
        # Fix: write all fields first, then trigger the workflow via tag.
        if contact_id:
            # Step 4a: Write all custom fields first
            _update_contact_custom_field(contact_id, "elena_outcome", outcome_label)
            _update_contact_custom_field(contact_id, "elena_stage", stage)
            _update_contact_custom_field(contact_id, "elena_summary", summary[:1000] if summary else "")
            _update_contact_custom_field(contact_id, "elena_ended_reason", ended_reason)
            _update_contact_custom_field(contact_id, "elena_call_type", call_type)
            if call_id:
                _update_contact_custom_field(contact_id, "elena_call_id", call_id)
            if appointment_id:
                _update_contact_custom_field(contact_id, "elena_appointment_id", appointment_id)
            # B3 FIX: Normalize success_eval to lowercase string before writing.
            # Vapi can return "true"/"false" (str) or True/False (bool) depending on model.
            # str(True) = "True" (capital T) which is inconsistent. Always write lowercase.
            # GHL-FIX: Always write the field so it never shows as empty in GHL.
            # If Vapi didn't return a value (no analysisPlan result yet), write "pending".
            _update_contact_custom_field(
                contact_id,
                "elena_success_eval",
                str(success_eval).lower() if success_eval is not None else "pending"
            )
            # B4 FIX: Write call duration in seconds to GHL for analytics.
            # Allows filtering real conversations from no-answers and measuring avg conversion time.
            # GHL-FIX: Always write the field (even 0) so it's never empty/unrendered in GHL.
            _update_contact_custom_field(contact_id, "elena_call_duration", str(int(call_duration_secs)))
            # Write structured data fields from analysisPlan
            if structured_data:
                if structured_data.get("interest_level"):
                    _update_contact_custom_field(contact_id, "elena_interest_level", structured_data["interest_level"])
                if structured_data.get("main_objection"):
                    _update_contact_custom_field(contact_id, "elena_main_objection", structured_data["main_objection"])
                if structured_data.get("language"):
                    _update_contact_custom_field(contact_id, "elena_language", structured_data["language"])
                if structured_data.get("callback_requested") is not None:
                    _update_contact_custom_field(contact_id, "elena_callback_requested", str(structured_data["callback_requested"]))
                if structured_data.get("pivot_needed") is not None:
                    _update_contact_custom_field(contact_id, "elena_pivot_needed", str(structured_data["pivot_needed"]))

            # Step 4c: Increment call counters (metrics only — not used for workflow logic)
            # elena_total_calls: every call, regardless of outcome
            # elena_conversations: only calls where the client actually spoke (outcome != no_contesto)
            try:
                contact_resp = http_requests.get(
                    f"{GHL_V2_BASE}/contacts/{contact_id}",
                    headers=v2_headers_contacts(),
                    timeout=10
                )
                if contact_resp.status_code == 200:
                    existing_fields = contact_resp.json().get("contact", {}).get("customFields", [])
                    fields_map = {f.get("key", ""): f.get("value", 0) for f in existing_fields}
                    current_total = int(fields_map.get("elena_total_calls", 0) or 0)
                    current_convos = int(fields_map.get("elena_conversations", 0) or 0)
                    _update_contact_custom_field(contact_id, "elena_total_calls", current_total + 1)
                    if outcome != "no_contesto":
                        _update_contact_custom_field(contact_id, "elena_conversations", current_convos + 1)
            except Exception as _counter_err:
                print(f"[WARN] Counter update failed: {_counter_err}")

            # Step 4b: Write elena_last_outcome custom field, then add generic trigger tag.
            # ARCHITECTURE: elena_last_outcome is the source of truth (single value, no accumulation).
            # elena_resultado_botox is a generic tag used only to fire the GHL workflow trigger.
            # The workflow reads elena_last_outcome (not the tag) to decide which branch to take.
            # The workflow removes elena_resultado_botox at the end of each branch so it can
            # re-trigger on the next call.
            _update_contact_custom_field(contact_id, "elena_last_outcome", outcome)
            _add_tag_to_contact(contact_id, "elena_resultado_botox")
            # Save full transcript as a note on the GHL contact
            # This allows team members to read the full conversation from GHL
            if transcript:
                _tz = pytz.timezone("America/New_York")
                _now = datetime.now(_tz).strftime("%Y-%m-%d %H:%M EDT")
                note = (
                    f"--- LLAMADA DE ELENA AI ({_now}) ---\n"
                    f"Resultado: {outcome_label}\n"
                    f"Motivo de fin: {ended_reason}\n"
                    f"Resumen: {summary}\n"
                    f"Call ID Vapi: {call_id}\n\n"
                    f"TRANSCRIPCIÓN COMPLETA:\n{transcript}"
                )
                _add_note_to_contact(contact_id, note)

    except Exception as e:
        print(f"[ERROR] _process_end_of_call failed: {e}")  # Never fail webhook but always log


# ─── Tool Registry ────────────────────────────────────────────────────────────
TOOL_HANDLERS = {
    "check_availability": handle_check_availability,
    "get_contact": handle_get_contact,
    "create_contact": handle_create_contact,
    "create_booking": handle_create_booking,
    "reschedule_appointment": handle_reschedule_appointment,
    "cancel_appointment": handle_cancel_appointment,
    "get_appointment_by_contact": handle_get_appointment_by_contact,
    "schedule_callback": handle_schedule_callback,
    "get_current_time": handle_get_current_time,  # FIX M: real Miami time for callback math
}


# ─── Vapi Server URL Endpoint ─────────────────────────────────────────────────
@app.route("/api/vapi/server-url", methods=["POST", "OPTIONS", "GET"])
def vapi_server_url():
    """Main entry point for Vapi tool calls.
    
    FIX H: Extracts caller phone from call.customer.number and injects it
    into every tool call as 'callerPhone' argument. This ensures get_contact
    and create_contact always have access to the real caller number even if
    the client gives a fake/wrong number.
    """
    if request.method == "OPTIONS":
        return "", 204, {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }

    cors = {"Access-Control-Allow-Origin": "*"}

    if request.method == "GET":
        return jsonify({
            "status": "healthy",
            "service": f"Elena AI - Vapi Tool Server {SERVER_VERSION}",
            "tools": list(TOOL_HANDLERS.keys()),
            "calendar_id": CALENDAR_ID,
            "using_pit": bool(GHL_PIT)
        }), 200, cors

    try:
        body = request.get_json(silent=True) or {}
        message = body.get("message", body)
        message_type = message.get("type", "")

        # FIX H: Extract caller phone from call.customer.number
        call_data = message.get("call", {})
        caller_phone = call_data.get("customer", {}).get("number", "")

        if message_type == "tool-calls":
            tool_calls = message.get("toolCallList", [])
            results = []

            for tool_call in tool_calls:
                fn_name = tool_call.get("function", {}).get("name", "")
                arguments = tool_call.get("function", {}).get("arguments", {})
                tc_id = tool_call.get("id", "")

                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                # FIX H: Inject caller phone into every tool call
                if caller_phone and isinstance(arguments, dict):
                    arguments["callerPhone"] = caller_phone

                handler = TOOL_HANDLERS.get(fn_name)
                if handler:
                    try:
                        result = handler(arguments)
                    except Exception as e:
                        result = {"error": f"Error ejecutando {fn_name}: {str(e)}"}
                else:
                    result = {"error": f"Función no reconocida: {fn_name}"}

                results.append({
                    "toolCallId": tc_id,
                    "result": json.dumps(result, ensure_ascii=False)
                })

            return jsonify({"results": results}), 200, cors

        elif message_type == "function-call":
            fn_name = message.get("functionCall", {}).get("name", "")
            arguments = message.get("functionCall", {}).get("parameters", {})

            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}

            # FIX H: Inject caller phone
            if caller_phone and isinstance(arguments, dict):
                arguments["callerPhone"] = caller_phone

            handler = TOOL_HANDLERS.get(fn_name)
            if handler:
                try:
                    result = handler(arguments)
                except Exception as e:
                    result = {"error": f"Error ejecutando {fn_name}: {str(e)}"}
            else:
                result = {"error": f"Función no reconocida: {fn_name}"}

            return jsonify({"result": json.dumps(result, ensure_ascii=False)}), 200, cors

        elif message_type == "end-of-call-report":
            # FIX P: Run end-of-call processing in a background thread.
            # This releases the gunicorn worker immediately (200 OK to Vapi in <1ms)
            # while GHL writes happen asynchronously. Critical for 100+ simultaneous calls
            # where synchronous processing would block all workers and cause Vapi timeouts.
            t = threading.Thread(target=_process_end_of_call, args=(message,), daemon=True)
            t.start()

            # ARIA: auditar la llamada en tiempo real (thread separado, no bloquea)
            try:
                call_data = message.get("call") or message
                def _aria_realtime_audit(cd):
                    """Ejecuta el audit real-time sin depender del módulo cacheado.
                    Usa aria_audit directamente si ya está importado, o lo importa una vez.
                    El módulo usa os.environ.get() internamente — las env vars ya están
                    disponibles en el proceso Flask desde el inicio.
                    """
                    import logging as _log
                    _aria_log = _log.getLogger('aria')
                    try:
                        # Importar sin reload — las env vars ya están en os.environ
                        # desde que el proceso Flask arrancó en Render
                        import aria_audit as _aria
                        _aria_log.info(f'ARIA realtime: iniciando audit para call {str(cd.get("id",""))[:20]}')
                        result = _aria.process_single_call_realtime(cd)
                        if result:
                            _aria_log.info(f'ARIA realtime: audit completado — discrepancy={result.get("has_discrepancy")} outcome={result.get("aria_outcome")}')
                        else:
                            _aria_log.warning('ARIA realtime: audit retornó None (llamada ya auditada o sin datos)')
                    except Exception as _e:
                        _aria_log.error(f'ARIA realtime audit error: {type(_e).__name__}: {_e}', exc_info=True)
                aria_t = threading.Thread(target=_aria_realtime_audit, args=(call_data,), daemon=False)  # FIX C1: non-daemon — ARIA thread sobrevive reciclado de worker Gunicorn
                aria_t.start()
                logging.getLogger('aria').info('ARIA realtime: thread iniciado (non-daemon)')
            except Exception as _hook_e:
                logging.getLogger('aria').error(f'ARIA realtime hook error: {_hook_e}')  # Log pero no bloquea Elena

            return jsonify({"status": "ok"}), 200, cors

        elif message_type in ["status-update", "hang", "speech-update", "transcript"]:
            return jsonify({"status": "ok"}), 200, cors

        else:
            return jsonify({"status": "ok"}), 200, cors

    except Exception as e:
        return jsonify({"error": str(e)}), 500, cors


# ─── Date Update Endpoint ─────────────────────────────────────────────────────
@app.route("/update-date", methods=["POST", "GET"])
def update_date():
    """Update the Vapi assistant system prompt with today's date.
    
    FIX G: Retries up to 3 times on failure.
    Always includes current tools in PATCH to avoid deleting them.
    """
    now = datetime.now(TZ)
    tomorrow = now + timedelta(days=1)
    today_str = f"{DAYS_ES[now.weekday()]} {now.day} de {MONTHS_ES[now.month-1]} de {now.year}"
    tomorrow_str = f"{DAYS_ES[tomorrow.weekday()]} {tomorrow.day} de {MONTHS_ES[tomorrow.month-1]} de {tomorrow.year}"

    date_section = f"""
## ⚡ FECHA ACTUAL (ACTUALIZADO AUTOMÁTICAMENTE)
**HOY es {today_str}.** Zona horaria: Miami, Florida (Eastern Time, UTC-4).
**MAÑANA es {tomorrow_str}.**
- Cuando el cliente diga **"mañana"** → se refiere al **{tomorrow.day} de {MONTHS_ES[tomorrow.month-1]} de {tomorrow.year}** ({DAYS_ES[tomorrow.weekday()]})
- Cuando el cliente diga **"hoy"** → se refiere al **{now.day} de {MONTHS_ES[now.month-1]} de {now.year}** ({DAYS_ES[now.weekday()]})
- Cuando el cliente diga **"esta semana"** → los días restantes de esta semana desde hoy
**USA SIEMPRE estas fechas. NUNCA inventes ni asumas una fecha diferente.**

"""

    # Read base prompt (without any existing date section)
    base_prompt_path = os.path.join(os.path.dirname(__file__), "system_prompt.txt")
    if not os.path.exists(base_prompt_path):
        return jsonify({"error": "system_prompt.txt not found"}), 500

    with open(base_prompt_path, "r", encoding="utf-8") as f:
        base_prompt = f.read()

    # Insert date section after the title line (position 2)
    lines = base_prompt.split("\n")
    insert_pos = 2
    new_lines = lines[:insert_pos] + date_section.split("\n") + lines[insert_pos:]
    full_prompt = "\n".join(new_lines)

    # FIX G: Retry up to 3 times
    last_error = None
    for attempt in range(3):
        try:
            # Get current assistant config to preserve tools, analysisPlan and other settings
            current_resp = http_requests.get(
                f"https://api.vapi.ai/assistant/{VAPI_ASSISTANT_ID}",
                headers={"Authorization": f"Bearer {VAPI_KEY}"},
                timeout=10
            )
            current_assistant = current_resp.json()
            current_model = current_assistant.get("model", {})
            current_tools = current_model.get("tools", [])
            current_tool_ids = current_model.get("toolIds", [])
            # B5 FIX: Preserve analysisPlan — previously this PATCH deleted it every day.
            # analysisPlan lives at the assistant level (not inside model), so we must
            # read it from the current assistant config and re-include it in the PATCH.
            current_analysis_plan = current_assistant.get("analysisPlan", {})

            # Update Vapi assistant system prompt — MUST include tools and analysisPlan to avoid deleting them
            patch_body = {
                "model": {
                    "provider": "openai",
                    "model": current_model.get("model", "gpt-4o-mini"),
                    "messages": [{"role": "system", "content": full_prompt}]
                }
            }
            if current_tools:
                patch_body["model"]["tools"] = current_tools
            if current_tool_ids:
                patch_body["model"]["toolIds"] = current_tool_ids
            # B5 FIX: Re-include analysisPlan so it survives daily date updates
            if current_analysis_plan:
                patch_body["analysisPlan"] = current_analysis_plan

            resp = http_requests.patch(
                f"https://api.vapi.ai/assistant/{VAPI_ASSISTANT_ID}",
                headers={"Authorization": f"Bearer {VAPI_KEY}", "Content-Type": "application/json"},
                json=patch_body,
                timeout=20
            )

            if resp.status_code == 200:
                return jsonify({
                    "success": True,
                    "today": today_str,
                    "tomorrow": tomorrow_str,
                    "prompt_length": len(full_prompt),
                    "tools_preserved": len(current_tools),
                    "analysis_plan_preserved": bool(current_analysis_plan),  # B5 FIX
                    "attempt": attempt + 1
                })
            else:
                last_error = resp.text[:300]
        except Exception as e:
            last_error = str(e)

    return jsonify({"success": False, "error": last_error, "attempts": 3}), 500


# ─── ARIA — Endpoints de Aprobación/Rechazo de Correcciones ───────────────────────────────

@app.route("/aria/telegram/webhook", methods=["POST"])
def aria_telegram_webhook():
    """
    Webhook de Telegram unificado:
    - Callbacks de botones inline (APROBAR / RECHAZAR)
    - Comandos de texto (/score, /reporte, /errores, /eficacia, /audit, /llamada)
    """
    import os, logging, threading
    aria_log = logging.getLogger("aria")

    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"ok": True})

        # ── Comandos de texto ──────────────────────────────────────────────────
        msg = data.get("message") or data.get("edited_message")
        if msg and not data.get("callback_query"):
            text = msg.get("text", "").strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if text and text.startswith("/"):
                parts = text.split(" ", 1)
                command = parts[0].lower().split("@")[0]
                args = parts[1] if len(parts) > 1 else ""
                aria_log.info(f"TELEGRAM COMMAND: {command} {args} from {chat_id}")
                def _handle_cmd(cmd, a, cid):
                    try:
                        import sys, importlib
                        if 'aria_audit' in sys.modules:
                            mod = importlib.reload(sys.modules['aria_audit'])
                        else:
                            mod = importlib.import_module('aria_audit')
                        mod.telegram_handle_command(cmd, a, cid)
                    except Exception as _e:
                        aria_log.error(f"TELEGRAM CMD error: {_e}", exc_info=True)
                        BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                        if BOT_TOKEN and chat_id:
                            http_requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                json={"chat_id": cid, "text": f"⚠️ Error: {str(_e)[:100]}"},
                                timeout=5
                            )
                threading.Thread(target=_handle_cmd, args=(command, args, chat_id), daemon=True).start()
            return jsonify({"ok": True})

        # ── Callbacks de botones ───────────────────────────────────────────────
        callback_query = data.get("callback_query")
        if not callback_query:
            return jsonify({"ok": True})

        callback_id   = callback_query.get("id")
        callback_data = callback_query.get("data", "")
        from_user     = callback_query.get("from", {})
        message       = callback_query.get("message", {})
        chat_id       = message.get("chat", {}).get("id")
        message_id    = message.get("message_id")

        # Leer credenciales directamente del entorno en tiempo de ejecución
        BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        SUPA_URL       = os.environ.get("SUPABASE_URL", "")
        SUPA_KEY       = os.environ.get("SUPABASE_SERVICE_KEY", "")
        GHL_TOKEN      = os.environ.get("GHL_PIT", "")

        def tg_answer(text):
            try:
                http_requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": callback_id, "text": text},
                    timeout=5
                )
            except Exception:
                pass

        def tg_send(text):
            try:
                http_requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=5
                )
            except Exception:
                pass

        def tg_edit(text):
            try:
                http_requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": text[:4096],
                        "parse_mode": "HTML",
                        "reply_markup": {"inline_keyboard": []}
                    },
                    timeout=5
                )
            except Exception:
                pass

        def supa_get(correction_id):
            r = http_requests.get(
                f"{SUPA_URL}/rest/v1/aria_corrections",
                headers={"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}"},
                params={"id": f"eq.{correction_id}", "select": "*"},
                timeout=10
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
            return None

        def supa_patch(table, filter_key, filter_val, data):
            r = http_requests.patch(
                f"{SUPA_URL}/rest/v1/{table}",
                headers={
                    "apikey": SUPA_KEY,
                    "Authorization": f"Bearer {SUPA_KEY}",
                    "Content-Type": "application/json"
                },
                params={filter_key: f"eq.{filter_val}"},
                json=data,
                timeout=10
            )
            return r.status_code in (200, 204)

        def ghl_update(contact_id, new_outcome):
            r = http_requests.put(
                f"https://services.leadconnectorhq.com/contacts/{contact_id}",
                headers={
                    "Authorization": f"Bearer {GHL_TOKEN}",
                    "Version": "2021-07-28",
                    "Content-Type": "application/json"
                },
                json={"customFields": [{"key": "elena_last_outcome", "field_value": new_outcome}]},
                timeout=10
            )
            return r.status_code in (200, 201)

        # Validar formato del callback
        if ":" not in callback_data:
            tg_answer("Formato inválido")
            return jsonify({"ok": True})

        action, correction_id = callback_data.split(":", 1)
        approved = (action == "approve")

        # Responder inmediatamente a Telegram (quita el spinner)
        tg_answer("✅ Procesando..." if approved else "❌ Procesando...")

        aria_log.info(f"WEBHOOK: action={action} correction_id={correction_id}")

        # Validar credenciales
        if not SUPA_KEY or not SUPA_URL:
            aria_log.error("WEBHOOK: SUPABASE_SERVICE_KEY o SUPABASE_URL no configurados")
            tg_send("⚠️ Error interno: credenciales de Supabase no disponibles.")
            return jsonify({"ok": True})

        # Obtener la corrección de Supabase
        correction = supa_get(correction_id)
        if not correction:
            aria_log.error(f"WEBHOOK: corrección {correction_id} no encontrada")
            tg_send(f"⚠️ Corrección no encontrada: {correction_id[:8]}...")
            return jsonify({"ok": True})

        current_status = correction.get("correction_status")
        if current_status != "pending":
            aria_log.warning(f"WEBHOOK: corrección {correction_id[:8]} ya procesada (status={current_status})")
            tg_send(f"ℹ️ Esta corrección ya fue procesada (status: {current_status}).")
            return jsonify({"ok": True})

        ghl_contact_id = correction.get("ghl_contact_id")
        old_value      = correction.get("old_value")
        new_value      = correction.get("new_value")
        audit_id       = correction.get("audit_id")
        vapi_call_id   = correction.get("vapi_call_id")

        if approved:
            # Aplicar en GHL
            ghl_ok = ghl_update(ghl_contact_id, new_value) if GHL_TOKEN else False
            new_status = "applied" if ghl_ok else "pending"
            ghl_code   = 200 if ghl_ok else 500
            aria_log.info(f"WEBHOOK: GHL update {'OK' if ghl_ok else 'FAILED'} for contact {ghl_contact_id}")

            # Actualizar audit_status
            if ghl_ok and audit_id:
                supa_patch("call_audits", "id", audit_id, {"audit_status": "feedback_approved"})
        else:
            new_status = "reverted"
            ghl_code   = None
            ghl_ok     = True
            if audit_id:
                supa_patch("call_audits", "id", audit_id, {"audit_status": "feedback_rejected"})

        # Actualizar corrección en Supabase
        supa_patch("aria_corrections", "id", correction_id, {
            "correction_status": new_status,
            "ghl_response_code": ghl_code
        })

        # Insertar en feedback_log
        try:
            http_requests.post(
                f"{SUPA_URL}/rest/v1/feedback_log",
                headers={
                    "apikey": SUPA_KEY,
                    "Authorization": f"Bearer {SUPA_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal"
                },
                json={
                    "audit_id": audit_id,
                    "vapi_call_id": vapi_call_id,
                    "feedback_type": "approved" if approved else "rejected",
                    "feedback_source": "telegram",
                    "original_outcome": old_value,
                    "aria_outcome": new_value,
                    "final_outcome": new_value if approved else old_value,
                    "notes": f"Telegram: {'aprobado' if approved else 'rechazado'} por {from_user.get('first_name', 'Juan')}"
                },
                timeout=5
            )
        except Exception:
            pass  # feedback_log no es crítico

        # Notificar resultado a Juan
        call_short = (vapi_call_id or "")[:20]
        if approved and ghl_ok:
            result_text = (
                f"✅ <b>Corrección aplicada en GHL</b>\n"
                f"<code>{call_short}...</code>\n"
                f"Outcome: <b>{old_value}</b> → <b>{new_value}</b>"
            )
        elif approved and not ghl_ok:
            result_text = (
                f"⚠️ <b>Aprobado pero GHL devolvió error</b>\n"
                f"Corrección marcada como pendiente. Revisa Supabase."
            )
        else:
            result_text = (
                f"❌ <b>Corrección rechazada</b>\n"
                f"<code>{call_short}...</code>\n"
                f"Se mantiene: <b>{old_value}</b>"
            )

        tg_send(result_text)

        # Editar el mensaje original para remover los botones
        original_text = message.get("text", "")
        status_label  = "✅ APROBADO" if approved else "❌ RECHAZADO"
        tg_edit(f"{original_text}\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n<b>{status_label}</b> por {from_user.get('first_name', 'Juan')}")

        aria_log.info(f"WEBHOOK: correction {correction_id[:8]} → {new_status}")
        return jsonify({"ok": True})

    except Exception as e:
        aria_log.error(f"WEBHOOK EXCEPTION: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({"ok": True})  # Siempre 200 a Telegram


@app.route("/aria/correction/<correction_id>/approve", methods=["GET", "POST"])
def aria_approve_correction(correction_id):
    """
    Endpoint alternativo para aprobar una corrección via link directo.
    Usado como fallback si los botones de Telegram no funcionan.
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from aria_audit import apply_correction
        result = apply_correction(correction_id, approved=True)
        if result.get("success"):
            return jsonify({"status": "applied", "message": f"Corrección aplicada: {result.get('old_value')} → {result.get('new_value')}", **result})
        else:
            return jsonify({"status": "error", **result}), 400
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/aria/correction/<correction_id>/reject", methods=["GET", "POST"])
def aria_reject_correction(correction_id):
    """
    Endpoint alternativo para rechazar una corrección via link directo.
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from aria_audit import apply_correction
        result = apply_correction(correction_id, approved=False)
        if result.get("success"):
            return jsonify({"status": "rejected", "message": f"Corrección rechazada. Se mantiene: {result.get('old_value')}", **result})
        else:
            return jsonify({"status": "error", **result}), 400
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/aria/corrections/pending", methods=["GET"])
def aria_pending_corrections():
    """
    Listar todas las correcciones pendientes de aprobación.
    """
    import os
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not SUPABASE_SERVICE_KEY:
        return jsonify({"error": "SUPABASE_SERVICE_KEY no configurado"}), 500

    r = http_requests.get(
        f"{SUPABASE_URL}/rest/v1/aria_corrections",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
        },
        params={"correction_status": "eq.pending", "select": "*", "order": "created_at.desc"},
        timeout=10
    )
    if r.status_code == 200:
        corrections = r.json()
        return jsonify({"pending": len(corrections), "corrections": corrections})
    return jsonify({"error": r.text[:200]}), 500


# ─── ARIA Diagnóstico ─────────────────────────────────────────────────────────────────────────────

@app.route("/aria/diag/webhook", methods=["POST"])
def aria_diag_webhook():
    """
    Endpoint de diagnóstico: ejecuta exactamente el mismo código del webhook
    y devuelve el resultado completo incluyendo errores.
    """
    import os, traceback as tb
    data = request.get_json(force=True) or {}
    correction_id = data.get("correction_id", "")
    approved = data.get("approved", False)

    result = {"correction_id": correction_id, "approved": approved}

    try:
        SUPA_URL = os.environ.get("SUPABASE_URL", "")
        SUPA_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
        GHL_TOKEN = os.environ.get("GHL_PIT", "")

        result["env"] = {
            "SUPA_URL": SUPA_URL[:30] if SUPA_URL else "EMPTY",
            "SUPA_KEY": SUPA_KEY[:20] + "..." if SUPA_KEY else "EMPTY",
            "GHL_TOKEN": GHL_TOKEN[:15] + "..." if GHL_TOKEN else "EMPTY"
        }

        if not SUPA_KEY:
            result["error"] = "SUPABASE_SERVICE_KEY vacío"
            return jsonify(result), 500

        # Obtener corrección
        r = http_requests.get(
            f"{SUPA_URL}/rest/v1/aria_corrections",
            headers={"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}"},
            params={"id": f"eq.{correction_id}", "select": "*"},
            timeout=10
        )
        result["supa_get_status"] = r.status_code
        result["supa_get_body"] = r.json() if r.status_code == 200 else r.text[:200]

        if r.status_code != 200 or not r.json():
            result["error"] = "Corrección no encontrada"
            return jsonify(result), 404

        correction = r.json()[0]
        result["correction"] = correction

        if correction.get("correction_status") != "pending":
            result["error"] = f"Ya procesada: {correction.get('correction_status')}"
            return jsonify(result), 200

        # Patch Supabase
        patch_data = {"correction_status": "reverted" if not approved else "applied"}
        rp = http_requests.patch(
            f"{SUPA_URL}/rest/v1/aria_corrections",
            headers={"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}", "Content-Type": "application/json"},
            params={"id": f"eq.{correction_id}"},
            json=patch_data,
            timeout=10
        )
        result["supa_patch_status"] = rp.status_code
        result["supa_patch_ok"] = rp.status_code in (200, 204)
        result["success"] = True

    except Exception as e:
        result["exception"] = type(e).__name__
        result["exception_msg"] = str(e)
        result["traceback"] = tb.format_exc()

    return jsonify(result)


# ─── ARIA: Vapi End-of-Call Webhook ──────────────────────────────────────────────────────────────
@app.route("/aria/vapi/end-of-call", methods=["POST"])
def aria_vapi_end_of_call():
    """
    Webhook disparado por Vapi cuando termina cada llamada.
    Audita la llamada en tiempo real con Claude y notifica a Juan por Telegram
    si detecta una discrepancia con la clasificación de GHL.

    Configurar en Vapi: Server URL → https://elena-pdem.onrender.com/aria/vapi/end-of-call
    Events: end-of-call-report
    """
    import os, logging, threading
    aria_log = logging.getLogger("aria")

    try:
        data = request.get_json(force=True) or {}
        msg_type = data.get("message", {}).get("type") if isinstance(data.get("message"), dict) else data.get("type")

        # Vapi puede enviar el payload directo o dentro de data.message
        call_data = data.get("message", {}).get("call") or data.get("call") or data.get("message") or data

        # Solo procesar end-of-call-report
        if msg_type and msg_type != "end-of-call-report":
            return jsonify({"ok": True, "skipped": f"type={msg_type}"})

        call_id = call_data.get("id") or data.get("message", {}).get("call", {}).get("id")
        if not call_id:
            aria_log.warning("VAPI WEBHOOK: no call_id in payload")
            return jsonify({"ok": True, "skipped": "no call_id"})

        aria_log.info(f"VAPI WEBHOOK: end-of-call received for call {call_id}")

        # Procesar en background para no bloquear la respuesta a Vapi
        def process_async():
            try:
                from aria_audit import process_single_call_realtime
                result = process_single_call_realtime(call_data)
                if result:
                    aria_log.info(f"VAPI WEBHOOK: call {call_id} audited — outcome={result.get('aria_outcome')} discrepancy={result.get('has_discrepancy')}")
                else:
                    aria_log.info(f"VAPI WEBHOOK: call {call_id} skipped (already audited or not ended)")
            except Exception as e:
                aria_log.error(f"VAPI WEBHOOK async error: {type(e).__name__}: {e}", exc_info=True)

        thread = threading.Thread(target=process_async, daemon=True)
        thread.start()

        return jsonify({"ok": True, "call_id": call_id, "status": "processing"})

    except Exception as e:
        logging.getLogger("aria").error(f"VAPI WEBHOOK EXCEPTION: {e}", exc_info=True)
        return jsonify({"ok": True})



# ─── ARIA: Manual Report Triggers ─────────────────────────────────────────────────────────────────
@app.route("/aria/report/daily", methods=["POST"])
def aria_trigger_daily_report():
    """Trigger manual del reporte diario (usado por el cron a las 8PM EDT)."""
    import threading
    def run_async():
        try:
            from aria_audit import run_daily_report
            run_daily_report()
        except Exception as e:
            logging.getLogger("aria").error(f"Daily report error: {e}", exc_info=True)
    threading.Thread(target=run_async, daemon=True).start()
    return jsonify({"ok": True, "status": "daily report triggered"})


@app.route("/aria/report/weekly", methods=["POST"])
def aria_trigger_weekly_report():
    """Trigger manual del reporte semanal (usado por el cron los domingos a las 8AM EDT)."""
    import threading
    def run_async():
        try:
            from aria_audit import run_weekly_report
            run_weekly_report()
        except Exception as e:
            logging.getLogger("aria").error(f"Weekly report error: {e}", exc_info=True)
    threading.Thread(target=run_async, daemon=True).start()
    return jsonify({"ok": True, "status": "weekly report triggered"})


@app.route("/aria/audit/run", methods=["POST"])
def aria_trigger_audit():
    """Trigger manual de un ciclo de auditoría (usado por el cron y on-demand)."""
    import threading
    data = request.get_json(force=True) or {}
    hours_back = int(data.get("hours_back", 25))
    def run_async():
        try:
            from aria_audit import run_audit
            run_audit(hours_back=hours_back)
        except Exception as e:
            logging.getLogger("aria").error(f"Audit run error: {e}", exc_info=True)
    threading.Thread(target=run_async, daemon=True).start()
    return jsonify({"ok": True, "status": f"audit triggered for last {hours_back}h"})


# ─── Health Check ──────────────────────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    now = datetime.now(TZ)
    return jsonify({
        "status": "healthy",
        "service": f"Elena AI Tool Server {SERVER_VERSION}",
        "using_pit": bool(GHL_PIT),
        "calendar_id": CALENDAR_ID,
        "current_time_miami": now.strftime("%Y-%m-%d %H:%M %Z"),
        "today": f"{DAYS_ES[now.weekday()]} {now.day} de {MONTHS_ES[now.month-1]} de {now.year}"
    })


# ─── ARIA Polling — Iniciar al arrancar el servidor ───────────────────────────
try:
    from aria_audit import start_aria_polling
    start_aria_polling(interval_seconds=180)  # cada 3 minutos
except Exception as _polling_err:
    logging.getLogger("aria").error(f"Error iniciando ARIA Polling: {_polling_err}")

# ─── ARIA Weekly Cron — Reporte de errores cada sábado 9:00 AM EDT ──────────
try:
    from aria_audit import start_weekly_cron
    start_weekly_cron()
except Exception as _weekly_err:
    logging.getLogger("aria").error(f"Error iniciando ARIA Weekly Cron: {_weekly_err}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)


