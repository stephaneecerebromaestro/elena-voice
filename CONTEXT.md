# Elena AI — Contexto del Sistema (Documento Vivo)

> **Última actualización:** v17.42.1 — 28 marzo 2026  
> **Propósito:** Este archivo es la fuente de verdad del sistema. Leerlo al inicio de cada sesión para retomar sin perder contexto.

---

## Arquitectura general

El sistema tiene tres capas que trabajan juntas:

**Capa 1 — Vapi (voz + LLM):** Ejecuta las llamadas telefónicas. Usa el `system_prompt.txt` para guiar el comportamiento conversacional de Elena. Llama herramientas (tools) via webhooks al servidor de Render cuando necesita datos externos (calendario, contacto, cita).

**Capa 2 — Render (backend Flask):** Servidor Python en `app.py`. Recibe los tool calls de Vapi, consulta la API de GHL, y al final de cada llamada clasifica el outcome y escribe los campos en el contacto de GHL.

**Capa 3 — GHL (CRM + workflows):** Almacena los contactos, el calendario de citas, y ejecuta los workflows de automatización según el outcome de cada llamada.

---

## Infraestructura

| Componente | Detalle |
|-----------|---------|
| Servidor backend | Render — `https://elena-pdem.onrender.com` — Service ID: `srv-d70lsh9aae7s739d8lo0` |
| Repositorio | GitHub — `vitusmediard/elena-vapi-server` (privado) |
| Asistente Vapi | ID: `1631c7cf-2914-45f9-bf82-6635cdf00aba` |
| Calendario GHL | ID: `hYHvVwjKPykvcPkrsQWT` |
| Location GHL | ID: `hzRj7DV9erP8tnPiTv7D` |
| Zona horaria | `America/New_York` (EST/EDT) |
| Booking title | `Evaluación Botox - Laser Place Miami` |

---

## Variables de entorno en Render

| Variable | Descripción |
|----------|-------------|
| `GHL_PIT` | Private Integration Token de GHL (API v2) |
| `GHL_CALENDAR_ID` | ID del calendario de citas |
| `GHL_LOCATION_ID` | ID de la location en GHL |
| `VAPI_API_KEY` | API key de Vapi |
| `VAPI_ASSISTANT_ID` | ID del asistente de Vapi |
| `BOOKING_TITLE` | Título de la cita (configurable) |

---

## Herramientas (Tools) que usa Elena

| Tool | Cuándo se usa | Qué hace |
|------|--------------|----------|
| `get_contact` | Al inicio de cada llamada | Busca el contacto por teléfono en GHL |
| `create_contact` | Si no existe el contacto | Crea el contacto en GHL |
| `check_availability` | Cuando el cliente quiere agendar | Devuelve slots disponibles del calendario (2 por día único, máx 14 slots) |
| `create_booking` | Cuando el cliente confirma un slot específico | Crea la cita en GHL |
| `reschedule_appointment` | Cuando el cliente quiere cambiar una cita existente | Reagenda la cita en GHL |
| `cancel_appointment` | Cuando el cliente quiere cancelar | Cancela la cita en GHL |
| `get_appointment_by_contact` | Para verificar citas existentes | Busca citas del contacto |
| `schedule_callback` | Cuando el cliente pide que lo llamen después | Programa un callback en GHL |
| `endCall` | Al final de la conversación | Termina la llamada |

---

## Lógica de Outcomes (Outcome Classifier)

El outcome se escribe en `elena_last_outcome` en el contacto de GHL. También activa el trigger `elena_resultado_botox` que dispara el Workflow 1.

### Árbol de decisión (en orden de prioridad):

```
1. agendo
   → create_booking fue llamado exitosamente (appointmentId presente en tool result)
   → reschedule_appointment fue llamado exitosamente (newStartTime presente en tool result)

2. llamar_luego
   → schedule_callback fue llamado exitosamente

3. no_contesto
   → short_call: duración < 20s (sin importar nada más)
   → shallow_call: duración 20-45s + customer-ended-call + user_spoke con ≤8 palabras totales + 0 tool calls
   → voicemail_by_customer: user_spoke=False + ended_reason=customer-ended-call
   → voicemail_by_elena: user_spoke=False + ended_reason=assistant-ended-call + (not duration_reliable OR call_duration < 45s)
   → FIX F2: Si todos los mensajes del usuario son frases de buzón de voz (inglés/español) → user_spoke=False

4. no_interesado
   → El LLM detectó rechazo explícito y lo marcó en el summary

5. no_agendo (default)
   → Todo lo demás: conversación real donde no se agendó cita
```

### Campos escritos a GHL después de cada llamada:

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `elena_last_outcome` | String | Outcome de la llamada (agendo/no_agendo/no_contesto/llamar_luego/no_interesado/error_tecnico) |
| `elena_call_duration` | Number | Duración en segundos (3 niveles de fallback para inbound/outbound) |
| `elena_ended_reason` | String | Razón de fin de llamada de Vapi (customer-ended-call, assistant-ended-call, etc.) |
| `elena_success_eval` | Boolean | True solo si se agendó cita (viene del analysisPlan de Vapi) |
| `elena_summary` | String | Resumen de la llamada generado por Vapi |
| `elena_appointment_id` | String | ID de la cita creada (solo si agendo) |
| `elena_appointment_date` | String | Fecha/hora de la cita (solo si agendo) |
| `elena_resultado_botox` | Trigger | Campo que activa el Workflow 1 de GHL |

### Fallback de `elena_call_duration` (3 niveles):

1. `call.startedAt` / `call.endedAt` del objeto call en el webhook
2. `message.startedAt` / `message.endedAt` del root del mensaje webhook
3. Timestamps Unix-ms del primer y último mensaje en `artifact.messages`

---

## Workflows de GHL

### Workflow 1: Vapi - Procesador de Llamadas

**Trigger:** Campo `elena_resultado_botox` cambia en el contacto.

**Ramas por outcome:**

| Outcome | Tags aplicados | Acciones |
|---------|---------------|---------|
| `agendo` | `elena_agendo` | Notificación WA interna + WA al cliente confirmando cita |
| `no_agendo` | `elena_no_agendo` | Notificación WA interna + re-entra al pipeline de seguimiento |
| `no_contesto` | `elena_no_contesto` | Notificación WA interna + reintento programado |
| `llamar_luego` | `elena_llamar_luego` | Notificación WA interna + callback programado |
| `no_interesado` | `elena_no_interesado` | Notificación WA interna + pipeline de no interesados |
| `error_tecnico` | `elena_error_tecnico` | Notificación WA interna |

**Campos usados en los mensajes WA del workflow:**
Todos deben usar el prefijo `contact.`:
- `{{contact.elena_last_outcome}}`
- `{{contact.elena_call_duration}}` ← corregido el 28/03/2026 (antes era `{{elena_call_duration}}` sin prefijo, causaba `[object Object]`)
- `{{contact.elena_ended_reason}}`
- `{{contact.elena_success_eval}}`
- `{{contact.elena_summary}}`
- `{{contact.elena_appointment_id}}`
- `{{contact.elena_appointment_date}}`

### Workflow 2: Principal - Entrada de Leads y Disparo de Llamadas

**Trigger:** Nuevo contacto / tag específico.

**Flujo:**
1. Lead entra (Facebook Lead Ad / manual / web form)
2. Asigna tag `botox_lead`
3. Espera 2 minutos
4. Verifica teléfono válido
5. Dispara llamada outbound via Vapi (webhook POST a Render)
6. Espera resultado via trigger `elena_resultado_botox`
7. Según resultado → rama del Workflow 1

**Lógica de reintentos:**
- `no_contesto`: reintenta hasta 3 veces con delay entre intentos
- `llamar_luego`: reintenta 1 vez en el horario indicado
- `no_agendo`: entra a secuencia de nurturing por WA

---

## Historial de versiones y fixes críticos

### v17.42.1 (28/03/2026)
- **Fix G:** Elena nunca se rinde buscando horario por disponibilidad. Solo cede ante rechazo explícito del servicio ("no quiero", "no me interesa", "no me llames más"). Elimina la REGLA DE TRES INTENTOS que hacía que Elena ofreciera callback después de 3 rechazos de horario.
- **Fix H:** Manejo de objeción de miedo al dolor — 3 niveles de respuesta (validar + educar → social proof → evaluación sin compromiso).

### v17.42 (28/03/2026)
- **Fix F1 (shallow_call):** Llamadas de 20-45s donde el cliente dijo ≤8 palabras y colgó sin tool calls → `no_contesto`. Resuelve el caso "Hello?" de Maria Roa.
- **Fix F2 (voicemail phrases):** Si todos los mensajes del usuario son frases de sistema de buzón de voz (inglés/español) → `user_spoke=False` → `no_contesto`. Resuelve el caso Barby Z.
- **Fix F3 (BUG3-v3):** `appointment.createdAt >= call.startedAt` en vez de `now_utc - 10min`. Elimina falso positivo de `agendo` cuando el mismo contacto tuvo una llamada anterior que sí agendó.

### v17.41 (28/03/2026)
- **Fix A:** `check_availability` devuelve 2 slots por día único (máx 14 slots cubriendo todos los días disponibles). Antes: 5 martes + 5 other (solo sábado y lunes visibles, miércoles/jueves/viernes invisibles).
- **Fix B:** `reschedule_appointment` exitoso = outcome `agendo` (antes era `no_agendo`).
- **Fix C:** Si cliente dice "quiero agendar" → Elena salta el pitch del SRA y va directo a `check_availability`.
- **Fix D:** No re-llamar `check_availability` si ya tiene los slots. Usar los slots que ya tiene.
- **Fix E:** `endCall` inmediato después de la despedida. Elimina el "¿Sigues ahí?" post-despedida.

### v17.40.1 (28/03/2026)
- **Fix duration:** `elena_call_duration` con 3 niveles de fallback. Nunca escribe 0 si hubo conversación.

### v17.40 (28/03/2026)
- 8 mejoras conversacionales en el prompt: firstMessage más corto, lógica de martes, validación de día en slots, silencio de check_availability, límite de 25 palabras en PASOS 5-7, skip pitch si cliente ya quiere agendar, muletillas variadas, eliminación de PREGUNTA 2 como barrera.

### v17.39 (27/03/2026)
- **Fix voicemail:** `voicemail_by_elena` requiere `not user_spoke`. Resuelve clasificación incorrecta de llamadas reales como `no_contesto`.
- **Fix PASO 7:** Regla PROHIBIDO ABSOLUTO — `create_booking` solo después de que el cliente eligió un slot específico de `check_availability`.

---

## Problemas conocidos / pendientes

| Problema | Estado |
|----------|--------|
| Fix C (skip pitch) no se activó en llamada de Laury Matos | Pendiente verificar en llamada real post-v17.41 |
| `elena_success_eval=false` en llamadas sin cita | No es bug — es el comportamiento correcto del analysisPlan |

---

## Cómo hacer deploy

```bash
# 1. Modificar app.py y/o system_prompt.txt
# 2. Verificar sintaxis
cd /home/ubuntu/elena-vapi-server && python3.11 -m py_compile app.py

# 3. Commit y push (Render auto-deploya desde main)
git add app.py system_prompt.txt && git commit -m "vX.XX: descripción" && git push

# 4. Sincronizar prompt a Vapi (si se modificó system_prompt.txt)
python3.11 << 'EOF'
import requests, json, os
VAPI_KEY = "VAPI_KEY_REDACTED_ROTATED_2026_04_24"
ASSISTANT_ID = "1631c7cf-2914-45f9-bf82-6635cdf00aba"
prompt = open("/home/ubuntu/elena-vapi-server/system_prompt.txt").read()
r = requests.patch(
    f"https://api.vapi.ai/assistant/{ASSISTANT_ID}",
    headers={"Authorization": f"Bearer {VAPI_KEY}", "Content-Type": "application/json"},
    json={"model": {"provider": "openai", "model": "gpt-4o", "messages": [{"role": "system", "content": prompt}]}}
)
print(r.status_code, r.json().get("id"))
EOF

# 5. Verificar deploy
curl -s https://elena-pdem.onrender.com/health | python3.11 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version'))"
```
