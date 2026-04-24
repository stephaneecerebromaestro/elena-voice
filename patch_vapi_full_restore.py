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

with open("/root/.secrets/vapi_key") as _kf:
    VAPI_KEY = _kf.read().strip()
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
                "4 = en 4 horas / esta tarde / hora específica 4-8h desde ahora; "
                "12 = mañana / pasado mañana / hora específica >8h desde ahora; "
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
    "backgroundDenoisingEnabled": True,
    "silenceTimeoutSeconds": 30,
    "hooks": [
        {
            "name": "silence-check-1",
            "on": "customer.speech.timeout",
            "do": [
                {
                    "type": "say",
                    "exact": "¿Sigues ahí?"
                }
            ],
            "options": {
                "timeoutSeconds": 5,
                "triggerMaxCount": 1,
                "triggerResetMode": "onUserSpeech"
            }
        },
        {
            "name": "silence-check-2",
            "on": "customer.speech.timeout",
            "do": [
                {
                    "type": "say",
                    "exact": "¿Me escuchas?"
                }
            ],
            "options": {
                "timeoutSeconds": 12,
                "triggerMaxCount": 1,
                "triggerResetMode": "onUserSpeech"
            }
        },
        {
            "name": "silence-check-3",
            "on": "customer.speech.timeout",
            "do": [
                {
                    "type": "say",
                    "exact": "Hola, ¿estás ahí? Parece que perdimos la conexión. ¡Que tengas un excelente día!"
                }
            ],
            "options": {
                "timeoutSeconds": 20,
                "triggerMaxCount": 1,
                "triggerResetMode": "onUserSpeech"
            }
        },
        {
            "name": "silence-end-call",
            "on": "customer.speech.timeout",
            "do": [
                {
                    "type": "tool",
                    "tool": {
                        "type": "endCall"
                    }
                }
            ],
            "options": {
                "timeoutSeconds": 30,
                "triggerMaxCount": 1,
                "triggerResetMode": "never"
            }
        }
    ],
    "voicemailDetection": {
        "provider": "vapi",
        "type": "audio",
        "beepMaxAwaitSeconds": 25,
        "backoffPlan": {
            "startAtSeconds": 2,
            "frequencySeconds": 2.5,
            "maxRetries": 6
        }
    },
    "voicemailMessage": "",
    "analysisPlan": {
        "summaryPlan": {
            "enabled": True,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Eres un analista de calidad para Elena, agente de IA de Laser Place Miami. "
                        "Resume la llamada en 2-3 oraciones en español. Incluye: "
                        "1) Si hubo conversación real con un humano, "
                        "2) El nivel de interés del cliente, "
                        "3) La objeción principal si hubo alguna, "
                        "4) El resultado final (cita agendada, callback, no interesado, etc.)."
                    )
                },
                {
                    "role": "user",
                    "content": "Aquí está la transcripción de la llamada: {{transcript}}"
                }
            ]
        },
        "structuredDataPlan": {
            "enabled": True,
            "schema": {
                "type": "object",
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": ["agendo", "no_contesto", "llamar_luego", "no_agendo", "error_tecnico"],
                        "description": "Resultado final de la llamada. agendo=cita creada, no_contesto=no contestó/buzón, llamar_luego=pidió callback, no_agendo=habló pero no agendó, error_tecnico=error del sistema"
                    },
                    "conversation_happened": {
                        "type": "boolean",
                        "description": "True si hubo conversación real con un humano (no buzón de voz)"
                    },
                    "language": {
                        "type": "string",
                        "enum": ["spanish", "english", "mixed"],
                        "description": "Idioma principal usado por el cliente durante la llamada"
                    },
                    "client_name": {
                        "type": "string",
                        "description": "Nombre del cliente si se mencionó durante la llamada, o vacío si no"
                    },
                    "interest_level": {
                        "type": "string",
                        "enum": ["alto", "medio", "bajo", "ninguno", "desconocido"],
                        "description": "Nivel de interés del cliente en el tratamiento o la cita"
                    },
                    "main_objection": {
                        "type": "string",
                        "description": "Objeción principal del cliente (precio, tiempo, miedo, ya tiene proveedor, etc.) o vacío si no hubo"
                    },
                    "callback_requested": {
                        "type": "boolean",
                        "description": "True si el cliente pidió que lo llamen en otro momento"
                    },
                    "pivot_needed": {
                        "type": "boolean",
                        "description": "True si el cliente rechazó Botox pero mostró interés en otro tratamiento"
                    },
                    "booked_slot": {
                        "type": "string",
                        "description": "Fecha y hora de la cita agendada en formato legible, o vacío si no se agendó"
                    },
                    "notes": {
                        "type": "string",
                        "description": "Notas adicionales relevantes sobre la llamada (máx 150 caracteres)"
                    }
                },
                "required": ["outcome", "conversation_happened", "language", "interest_level", "callback_requested", "pivot_needed"]
            },
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Analiza la transcripción de esta llamada de Elena (agente IA de Laser Place Miami) "
                        "y extrae los datos estructurados. "
                        "REGLAS CRÍTICAS para 'outcome': "
                        "- 'agendo' SOLO si se confirmó una cita con fecha y hora específica. "
                        "- 'no_contesto' si fue buzón de voz, no contestó, o la llamada duró menos de 20 segundos. "
                        "- 'llamar_luego' si el cliente pidió explícitamente que lo llamen en otro momento. "
                        "- 'no_agendo' si hubo conversación real pero no se agendó cita. "
                        "- 'error_tecnico' solo si hubo un error técnico del sistema. "
                        "Para 'conversation_happened': True solo si un humano real respondió y habló (no buzón). "
                        "Para 'interest_level': evalúa el tono, preguntas y respuestas del cliente."
                    )
                },
                {
                    "role": "user",
                    "content": "Transcripción: {{transcript}}"
                }
            ]
        },
        "successEvaluationPlan": {
            "enabled": True,
            "rubric": "PassFail",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Evalúa si Elena (agente IA de Laser Place Miami) tuvo éxito en esta llamada. "
                        "CRITERIO DE ÉXITO (Pass): Se agendó una cita para el Skin Reveal Analysis. "
                        "CRITERIO DE FALLO (Fail): No se agendó cita, independientemente del motivo. "
                        "Responde ÚNICAMENTE con 'true' (éxito) o 'false' (fallo). Sin explicaciones."
                    )
                },
                {
                    "role": "user",
                    "content": "Transcripción: {{transcript}}"
                }
            ]
        }
    }
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
    print(f"   silenceTimeout:     {result.get('silenceTimeoutSeconds')}s")
    result_hooks = result.get('hooks', [])
    print(f"   hooks count:        {len(result_hooks)} ({[h.get('name') for h in result_hooks]})")

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

    # Verify analysisPlan
    result_analysis = result.get("analysisPlan", {})
    summary_ok = result_analysis.get("summaryPlan", {}).get("enabled", False)
    structured_ok = result_analysis.get("structuredDataPlan", {}).get("enabled", False)
    success_ok = result_analysis.get("successEvaluationPlan", {}).get("enabled", False)
    print(f"\n   analysisPlan verification:")
    print(f"     {'✅' if summary_ok else '❌'} summaryPlan enabled")
    print(f"     {'✅' if structured_ok else '❌'} structuredDataPlan enabled")
    print(f"     {'✅' if success_ok else '❌'} successEvaluationPlan enabled")
    analysis_ok = summary_ok and structured_ok and success_ok

    if all_ok and analysis_ok:
        print("\n✅ Full restore complete. All systems operational.")
    elif not all_ok:
        print("\n❌ Some tools missing.")
    else:
        print("\n❌ analysisPlan not fully configured.")
else:
    print(f"❌ ERROR: {patch_resp.text[:500]}")
