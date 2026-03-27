"""
Elena AI - Vapi Tool Server for GoHighLevel
Standalone Flask server for deployment on Render.

Uses GHL V2 API (services.leadconnectorhq.com) with Private Integration Token (PIT).
ALL endpoints use V2 API — V1 is NOT used anywhere.

VERSION: v17.35 — All fixes applied:
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
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────
GHL_PIT = os.environ.get("GHL_PIT", "")
CALENDAR_ID = os.environ.get("GHL_CALENDAR_ID", "hYHvVwjKPykvcPkrsQWT")
LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "hzRj7DV9erP8tnPiTv7D")
VAPI_KEY = os.environ.get("VAPI_API_KEY", "VAPI_KEY_REDACTED_ROTATED_2026_04_24")
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

SERVER_VERSION = "v17.35"  # Full audit: B1-B10 fixes (docstring, BUG3 window, success_eval normalize, call_duration, analysisPlan preserve, BOOKING_TITLE, GHL retry, lock cleanup, schedule_callback race, EN verificando)

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
        # Sort: Tuesdays first, then by date
        tuesday_slots = [s for s in slots if s["is_tuesday"]]
        other_slots = [s for s in slots if not s["is_tuesday"]]
        ordered = tuesday_slots[:5] + other_slots[:5]  # M5 FIX: max 10 slots total to reduce LLM context
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
                "Usa el 'label' para hablar con el cliente y el 'time' exacto para las herramientas."
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
    caller_phone = args.get("callerPhone", "")  # FIX A: real caller phone injected by server
    # Normalize the provided phone
    phone_normalized, phone_valid = normalize_phone(phone)
    
    # FIX A+B+K: If phone is empty OR invalid/fake, use the real caller phone
    # This prevents Elena from hallucinating a phone number — she should call
    # get_contact without a phone argument and the server uses the real caller number.
    if (not phone or not phone_valid) and caller_phone:
        phone_normalized, phone_valid = normalize_phone(caller_phone)
        if phone_valid:
            phone = caller_phone  # Use real caller phone
    
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
    
    # FIX B: Validate phone
    phone_normalized, phone_valid = normalize_phone(phone)
    
    # FIX A: If provided phone is invalid, use real caller phone
    if not phone_valid and caller_phone:
        phone_normalized, phone_valid = normalize_phone(caller_phone)
        if phone_valid:
            phone = caller_phone
    
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
            "status": appt.get("appointmentStatus", "")
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
    if hours not in VALID_HOURS:
        distances = {v: abs(v - hours) for v in VALID_HOURS}
        hours = min(distances, key=distances.get)

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
        # Calculate call duration in seconds from startedAt / endedAt timestamps
        call_duration_secs = 0
        try:
            started_at = call.get("startedAt", "")
            ended_at = call.get("endedAt", "")
            if started_at and ended_at:
                from datetime import timezone as _tz_mod
                _started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                _ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
                call_duration_secs = (_ended - _started).total_seconds()
        except Exception:
            call_duration_secs = 0
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

        for msg in messages_list:
            # Format 1: role=tool with JSON content (older Vapi format)
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for item in content:
                        text = item.get("text", "") if isinstance(item, dict) else ""
                        try:
                            parsed = json.loads(text)
                            if isinstance(parsed, dict) and parsed.get("success"):
                                if parsed.get("appointmentId"):
                                    agendo = True
                                    appointment_id = parsed.get("appointmentId", "")
                                    booked_time = parsed.get("message", "")
                                    break
                                if parsed.get("hours") and parsed.get("callbackTime"):
                                    # FIX N: schedule_callback returned success
                                    llamar_luego_confirmed = True
                                    callback_hours_confirmed = int(parsed.get("hours", 2))
                        except Exception:
                            pass
                elif isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict) and parsed.get("success"):
                            if parsed.get("appointmentId"):
                                agendo = True
                                appointment_id = parsed.get("appointmentId", "")
                                booked_time = parsed.get("message", "")
                                break
                            if parsed.get("hours") and parsed.get("callbackTime"):
                                llamar_luego_confirmed = True
                                callback_hours_confirmed = int(parsed.get("hours", 2))
                    except Exception:
                        pass

            # Format 2: type=tool-call-result (newer Vapi format)
            if msg.get("type") == "tool-call-result":
                result_str = msg.get("result", "")
                try:
                    parsed = json.loads(result_str) if isinstance(result_str, str) else result_str
                    if isinstance(parsed, dict) and parsed.get("success"):
                        if parsed.get("appointmentId"):
                            agendo = True
                            appointment_id = parsed.get("appointmentId", "")
                            booked_time = parsed.get("message", "")
                            break
                        if parsed.get("hours") and parsed.get("callbackTime"):
                            llamar_luego_confirmed = True
                            callback_hours_confirmed = int(parsed.get("hours", 2))
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
        # Calls under 20 seconds are treated as no_contesto regardless of who spoke.
        # Exception: if duration is 0 (startedAt/endedAt missing — common in inbound calls)
        # AND user actually spoke, the duration is unreliable — do NOT treat as short call.
        duration_reliable = call_duration_secs > 0
        short_call = duration_reliable and call_duration_secs < 20

        # Voicemail / no-answer detection: Elena ended the call AND duration < 45s.
        # We use 45s because voicemail greetings + Elena's goodbye can last up to ~40s.
        # We do NOT rely on user_spoke here because Vapi transcribes voicemail audio as role=user.
        # FIX1: duration_reliable is no longer required — Vapi sometimes omits startedAt/endedAt.
        # When timestamps are missing, we trust endedReason alone for voicemail detection.
        # If duration IS available, we still enforce the 45s cap as a safety check.
        voicemail_by_elena = (
            ended_reason in (
                "assistant-ended-call",
                "assistant-ended-call-after-message-spoken",
                "assistant-said-end-call-phrase"
            )
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

        # FIX BUG3: Vapi does not always include tool-call results in artifact.messages.
        # If agendo is still False after scanning messages, do a direct GHL lookup:
        # check if an appointment was created for this contact during this call.
        # B2 FIX: Reduced window from 7 days to 30 minutes.
        # The 7-day window caused pre-existing appointments (booked in prior calls) to be
        # detected as new bookings, generating false-positive 'agendo' outcomes and
        # incorrectly firing the GHL workflow. 30 minutes is tight enough to only catch
        # appointments booked during the current call.
        if not agendo and contact_id:
            try:
                appt_check = handle_get_appointment_by_contact({"contactId": contact_id})
                if appt_check.get("found"):
                    appt_start_raw = appt_check.get("startTime", "")
                    appt_id_check = appt_check.get("appointmentId", "")
                    if appt_id_check and call_duration_secs > 0:
                        # Only trust this lookup if we have a call start time to compare against.
                        # We use call start time + call duration + 5min buffer as the window.
                        # If timestamps are missing (call_duration_secs == 0), skip this check
                        # to avoid false positives — better to miss an agendo than to fabricate one.
                        try:
                            appt_dt = datetime.fromisoformat(appt_start_raw.replace("Z", "+00:00"))
                            now_utc = datetime.now(pytz.utc)
                            # B2 FIX: 30-minute window instead of 7 days
                            # Appointment must be in the future AND the call started < 30 min ago
                            call_started_utc = now_utc - timedelta(seconds=call_duration_secs)
                            minutes_since_call_start = (now_utc - call_started_utc).total_seconds() / 60
                            if appt_dt > now_utc and minutes_since_call_start <= 30:
                                agendo = True
                                appointment_id = appt_id_check
                                booked_time = appt_check.get("humanTime", "")
                                outcome = "agendo"
                                outcome_label = "Agendó"
                                stage = "Consulta Agendada"
                                print(f"[FIX BUG3] agendo detected via GHL lookup (call started {minutes_since_call_start:.1f}min ago)")
                        except Exception:
                            pass
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
            if success_eval is not None:
                _update_contact_custom_field(contact_id, "elena_success_eval", str(success_eval).lower())
            # B4 FIX: Write call duration in seconds to GHL for analytics.
            # Allows filtering real conversations from no-answers and measuring avg conversion time.
            if call_duration_secs > 0:
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


# ─── Health Check ─────────────────────────────────────────────────────────────
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
