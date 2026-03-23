"""
Elena AI - Vapi Tool Server for GoHighLevel
Standalone Flask server for deployment on Render/Railway/any cloud platform.

Handles tool calls from Vapi during live phone conversations:
- check_availability: Check calendar availability
- create_booking: Create a new appointment
- reschedule_appointment: Reschedule an existing appointment
- cancel_appointment: Cancel an existing appointment
- get_contact: Get contact by phone number
- create_contact: Create a new contact
- get_appointment_by_contact: Find upcoming appointments for a contact
"""

from flask import Flask, request, jsonify
import requests as http_requests
import json
import os
from datetime import datetime, timedelta

app = Flask(__name__)

# Configuration from environment variables
GHL_API_KEY = os.environ.get("GHL_API_KEY", "")
GHL_BASE_URL = "https://rest.gohighlevel.com/v1"
CALENDAR_ID = os.environ.get("GHL_CALENDAR_ID", "hYHvVwjKPykvcPkrsQWT")
LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "hzRj7DV9erP8tnPiTv7D")


def ghl_headers():
    return {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json"
    }


def ghl_get(endpoint, params=None):
    url = f"{GHL_BASE_URL}/{endpoint}"
    resp = http_requests.get(url, headers=ghl_headers(), params=params, timeout=10)
    return resp.json()


def ghl_post(endpoint, data):
    url = f"{GHL_BASE_URL}/{endpoint}"
    resp = http_requests.post(url, headers=ghl_headers(), json=data, timeout=10)
    return resp.json()


def ghl_put(endpoint, data):
    url = f"{GHL_BASE_URL}/{endpoint}"
    resp = http_requests.put(url, headers=ghl_headers(), json=data, timeout=10)
    return resp.json()


# ─── Tool Handlers ───────────────────────────────────────────────────────────

def handle_check_availability(args):
    """Check available appointment slots for the next 14 days."""
    now = datetime.utcnow()
    start_ms = int(now.timestamp() * 1000)
    end_ms = int((now + timedelta(days=14)).timestamp() * 1000)

    result = ghl_get("appointments/slots", {
        "calendarId": CALENDAR_ID,
        "startDate": start_ms,
        "endDate": end_ms,
        "timezone": "America/New_York"
    })

    slots = result.get("slots", result) if isinstance(result, dict) else result
    formatted_slots = []

    if isinstance(slots, dict):
        for date_key, day_slots in sorted(slots.items()):
            if isinstance(day_slots, list):
                for slot in day_slots[:4]:
                    formatted_slots.append({"date": date_key, "time": slot})
            elif isinstance(day_slots, dict) and "slots" in day_slots:
                for slot in day_slots["slots"][:4]:
                    formatted_slots.append({"date": date_key, "time": slot})
    elif isinstance(slots, list):
        formatted_slots = slots[:20]

    tuesday_slots = []
    other_slots = []
    for slot in formatted_slots:
        slot_str = json.dumps(slot).lower()
        if "tue" in slot_str or "martes" in slot_str:
            tuesday_slots.append(slot)
        else:
            other_slots.append(slot)

    return {
        "available": len(formatted_slots) > 0,
        "tuesday_slots": tuesday_slots[:5],
        "other_slots": other_slots[:10],
        "total_available": len(formatted_slots),
        "message": "Horarios disponibles encontrados. Prioriza ofrecer los martes."
        if formatted_slots else "No hay horarios disponibles en los próximos 14 días."
    }


def handle_get_contact(args):
    """Search for a contact by phone number."""
    phone = args.get("phone", "")
    if not phone:
        return {"found": False, "message": "Número de teléfono no proporcionado."}

    phone_clean = phone.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
    # Ensure +1 prefix for US numbers
    if not phone_clean.startswith("+"):
        phone_clean = phone_clean.lstrip("0")
        if len(phone_clean) == 10:
            phone_clean = "+1" + phone_clean
        elif len(phone_clean) == 11 and phone_clean.startswith("1"):
            phone_clean = "+" + phone_clean
        else:
            phone_clean = "+" + phone_clean

    result = ghl_get("contacts/lookup", {"phone": phone_clean})

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
    """Create a new contact in GHL."""
    data = {
        "locationId": LOCATION_ID,
        "firstName": args.get("firstName", ""),
        "lastName": args.get("lastName", ""),
        "phone": args.get("phone", ""),
        "source": "AI Elena - Llamada"
    }
    if args.get("email"):
        data["email"] = args["email"]

    result = ghl_post("contacts/", data)

    if "contact" in result:
        contact = result["contact"]
        return {
            "success": True,
            "contactId": contact.get("id"),
            "message": f"Contacto creado: {contact.get('firstName', '')} {contact.get('lastName', '')}"
        }
    return {"success": False, "message": f"No se pudo crear el contacto: {str(result)[:200]}"}


def handle_create_booking(args):
    """Create a new appointment in GHL calendar."""
    contact_id = args.get("contactId", "")
    start_time = args.get("startTime", "")
    title = args.get("title", "Consulta Botox - Laser Place Miami")

    if not contact_id or not start_time:
        return {"success": False, "message": "Se necesita contactId y startTime para agendar."}

    data = {
        "calendarId": CALENDAR_ID,
        "contactId": contact_id,
        "startTime": start_time,
        "title": title,
        "appointmentStatus": "confirmed",
        "locationId": LOCATION_ID
    }

    result = ghl_post("appointments/", data)

    if "id" in result or "appointment" in result:
        appt = result if "id" in result else result.get("appointment", {})
        return {
            "success": True,
            "appointmentId": appt.get("id", ""),
            "message": f"Cita agendada exitosamente para {start_time}. ID: {appt.get('id', '')}"
        }
    return {"success": False, "message": f"No se pudo agendar: {str(result)[:200]}"}


def handle_reschedule_appointment(args):
    """Reschedule an existing appointment to a new time."""
    appointment_id = args.get("appointmentId", "")
    new_start_time = args.get("newStartTime", "")

    if not appointment_id:
        return {"success": False, "message": "Se necesita el appointmentId para reagendar."}
    if not new_start_time:
        return {"success": False, "message": "Se necesita el nuevo horario (newStartTime) para reagendar."}

    result = ghl_put(f"appointments/{appointment_id}", {"startTime": new_start_time})

    if "id" in result or "appointment" in result:
        return {
            "success": True,
            "appointmentId": appointment_id,
            "newStartTime": new_start_time,
            "message": f"Cita reagendada exitosamente al nuevo horario: {new_start_time}"
        }
    return {"success": False, "message": f"No se pudo reagendar: {str(result)[:200]}"}


def handle_cancel_appointment(args):
    """Cancel an existing appointment."""
    appointment_id = args.get("appointmentId", "")
    if not appointment_id:
        return {"success": False, "message": "Se necesita el appointmentId para cancelar."}

    result = ghl_put(f"appointments/{appointment_id}", {"appointmentStatus": "cancelled"})

    if "id" in result or "appointment" in result:
        return {"success": True, "appointmentId": appointment_id, "message": "Cita cancelada exitosamente."}
    return {"success": False, "message": f"No se pudo cancelar: {str(result)[:200]}"}


def handle_get_appointment_by_contact(args):
    """Get upcoming appointments for a contact (needed before rescheduling)."""
    contact_id = args.get("contactId", "")
    if not contact_id:
        return {"found": False, "message": "Se necesita el contactId para buscar citas."}

    now = datetime.utcnow()
    start_ms = int(now.timestamp() * 1000)
    end_ms = int((now + timedelta(days=60)).timestamp() * 1000)

    result = ghl_get("appointments/", {
        "calendarId": CALENDAR_ID,
        "startDate": start_ms,
        "endDate": end_ms
    })

    appointments = result.get("appointments", [])
    contact_appts = [a for a in appointments if a.get("contactId") == contact_id]

    if contact_appts:
        appt = contact_appts[0]
        return {
            "found": True,
            "appointmentId": appt.get("id", ""),
            "startTime": appt.get("startTime", ""),
            "title": appt.get("title", ""),
            "status": appt.get("appointmentStatus", ""),
            "message": f"Cita encontrada: {appt.get('title', '')} - {appt.get('startTime', '')}"
        }
    return {"found": False, "message": "No se encontraron citas próximas para este contacto."}


# ─── Tool Registry ───────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    "check_availability": handle_check_availability,
    "get_contact": handle_get_contact,
    "create_contact": handle_create_contact,
    "create_booking": handle_create_booking,
    "reschedule_appointment": handle_reschedule_appointment,
    "cancel_appointment": handle_cancel_appointment,
    "get_appointment_by_contact": handle_get_appointment_by_contact,
}


# ─── Vapi Server URL Endpoint ───────────────────────────────────────────────

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
            "calendar_id": CALENDAR_ID
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
    return jsonify({"status": "healthy", "service": "Elena AI Tool Server"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
