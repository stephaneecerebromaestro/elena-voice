"""
Elena AI - Vapi Tool Server for GoHighLevel
Standalone Flask server for deployment on Render.

Uses GHL V2 API (services.leadconnectorhq.com) with Private Integration Token (PIT).

Handles tool calls from Vapi during live phone conversations:
- check_availability: Check calendar availability (next 14 days)
- get_contact: Search contact by phone number
- create_contact: Create a new contact
- create_booking: Create a new appointment
- reschedule_appointment: Reschedule an existing appointment
- cancel_appointment: Cancel an existing appointment
- get_appointment_by_contact: Find upcoming appointments for a contact
"""

from flask import Flask, request, jsonify
import requests as http_requests
import json
import os
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────
GHL_PIT = os.environ.get("GHL_PIT", "")           # Private Integration Token
GHL_API_KEY = os.environ.get("GHL_API_KEY", "")   # Legacy JWT key (fallback)
CALENDAR_ID = os.environ.get("GHL_CALENDAR_ID", "hYHvVwjKPykvcPkrsQWT")
LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "hzRj7DV9erP8tnPiTv7D")

GHL_V2_BASE = "https://services.leadconnectorhq.com"
GHL_V1_BASE = "https://rest.gohighlevel.com/v1"
TZ = pytz.timezone("America/New_York")


def v2_headers():
    """Headers for GHL V2 API using PIT."""
    token = GHL_PIT or GHL_API_KEY
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Version": "2021-04-15"
    }


def v1_headers():
    """Headers for GHL V1 API using legacy JWT."""
    return {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json"
    }


def ghl_v2_get(path, params=None):
    url = f"{GHL_V2_BASE}{path}"
    resp = http_requests.get(url, headers=v2_headers(), params=params, timeout=12)
    return resp.json()


def ghl_v2_post(path, data):
    url = f"{GHL_V2_BASE}{path}"
    resp = http_requests.post(url, headers=v2_headers(), json=data, timeout=12)
    return resp.json()


def ghl_v2_put(path, data):
    url = f"{GHL_V2_BASE}{path}"
    resp = http_requests.put(url, headers=v2_headers(), json=data, timeout=12)
    return resp.json()


def ghl_v1_get(endpoint, params=None):
    url = f"{GHL_V1_BASE}/{endpoint}"
    resp = http_requests.get(url, headers=v1_headers(), params=params, timeout=12)
    return resp.json()


def ghl_v1_post(endpoint, data):
    url = f"{GHL_V1_BASE}/{endpoint}"
    resp = http_requests.post(url, headers=v1_headers(), json=data, timeout=12)
    return resp.json()


def ghl_v1_put(endpoint, data):
    url = f"{GHL_V1_BASE}/{endpoint}"
    resp = http_requests.put(url, headers=v1_headers(), json=data, timeout=12)
    return resp.json()


# ─── Tool Handlers ────────────────────────────────────────────────────────────

def handle_check_availability(args):
    """Check available appointment slots for the next 14 days using V2 API."""
    now = datetime.now(TZ)
    start_ms = int(now.timestamp() * 1000)
    end_ms = int((now + timedelta(days=14)).timestamp() * 1000)

    result = ghl_v2_get(
        f"/calendars/{CALENDAR_ID}/free-slots",
        params={
            "startDate": start_ms,
            "endDate": end_ms,
            "timezone": "America/New_York"
        }
    )

    # Parse the response - V2 returns {date: {slots: [...]}}
    formatted_slots = []
    tuesday_slots = []
    other_slots = []

    if isinstance(result, dict):
        for date_key, day_data in sorted(result.items()):
            if isinstance(day_data, dict) and "slots" in day_data:
                slots_list = day_data["slots"]
            elif isinstance(day_data, list):
                slots_list = day_data
            else:
                continue

            for slot in slots_list[:5]:  # max 5 per day
                slot_entry = {"date": date_key, "time": slot}
                formatted_slots.append(slot_entry)

                # Check if it's Tuesday
                try:
                    dt = datetime.fromisoformat(slot.replace("Z", "+00:00"))
                    if dt.weekday() == 1:  # Tuesday = 1
                        tuesday_slots.append(slot_entry)
                    else:
                        other_slots.append(slot_entry)
                except Exception:
                    other_slots.append(slot_entry)

    if formatted_slots:
        return {
            "available": True,
            "tuesday_slots": tuesday_slots[:5],
            "other_slots": other_slots[:10],
            "total_available": len(formatted_slots),
            "message": "Horarios disponibles encontrados. Prioriza ofrecer los martes."
        }
    else:
        return {
            "available": False,
            "tuesday_slots": [],
            "other_slots": [],
            "total_available": 0,
            "message": "No hay horarios disponibles en los próximos 14 días.",
            "raw_response": str(result)[:200]
        }


def handle_get_contact(args):
    """Search for a contact by phone number using V1 API."""
    phone = args.get("phone", "")
    if not phone:
        return {"found": False, "message": "Número de teléfono no proporcionado."}

    phone_clean = phone.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
    if not phone_clean.startswith("+"):
        phone_clean = phone_clean.lstrip("0")
        if len(phone_clean) == 10:
            phone_clean = "+1" + phone_clean
        elif len(phone_clean) == 11 and phone_clean.startswith("1"):
            phone_clean = "+" + phone_clean
        else:
            phone_clean = "+" + phone_clean

    result = ghl_v1_get("contacts/lookup", {"phone": phone_clean})

    if "contacts" in result and len(result["contacts"]) > 0:
        contact = result["contacts"][0]
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
    """Create a new contact in GHL using V1 API."""
    data = {
        "locationId": LOCATION_ID,
        "firstName": args.get("firstName", ""),
        "lastName": args.get("lastName", ""),
        "phone": args.get("phone", ""),
        "source": "AI Elena - Llamada"
    }
    if args.get("email"):
        data["email"] = args["email"]

    result = ghl_v1_post("contacts/", data)

    if "contact" in result:
        contact = result["contact"]
        return {
            "success": True,
            "contactId": contact.get("id"),
            "message": f"Contacto creado: {contact.get('firstName', '')} {contact.get('lastName', '')}"
        }
    return {"success": False, "message": f"No se pudo crear el contacto: {str(result)[:200]}"}


def handle_create_booking(args):
    """Create a new appointment in GHL calendar using V2 API with PIT."""
    contact_id = args.get("contactId", "")
    start_time = args.get("startTime", "")
    title = args.get("title", "Evaluación Botox - Laser Place Miami")

    if not contact_id or not start_time:
        return {"success": False, "message": "Se necesita contactId y startTime para agendar."}

    # Calculate end time (30 min after start)
    try:
        dt_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        dt_end = dt_start + timedelta(minutes=30)
        end_time = dt_end.isoformat()
    except Exception:
        end_time = start_time  # fallback

    data = {
        "calendarId": CALENDAR_ID,
        "locationId": LOCATION_ID,
        "contactId": contact_id,
        "startTime": start_time,
        "endTime": end_time,
        "title": title,
        "appointmentStatus": "confirmed",
        "selectedTimezone": "America/New_York",
        "selectedSlot": start_time
    }

    result = ghl_v2_post("/calendars/events/appointments", data)

    if "id" in result or "appointment" in result:
        appt = result if "id" in result else result.get("appointment", {})
        return {
            "success": True,
            "appointmentId": appt.get("id", ""),
            "message": f"Cita agendada exitosamente para {start_time}. ID: {appt.get('id', '')}"
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

    # Calculate end time (30 min after start)
    try:
        dt_start = datetime.fromisoformat(new_start_time.replace("Z", "+00:00"))
        dt_end = dt_start + timedelta(minutes=30)
        end_time = dt_end.isoformat()
    except Exception:
        end_time = new_start_time  # fallback

    data = {
        "startTime": new_start_time,
        "endTime": end_time,
        "calendarId": CALENDAR_ID,
        "selectedTimezone": "America/New_York",
        "selectedSlot": new_start_time
    }

    result = ghl_v2_put(f"/calendars/events/appointments/{appointment_id}", data)

    if "id" in result:
        return {
            "success": True,
            "appointmentId": appointment_id,
            "newStartTime": new_start_time,
            "message": f"Cita reagendada exitosamente al nuevo horario: {new_start_time}"
        }
    return {"success": False, "message": f"No se pudo reagendar: {str(result)[:300]}"}


def handle_cancel_appointment(args):
    """Cancel an existing appointment using V2 API with PIT."""
    appointment_id = args.get("appointmentId", "")
    if not appointment_id:
        return {"success": False, "message": "Se necesita el appointmentId para cancelar."}

    result = ghl_v2_put(f"/calendars/events/appointments/{appointment_id}", {"appointmentStatus": "cancelled"})

    if "id" in result:
        return {"success": True, "appointmentId": appointment_id, "message": "Cita cancelada exitosamente."}
    return {"success": False, "message": f"No se pudo cancelar: {str(result)[:200]}"}


def handle_get_appointment_by_contact(args):
    """Get upcoming appointments for a contact using V2 API with PIT."""
    contact_id = args.get("contactId", "")
    if not contact_id:
        return {"found": False, "message": "Se necesita el contactId para buscar citas."}

    result = ghl_v2_get(f"/contacts/{contact_id}/appointments")

    # GHL V2 returns 'events' key (not 'appointments')
    appointments = result.get("events", result.get("appointments", []))
    # Filter to only this calendar and future appointments
    now = datetime.now(TZ)
    upcoming = []
    for a in appointments:
        if a.get("calendarId") != CALENDAR_ID:
            continue
        if a.get("appointmentStatus") == "cancelled":
            continue
        start_str = a.get("startTime", "")
        try:
            # GHL returns UTC strings like "2026-03-24 16:00:00"
            if " " in start_str:
                start_str_iso = start_str.replace(" ", "T") + "+00:00"
            else:
                start_str_iso = start_str
            dt = datetime.fromisoformat(start_str_iso)
            if dt.tzinfo is None:
                dt = pytz.utc.localize(dt)
            if dt > now:
                upcoming.append((dt, a))
        except Exception:
            pass

    upcoming.sort(key=lambda x: x[0])

    if upcoming:
        _, appt = upcoming[0]
        start_raw = appt.get("startTime", "")
        if " " in start_raw:
            start_raw = start_raw.replace(" ", "T") + "+00:00"
        return {
            "found": True,
            "appointmentId": appt.get("id", ""),
            "startTime": start_raw,
            "title": appt.get("title", ""),
            "status": appt.get("appointmentStatus", ""),
            "message": f"Cita encontrada: {appt.get('title', '')} - {appt.get('startTime', '')}"
        }
    return {"found": False, "message": "No se encontraron citas próximas para este contacto en el calendario de Botox."}


# ─── Tool Registry ────────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    "check_availability": handle_check_availability,
    "get_contact": handle_get_contact,
    "create_contact": handle_create_contact,
    "create_booking": handle_create_booking,
    "reschedule_appointment": handle_reschedule_appointment,
    "cancel_appointment": handle_cancel_appointment,
    "get_appointment_by_contact": handle_get_appointment_by_contact,
}


# ─── Vapi Server URL Endpoint ─────────────────────────────────────────────────

@app.route("/api/vapi/server-url", methods=["POST", "OPTIONS", "GET"])
def vapi_server_url():
    """Main entry point for Vapi tool calls."""
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
            "service": "Elena AI - Vapi Tool Server",
            "tools": list(TOOL_HANDLERS.keys()),
            "calendar_id": CALENDAR_ID,
            "using_pit": bool(GHL_PIT)
        }), 200, cors

    try:
        body = request.get_json(silent=True) or {}
        message = body.get("message", body)
        message_type = message.get("type", "")

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

            handler = TOOL_HANDLERS.get(fn_name)
            if handler:
                try:
                    result = handler(arguments)
                except Exception as e:
                    result = {"error": f"Error ejecutando {fn_name}: {str(e)}"}
            else:
                result = {"error": f"Función no reconocida: {fn_name}"}

            return jsonify({"result": json.dumps(result, ensure_ascii=False)}), 200, cors

        elif message_type in ["status-update", "end-of-call-report", "hang", "speech-update", "transcript"]:
            return jsonify({"status": "ok"}), 200, cors

        else:
            return jsonify({"status": "ok"}), 200, cors

    except Exception as e:
        return jsonify({"error": str(e)}), 500, cors


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "service": "Elena AI Tool Server",
        "using_pit": bool(GHL_PIT),
        "calendar_id": CALENDAR_ID
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
