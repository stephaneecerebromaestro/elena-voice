> ⚠️ **ARCHIVO HISTÓRICO** — Este documento NO refleja el estado actual del sistema. El documento vigente es `CLAUDE.md`. Se conserva como referencia del camino recorrido.

---

# Elena AI + ARIA — Contexto del Sistema (Documento Vivo)

> **Última actualización:** v17.46 + ARIA v1.1.0 — 29 marzo 2026  
> **Propósito:** Este archivo es la fuente de verdad del sistema. Leerlo al inicio de cada sesión para retomar sin perder contexto.

---

## Arquitectura general

El sistema tiene cuatro capas que trabajan juntas:

**Capa 1 — Vapi (voz + LLM):** Ejecuta las llamadas telefónicas. Usa el `system_prompt.txt` para guiar el comportamiento conversacional de Elena. Llama herramientas (tools) via webhooks al servidor de Render cuando necesita datos externos (calendario, contacto, cita).

**Capa 2 — Render (backend Flask):** Servidor Python en `app.py`. Recibe los tool calls de Vapi, consulta la API de GHL, y al final de cada llamada clasifica el outcome y escribe los campos en el contacto de GHL.

**Capa 3 — GHL (CRM + workflows):** Almacena los contactos, el calendario de citas, y ejecuta los workflows de automatización según el outcome de cada llamada.

**Capa 4 — ARIA (auditoría autónoma):** Script independiente `aria_audit.py`. Corre como cron job separado. Lee datos de Vapi/GHL, audita con Claude Sonnet 4.5, detecta errores y discrepancias, guarda en Supabase, notifica a Juan por Telegram con botones de aprobación/rechazo, y envía reportes diarios. **NUNCA modifica app.py ni Elena.**

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
| Supabase | `https://subzlfzuzcyqyfrzszjb.supabase.co` |

---

## Variables de entorno en Render

| Variable | Descripción | Valor |
|----------|-------------|-------|
| `GHL_PIT` | Private Integration Token de GHL (API v2) | Ver Render env vars |
| `GHL_CALENDAR_ID` | ID del calendario de citas | `hYHvVwjKPykvcPkrsQWT` |
| `GHL_LOCATION_ID` | ID de la location en GHL | `hzRj7DV9erP8tnPiTv7D` |
| `VAPI_API_KEY` | API key de Vapi | Ver Render env vars |
| `VAPI_ASSISTANT_ID` | ID del asistente de Vapi | `1631c7cf-2914-45f9-bf82-6635cdf00aba` |
| `BOOKING_TITLE` | Título de la cita (configurable) | `Evaluación Botox - Laser Place Miami` |
| `ANTHROPIC_API_KEY` | API key de Anthropic (para ARIA) | Ver Render env vars |
| `SUPABASE_URL` | URL del proyecto Supabase | `https://subzlfzuzcyqyfrzszjb.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Service Role Key JWT de Supabase | ✅ Configurado |
| `GMAIL_FROM` | Email remitente para reportes ARIA | `vitusmediard@gmail.com` |
| `GMAIL_APP_PASSWORD` | App Password de Gmail para SMTP | ✅ Configurado |
| `TELEGRAM_BOT_TOKEN` | Token del bot `@aria_elena_bot` | ✅ Configurado |
| `TELEGRAM_CHAT_ID` | Chat ID de Juan en Telegram | `7962087583` |
| `RENDER_SERVER_URL` | URL base del servidor en Render | `https://elena-pdem.onrender.com` |

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

## ARIA — Sistema de Auditoría Inteligente

### Arquitectura

```
Vapi API → aria_audit.py → Claude Sonnet 4.5 → Supabase
                        ↓
              discrepancia detectada (confianza ≥ 85%)
                        ↓
           Telegram @aria_elena_bot → Juan (botones ✅/❌)
                        ↓
           /aria/telegram/webhook (app.py) → apply_correction()
                        ↓
           GHL API (correcciones) + feedback_log (Supabase)
                        ↓
                   Email + Telegram (reportes diarios)
```

### Flujo de aprobación de correcciones

1. ARIA detecta discrepancia con confianza ≥ 85%
2. Guarda en `aria_corrections` (status=`pending`)
3. Envía mensaje Telegram a Juan con botones inline
4. Juan toca ✅ APROBAR o ❌ RECHAZAR
5. Telegram envía callback a `/aria/telegram/webhook`
6. Si aprueba: `apply_correction()` actualiza `elena_last_outcome` en GHL + status=`applied`
7. Si rechaza: status=`reverted` + se guarda en `feedback_log`
8. El mensaje original en Telegram se edita mostrando la decisión tomada

**Endpoints de ARIA en app.py:**

| Endpoint | Método | Descripción |
|---------|--------|-------------|
| `/aria/telegram/webhook` | POST | Recibe callbacks de botones Telegram |
| `/aria/correction/<id>/approve` | GET/POST | Aprobar corrección via link directo |
| `/aria/correction/<id>/reject` | GET/POST | Rechazar corrección via link directo |
| `/aria/corrections/pending` | GET | Listar correcciones pendientes |

### Archivos de ARIA

| Archivo | Descripción |
|---------|-------------|
| `aria_audit.py` | Script principal de auditoría |
| `aria_schema.sql` | Schema SQL para Supabase (ejecutar una vez en el SQL Editor) |

### Tablas en Supabase

| Tabla | Descripción |
|-------|-------------|
| `call_audits` | Registro maestro de cada llamada auditada |
| `feedback_log` | Feedback de Juan (✅/❌) sobre correcciones de ARIA |
| `daily_metrics` | Métricas agregadas por día |
| `aria_corrections` | Log de correcciones propuestas/aplicadas en GHL |
| `aria_config` | Configuración dinámica de ARIA |

### Outcomes que ARIA puede detectar

| Outcome | Criterio |
|---------|---------|
| `agendo` | create_booking o reschedule_appointment exitoso en tool calls |
| `no_contesto` | Duración <20s, buzón de voz, IVR, o cliente no habló |
| `no_agendo` | Conversación real sin cita agendada |
| `llamar_luego` | schedule_callback exitoso |
| `no_interesado` | Rechazo explícito del servicio |
| `error_tecnico` | Error técnico que impidió la llamada |

### Errores que ARIA detecta en Elena

| Error | Descripción |
|-------|-------------|
| `confusion_created` | Elena dio información contradictoria al cliente |
| `repeated_availability_check` | Llamó a check_availability más de 2 veces innecesariamente |
| `wrong_info` | Dio información incorrecta (disponibilidad, precio, etc.) |
| `missed_close` | Tenía oportunidad de agendar pero no la aprovechó |
| `premature_endcall` | Terminó la llamada cuando el cliente aún quería hablar |
| `playbook_violation` | No siguió el playbook (ej: ofreció precio antes de la evaluación) |
| `language_switch` | Cambio de idioma no manejado correctamente |

### Cómo ejecutar ARIA

```bash
# Modo piloto (sin guardar en Supabase) — para testing
python3.11 aria_audit.py pilot 3

# Modo auditoría normal (últimas 25h)
python3.11 aria_audit.py audit

# Modo auditoría con rango personalizado
python3.11 aria_audit.py audit 48

# Dry-run (audita pero no guarda)
python3.11 aria_audit.py dry-run
```

### Configuración del cron job en Render

**Cron Job activo:** `aria-daily-audit` (ID: `crn-d746bti4d50c73c27i30`)
- **Schedule:** `0 11 * * *` — todos los días a las 7:00am EDT (Miami)
- **Comando:** `python3.11 aria_audit.py audit 25`
- **Auto-deploy:** Sí — se actualiza con cada push a `main`

### Telegram Bot

- **Bot:** `@aria_elena_bot` — `t.me/aria_elena_bot`
- **Webhook:** `https://elena-pdem.onrender.com/aria/telegram/webhook`
- **Chat ID de Juan:** `7962087583`
- **Configurado:** ✅ 29/03/2026

### Estado de ARIA (29/03/2026)

| Componente | Estado |
|-----------|--------|
| Motor `aria_audit.py` v1.1.0 | ✅ Operativo |
| Schema Supabase (5 tablas) | ✅ Creado |
| 50 llamadas auditadas (primer run) | ✅ En Supabase |
| Variables en Render (18 variables) | ✅ Configuradas |
| Cron job diario 7am EDT | ✅ Activo |
| Email de reportes Gmail SMTP | ✅ Testeado |
| Telegram bot con botones ✅/❌ | ✅ Operativo |
| Webhook Telegram → Render | ✅ Configurado |
| Loop de correcciones GHL | ✅ Implementado |
| Feedback loop (feedback_log) | ✅ Implementado |
| Métricas diarias corregidas por ARIA | ⚠️ Pendiente (Fase 3) |

### Modelo LLM de ARIA

- **Modelo actual:** `claude-sonnet-4-5-20250929` (Claude Sonnet 4.5)
- **Razón:** Es el mejor modelo disponible en la cuenta de Anthropic del usuario
- **Nota:** `claude-3-5-sonnet-20241022` no está disponible en esta cuenta (da 404)

---

## Historial de versiones y fixes críticos

### ARIA v1.1.0 (29/03/2026)
- Loop de correcciones completo: Telegram → Juan → GHL
- Bot `@aria_elena_bot` con botones inline ✅ APROBAR / ❌ RECHAZAR
- Webhook `/aria/telegram/webhook` en app.py
- Función `apply_correction()` que aplica/rechaza en GHL y actualiza Supabase
- Endpoints alternativos `/aria/correction/<id>/approve` y `/reject`
- Resumen diario por Telegram (complementa el email)
- 18 variables de entorno configuradas en Render

### ARIA v1.0.0 (28/03/2026)
- Primer release del sistema de auditoría
- Auditoría con Claude Sonnet 4.5
- Detección de 7 tipos de errores de Elena
- Schema SQL completo en Supabase (5 tablas)
- 50 llamadas auditadas en el primer run
- Cron job diario 7am EDT en Render
- Email de reportes vía Gmail SMTP con App Password

### v17.46 (28/03/2026)
- **Fix:** `check_availability`, `get_contact`, `create_contact`, `create_booking` como inline model.tools — LLM ahora tiene los 9 tools via server URL

### v17.44 (28/03/2026)
- **Fix shallow_call:** Extendido para cubrir `assistant-ended-call` (y variantes) en llamadas <45s sin tool calls. Evita que llamadas cortas cortadas por Elena se marquen como `agendo`.
- **Fix F (Frases incompletas):** Regla en el prompt para que Elena NUNCA ejecute `endCall` si el cliente dice una frase incompleta o cortada (ej: "yo quiero agendar...").

### v17.43 (28/03/2026)
- **Fix B1-B6:** Correcciones adicionales en el outcome classifier, incluyendo falsos positivos de `agendo` cuando `startedAt=None`, detección de IVR, y ajustes en el umbral de `shallow_call`.
- **Update:** Número de teléfono actualizado a 786-953-2577 para escenarios de disclosure de IA.

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

---

## Problemas conocidos / pendientes

| Problema | Estado |
|----------|--------|
| Fix C (skip pitch) no se activó en llamada de Laury Matos | Pendiente verificar en llamada real post-v17.41 |
| `elena_success_eval=false` en llamadas sin cita | No es bug — es el comportamiento correcto del analysisPlan |
| SUPABASE_SERVICE_KEY pendiente de configurar | ARIA funciona en modo piloto; Supabase se activa cuando se provea la key |

---

## Cómo hacer deploy de Elena

```bash
# 1. Modificar app.py y/o system_prompt.txt
# 2. Verificar sintaxis
cd /home/ubuntu/elena-vapi-server && python3.11 -m py_compile app.py

# 3. Commit y push (Render auto-deploya desde main)
git add app.py system_prompt.txt && git commit -m "vX.XX: descripción" && git push

# 4. Sincronizar prompt a Vapi (si se modificó system_prompt.txt)
python3.11 update_vapi_prompt.py

# 5. Verificar deploy
curl -s https://elena-pdem.onrender.com/health | python3.11 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version'))"
```

## Cómo activar ARIA completamente

```bash
# 1. Obtener SUPABASE_SERVICE_KEY del dashboard:
#    https://supabase.com/dashboard/project/subzlfzuzcyqyfrzszjb/settings/api-keys
#    → Secret keys → Reveal

# 2. Ejecutar el schema SQL en Supabase:
#    https://supabase.com/dashboard/project/subzlfzuzcyqyfrzszjb/sql/new
#    → Pegar el contenido de aria_schema.sql → Run

# 3. Agregar variables de entorno en Render:
#    ANTHROPIC_API_KEY = sk-ant-api03-...
#    SUPABASE_URL = https://subzlfzuzcyqyfrzszjb.supabase.co
#    SUPABASE_SERVICE_KEY = eyJ... (el service_role key)

# 4. Hacer push del código a GitHub (Render auto-deploya)
git add aria_audit.py aria_schema.sql CONTEXT.md
git commit -m "feat: ARIA v1.0.0 — sistema de auditoría automática"
git push

# 5. Configurar cron job en Render:
#    → New Cron Job → Command: python3.11 aria_audit.py audit
#    → Schedule: 0 12 * * * (7am EST = 12:00 UTC)

# 6. Probar manualmente:
#    python3.11 aria_audit.py pilot 5
```
