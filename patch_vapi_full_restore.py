"""
FULL RESTORE — Reconstruye el assistant de Vapi completamente:
- System prompt desde system_prompt.txt (con fecha de hoy inyectada)
- Todas las 9 tools con serverUrl correcto
- temperature: 0.45, maxTokens: 170
- Todos los parámetros de Fase 1 (serverUrl, fillerInjection, stopSpeakingPlan, transcriber)
"""
import requests
import json
from datetime import datetime, timedelta
import pytz

VAPI_KEY = "88453266-9413-4275-babf-8e2481a9a1d6"
ASSISTANT_ID = "1631c7cf-2914-45f9-bf82-6635cdf00aba"
SERVER_URL = "https://elena-pdem.onrender.com/api/vapi/server-url"

HEADERS = {
    "Authorization": f"Bearer {VAPI_KEY}",
    "Content-Type": "application/json"
}

TZ = pytz.timezone("America/New_York")
DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Build today/tomorrow strings
now = datetime.now(TZ)
tomorrow = now + timedelta(days=1)
today_str = f"{DAYS_ES[now.weekday()]} {now.day} de {MONTHS_ES[now.month-1]} de {now.year}"
tomorrow_str = f"{DAYS_ES[tomorrow.weekday()]} {tomorrow.day} de {MONTHS_ES[tomorrow.month-1]} de {tomorrow.year}"

# Load and inject date into system prompt
with open("system_prompt.txt", "r") as f:
    prompt_text = f.read()

prompt_text = prompt_text.replace("{{TODAY}}", today_str).replace("{{TOMORROW}}", tomorrow_str)
print(f"System prompt: {len(prompt_text)} chars, date injected: {today_str}")

# ─── All 9 tools ──────────────────────────────────────────────────────────────

tools = [
    {
        "type": "function",
        "server": {"url": SERVER_URL},
        "function": {
            "name": "check_availability",
            "description": (
                "Verifica los horarios disponibles en el calendario de Laser Place Miami para los próximos 30 días. "
                "Llama esta función SIEMPRE antes de ofrecer horarios al cliente. "
                "Los martes son los días prioritarios para procedimientos. "
                "Devuelve una lista de slots con 'time' (ISO exacto) y 'label' (texto legible)."
            ),
            "parameters": {
                "type": "object",
                "required": [],
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "server": {"url": SERVER_URL},
        "function": {
            "name": "get_contact",
            "description": (
                "Busca un contacto existente en GHL por número de teléfono. "
                "Úsalo para verificar si el cliente ya está en el sistema y obtener su nombre."
            ),
            "parameters": {
                "type": "object",
                "required": ["phone"],
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Número de teléfono del cliente en formato E.164, ej: +17865533777"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "server": {"url": SERVER_URL},
        "function": {
            "name": "create_contact",
            "description": (
                "Crea un nuevo contacto en GHL. Úsalo cuando el cliente no existe en el sistema. "
                "Requiere firstName y phone. Email es opcional."
            ),
            "parameters": {
                "type": "object",
                "required": ["firstName", "phone"],
                "properties": {
                    "firstName": {
                        "type": "string",
                        "description": "Nombre del cliente"
                    },
                    "lastName": {
                        "type": "string",
                        "description": "Apellido del cliente (opcional)"
                    },
                    "phone": {
                        "type": "string",
                        "description": "Número de teléfono en formato E.164, ej: +17865533777"
                    },
                    "email": {
                        "type": "string",
                        "description": "Email del cliente (opcional)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "server": {"url": SERVER_URL},
        "function": {
            "name": "create_booking",
            "description": (
                "Crea una cita en el calendario de Laser Place Miami. "
                "SIEMPRE usa el ISO timestamp EXACTO devuelto por check_availability — nunca construyas el timestamp manualmente. "
                "Requiere contactId y el slot time exacto."
            ),
            "parameters": {
                "type": "object",
                "required": ["contactId", "startTime"],
                "properties": {
                    "contactId": {
                        "type": "string",
                        "description": "ID del contacto en GHL"
                    },
                    "startTime": {
                        "type": "string",
                        "description": "ISO timestamp exacto del slot seleccionado (obtenido de check_availability)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "server": {"url": SERVER_URL},
        "function": {
            "name": "get_appointment_by_contact",
            "description": (
                "Busca citas próximas de un contacto en GHL. "
                "Úsalo para verificar si el cliente ya tiene una cita antes de crear una nueva."
            ),
            "parameters": {
                "type": "object",
                "required": ["contactId"],
                "properties": {
                    "contactId": {
                        "type": "string",
                        "description": "ID del contacto en GHL"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "server": {"url": SERVER_URL},
        "function": {
            "name": "reschedule_appointment",
            "description": (
                "Reagenda una cita existente a un nuevo horario. "
                "Requiere el appointmentId y el nuevo newStartTime exacto de check_availability."
            ),
            "parameters": {
                "type": "object",
                "required": ["appointmentId", "newStartTime"],
                "properties": {
                    "appointmentId": {
                        "type": "string",
                        "description": "ID de la cita a reagendar"
                    },
                    "newStartTime": {
                        "type": "string",
                        "description": "Nuevo ISO timestamp exacto del slot (obtenido de check_availability). DEBE ser el campo 'time' del slot exactamente como aparece."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "server": {"url": SERVER_URL},
        "function": {
            "name": "cancel_appointment",
            "description": (
                "Cancela una cita existente en GHL. "
                "Úsalo solo cuando el cliente confirme explícitamente que quiere cancelar."
            ),
            "parameters": {
                "type": "object",
                "required": ["appointmentId"],
                "properties": {
                    "appointmentId": {
                        "type": "string",
                        "description": "ID de la cita a cancelar"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "server": {"url": SERVER_URL},
        "function": {
            "name": "schedule_callback",
            "description": (
                "Programa una llamada de seguimiento cuando el cliente no puede hablar ahora. "
                "SIEMPRE llama get_current_time PRIMERO para saber la hora real de Miami. "
                "Luego elige hours según lo que dijo el cliente: "
                "2 = en 2 horas / ahorita / más tarde (vago); "
                "4 = en 4 horas / esta tarde / hora específica ≤5h desde ahora; "
                "12 = mañana / pasado mañana / hora específica >5h; "
                "120 = próxima semana / en unos días / más adelante."
            ),
            "parameters": {
                "type": "object",
                "required": ["hours"],
                "properties": {
                    "hours": {
                        "type": "integer",
                        "enum": ["2", "4", "12", "120"],
                        "description": "Horas hasta el callback. Solo acepta: 2, 4, 12, o 120."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "server": {"url": SERVER_URL},
        "function": {
            "name": "get_current_time",
            "description": (
                "Obtiene la hora y fecha actual en Miami (Eastern Time). "
                "Llama esta función INMEDIATAMENTE cuando el cliente dice que no puede hablar ahora, "
                "ANTES de preguntar por el callback. Sin parámetros."
            ),
            "parameters": {
                "type": "object",
                "required": [],
                "properties": {}
            }
        }
    }
]

# ─── Full PATCH body ───────────────────────────────────────────────────────────

patch_body = {
    "serverUrl": SERVER_URL,
    "firstMessage": (
        "Hola, habla Elena de Laser Place Miami en Coral Gables. "
        "Te llamo porque vimos tu interés en el Botox y quería hacerte unas preguntas rápidas "
        "para ver si calificas. ¿Tienes 2 minutos?"
    ),
    "model": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.4,
        "maxTokens": 300,
        "messages": [
            {
                "role": "system",
                "content": prompt_text
            }
        ],
        "tools": tools
    },
    "voice": {
        "provider": "11labs",
        "voiceId": "b2htR0pMe28pYwCY9gnP",
        "model": "eleven_flash_v2_5",
        "stability": 0.5,
        "similarityBoost": 0.75,
        "style": 0.0,
        "useSpeakerBoost": True,
        "speed": 0.95,
        "fillerInjectionEnabled": False
    },
    "backchannelingEnabled": False,
    "responseDelaySeconds": 0,
    "numWordsToInterruptAssistant": 2,
    "transcriber": {
        "provider": "deepgram",
        "model": "nova-3",
        "language": "multi"
    },
    "stopSpeakingPlan": {
        "numWords": 2,
        "voiceSeconds": 0.5,
        "backoffSeconds": 1.5
    },
    "backgroundDenoisingEnabled": True
}

print(f"\nApplying full PATCH with {len(tools)} tools...")
patch_resp = requests.patch(
    f"https://api.vapi.ai/assistant/{ASSISTANT_ID}",
    headers=HEADERS,
    json=patch_body,
    timeout=30
)

print(f"PATCH status: {patch_resp.status_code}")
if patch_resp.status_code == 200:
    result = patch_resp.json()
    result_model = result.get("model", {})
    result_tools = result_model.get("tools", [])
    result_messages = result_model.get("messages", [])
    result_system = next(
        (m.get("content", "") for m in result_messages if m.get("role") == "system"),
        ""
    )
    result_tool_names = [t.get('function', {}).get('name') for t in result_tools]

    print(f"\n✅ SUCCESS")
    print(f"   serverUrl:          {result.get('serverUrl')}")
    print(f"   firstMessage:       {result.get('firstMessage', '')[:60]}...")
    print(f"   temperature:        {result_model.get('temperature')}")
    print(f"   maxTokens:          {result_model.get('maxTokens')}")
    print(f"   tools count:        {len(result_tools)}")
    print(f"   system prompt len:  {len(result_system)} chars")
    result_voice = result.get('voice', {})
    print(f"   fillerInjection:    {result_voice.get('fillerInjectionEnabled')}")
    print(f"   stability:          {result_voice.get('stability')}")
    print(f"   similarityBoost:    {result_voice.get('similarityBoost')}")
    print(f"   style:              {result_voice.get('style')}")
    print(f"   speed:              {result_voice.get('speed')}")
    print(f"   backchanneling:     {result.get('backchannelingEnabled')}")
    print(f"   responseDelay:      {result.get('responseDelaySeconds')}s")
    print(f"   numWordsInterrupt:  {result.get('numWordsToInterruptAssistant')} words")
    print(f"   transcriber:        {result.get('transcriber', {}).get('model')} {result.get('transcriber', {}).get('language')}")
    print(f"   stopSpeakingPlan:   {result.get('stopSpeakingPlan')}")
    print(f"   backgroundDenoise:  {result.get('backgroundDenoisingEnabled')}")

    expected_tools = [
        "check_availability", "get_contact", "create_contact", "create_booking",
        "get_appointment_by_contact", "reschedule_appointment", "cancel_appointment",
        "schedule_callback", "get_current_time"
    ]
    print("\n   Tool verification:")
    all_ok = True
    for t in expected_tools:
        status = "✅" if t in result_tool_names else "❌ MISSING"
        if t not in result_tool_names:
            all_ok = False
        print(f"     {status} {t}")

    if all_ok:
        print("\n✅ Full restore complete. All systems operational.")
    else:
        print("\n❌ Some tools missing.")
else:
    print(f"❌ ERROR: {patch_resp.text[:500]}")
