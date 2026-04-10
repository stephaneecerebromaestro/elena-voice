# CLAUDE.md — Elena Voice · Agente Maestro de Voz
# Laser Place Miami · Creado: 2026-04-11
# ════════════════════════════════════════════════════════════

## IDENTIDAD

- **Nombre:** Elena Voice
- **Rol:** Agente maestro de llamadas telefónicas — supervisa y gestiona todos los agentes de voz
- **Negocio:** Laser Place Miami — clínica de estética médica premium
- **Reporta a:** Stephanee (Agente Maestro)
- **Canal:** Llamadas telefónicas via Vapi + Twilio

---

## ARQUITECTURA

```
STEPHANEE (Agente Maestro)
    └── ELENA VOICE (este agente)
            ├── elena-voice-botox (Botox) ← ACTIVO EN PRODUCCIÓN
            ├── elena-voice-lhr (Laser Hair Removal) ← PENDIENTE
            ├── elena-voice-fillers ← PENDIENTE
            ├── elena-voice-bioestimuladores ← PENDIENTE
            ├── elena-voice-acne ← PENDIENTE
            ├── elena-voice-matrixpro ← PENDIENTE
            └── elena-voice-cicatrices ← PENDIENTE
```

Elena Voice es el servidor de tools para Vapi. Cada tratamiento es un assistant diferente en Vapi, todos apuntan al mismo server URL. El server identifica qué assistant llamó vía `call.assistantId`.

---

## STACK TÉCNICO

| Componente | Tecnología |
|-----------|-----------|
| Runtime | Python 3.11 + Flask + Gunicorn (1 worker, 8 threads) |
| AI Model | GPT-4o-mini via Vapi (para las llamadas) |
| AI Auditoría | Claude (Anthropic) via ARIA |
| Voz | ElevenLabs — "Sofía" (colombiana, conversational) |
| Telefonía | Twilio |
| CRM | GoHighLevel (GHL) API v2 |
| Database | Supabase (proyecto ARIA) |
| Notificaciones | Telegram (@aria_elena_bot) |
| Deploy | Render (auto-deploy on git push) |
| Repo | github.com/stephaneecerebromaestro/elena-voice |

---

## HERRAMIENTAS DISPONIBLES (9)

| Tool | Función |
|------|---------|
| check_availability | Ver slots disponibles en calendario GHL |
| get_contact | Buscar contacto por teléfono |
| create_contact | Crear contacto nuevo |
| create_booking | Crear cita |
| reschedule_appointment | Reagendar cita existente |
| cancel_appointment | Cancelar cita |
| get_appointment_by_contact | Buscar citas de un contacto |
| get_current_time | Hora actual de Miami (para calcular callbacks) |
| schedule_callback | Programar llamada de seguimiento |

---

## ASSISTANTS VAPI REGISTRADOS

| Assistant | Tratamiento | Vapi ID | Calendar GHL | Pipeline GHL | Estado |
|-----------|------------|---------|-------------|-------------|--------|
| Elena - Laser Place Miami | Botox | 1631c7cf-2914-45f9-bf82-6635cdf00aba | hYHvVwjKPykvcPkrsQWT | jiLGCWy0CEsa0iAmmMWT | ACTIVO |

---

## CONFIGURACIÓN VAPI DEL ASSISTANT BOTOX

| Campo | Valor |
|-------|-------|
| Model | GPT-4o-mini (OpenAI) |
| Voice | Sofía — b2htR0pMe28pYwCY9gnP (ElevenLabs, colombiana) |
| Transcriber | Deepgram |
| Server URL | https://elena-pdem.onrender.com/api/vapi/server-url |
| First Message | "Hola, habla Elena de Laser Place Miami. Vi tu interés en el Botox..." |

---

## FLUJO DE UNA LLAMADA

```
1. GHL workflow dispara llamada outbound via Vapi
2. Vapi crea la llamada con Twilio
3. Paciente contesta (o buzón de voz)
4. Vapi transcribe con Deepgram → GPT-4o-mini procesa
5. Cuando GPT necesita una tool → POST a /api/vapi/server-url
6. Elena Voice ejecuta la tool contra GHL API
7. Resultado vuelve a Vapi → GPT genera respuesta
8. ElevenLabs convierte texto a voz → paciente escucha
9. Al colgar → end-of-call-report:
   a. Clasifica outcome (agendó, no_contestó, no_agendó, llamar_luego, error_técnico)
   b. Escribe tags en GHL
   c. ARIA audita el transcript
   d. Notifica resultado a Telegram
```

---

## ENDPOINTS

| Ruta | Método | Función |
|------|--------|---------|
| /api/vapi/server-url | POST | Recibe tool calls de Vapi |
| /api/vapi/server-url | GET | Health check del server |
| /health | GET | Health check general |
| /update-date | POST/GET | Actualiza fecha en prompt del assistant |
| /aria/telegram/webhook | POST | Comandos ARIA via Telegram |
| /aria/vapi/end-of-call | POST | Procesa fin de llamada |
| /aria/report/daily | POST | Trigger reporte diario |
| /aria/report/weekly | POST | Trigger reporte semanal |

---

## ARIA — Sistema de Auditoría

ARIA audita CADA llamada automáticamente:
- Clasifica outcome y tags de GHL
- Detecta objeciones, errores de script
- Calcula playbook score (1-10)
- Envía resumen a Telegram
- Genera reportes diarios y semanales
- Comandos via Telegram: /intel, /tendencia, /errores, /leads, /backfill

---

## POST-CALL OUTCOMES (tags GHL)

| Tag | Significado |
|-----|------------|
| agendo | Cita confirmada |
| no_contesto | Buzón de voz / no contestó |
| no_agendo | Contestó pero no agendó |
| llamar_luego | Pidió que lo llamen después |
| error_tecnico | Fallo técnico durante la llamada |

---

## ESTADO ACTUAL — PENDIENTE DE REFACTOR

El código actual (app.py, 2355 líneas) está hardcodeado para Botox:
- Un solo CALENDAR_ID
- Un solo VAPI_ASSISTANT_ID
- Un solo BOOKING_TITLE

Para multi-tratamiento necesita:
1. config.py con dict de assistants → {calendar, pipeline, title}
2. vapi_server_url() extrae assistantId del payload de Vapi
3. Cada handler recibe bot_config en vez de globals

**Este refactor se hace con Juan en PC (Fase D).**

---

## CREDENCIALES

| Servicio | En Render |
|----------|----------|
| GHL | GHL_PIT + GHL_LOCATION_ID + GHL_CALENDAR_ID |
| Vapi | VAPI_API_KEY + VAPI_ASSISTANT_ID |
| Anthropic (ARIA) | ANTHROPIC_API_KEY |
| Supabase | SUPABASE_URL + SUPABASE_SERVICE_KEY |
| Telegram | TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID |

Render service: elena (srv-d70lsh9aae7s739d8lo0)

---

_Elena Voice — Agente Maestro de Voz · Laser Place Miami_
_Última actualización: 2026-04-11_
