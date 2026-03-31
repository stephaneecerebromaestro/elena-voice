# ELENA + ARIA — Documento de Handoff Maestro
**Versión:** 2.1.1 | **Fecha:** 31 marzo 2026 | **Propietario:** Juan Garcia — Laser Place Miami

> **Instrucciones para el agente que recibe este documento:** Lee este archivo completo antes de tocar cualquier sistema. Contiene todos los IDs, claves, arquitectura, lógica de negocio, procedimientos operativos y contexto histórico del sistema. No asumas nada que no esté aquí.

---

## ÍNDICE

1. [Descripción del Sistema](#1-descripción-del-sistema)
2. [Mapa de Infraestructura](#2-mapa-de-infraestructura)
3. [Credenciales y Claves de Acceso](#3-credenciales-y-claves-de-acceso)
4. [Arquitectura Técnica Detallada](#4-arquitectura-técnica-detallada)
5. [Elena — El Agente de Voz](#5-elena--el-agente-de-voz)
6. [ARIA — El Sistema de Auditoría](#6-aria--el-sistema-de-auditoría)
7. [GHL — CRM y Workflows](#7-ghl--crm-y-workflows)
8. [Supabase — Base de Datos](#8-supabase--base-de-datos)
9. [Procedimientos Operativos (SOPs)](#9-procedimientos-operativos-sops)
10. [Historial de Versiones y Bugs Resueltos](#10-historial-de-versiones-y-bugs-resueltos)
11. [Estado Actual y Próximos Pasos](#11-estado-actual-y-próximos-pasos)

---

## 1. Descripción del Sistema

**Elena** es un agente de voz con IA que atiende llamadas inbound y outbound para **Laser Place Miami** (4649 Ponce De León Boulevard, Suite 302, Coral Gables, FL 33146). Su único objetivo es agendar evaluaciones gratuitas de Botox (Skin Reveal Analysis con Laury). No vende tratamientos por teléfono.

**ARIA** (Autonomous Review & Intelligence Agent) es el sistema de auditoría autónoma que monitorea cada llamada de Elena, detecta errores de playbook, clasifica outcomes, notifica a Juan por Telegram y genera reportes diarios/semanales.

El sistema opera en español (primario) e inglés (secundario), con leads provenientes de Facebook Ads que entran a GHL con el tag `botox_en_proceso`.

### Métricas actuales (31 marzo 2026)

| Métrica | Valor |
|---------|-------|
| Total llamadas procesadas | ~100 |
| Tasa de contacto (>30s) | ~18% |
| Tasa de conversión (conversación → cita) | ~6.7% histórico / en mejora con V2.1.1 |
| Bookings confirmados post-V2.1.1 | 1 (Juan Garcia, 31/03/2026) |
| Versión del servidor | v17.48 |
| Versión del prompt | V2.1.1 |

---

## 2. Mapa de Infraestructura

```
Facebook Ads Lead
       │
       ▼
GoHighLevel (CRM)
  Tag: botox_en_proceso
  Workflow: "Botox - Lead Nuevo AI (Principal)"
       │
       ▼
Vapi (Orquestador de voz)
  Asistente: Elena - Laser Place Miami
  Modelo: GPT-4o-mini (OpenAI)
  Voz: ElevenLabs b2htR0pMe28pYwCY9gnP
  Teléfonos: Twilio +17869835076 / Vapi nativo +17867430129
       │ tool calls
       ▼
Render (Servidor Flask — app.py v17.48)
  URL: https://elena-pdem.onrender.com
  Endpoint principal: /api/vapi/server-url
       │
       ├──► GHL API v2 (contactos, calendario, citas)
       ├──► Supabase (call_audits, aria_corrections)
       └──► Telegram Bot (@aria_elena_bot) — notificaciones ARIA
```

---

## 3. Credenciales y Claves de Acceso

> **IMPORTANTE:** Las claves marcadas como "Ver Render env vars" están almacenadas en el dashboard de Render como variables de entorno. No están hardcodeadas en el código. Para verlas: `render.com → elena-pdem → Environment`.

### IDs Públicos (hardcodeados en código, no son secretos)

| Variable | Valor | Descripción |
|----------|-------|-------------|
| `VAPI_ASSISTANT_ID` | `1631c7cf-2914-45f9-bf82-6635cdf00aba` | ID del asistente Elena en Vapi |
| `GHL_CALENDAR_ID` | `hYHvVwjKPykvcPkrsQWT` | ID del calendario de Laury en GHL |
| `GHL_LOCATION_ID` | `hzRj7DV9erP8tnPiTv7D` | ID de la location Laser Place Miami en GHL |
| `SUPABASE_URL` | `https://subzlfzuzcyqyfrzszjb.supabase.co` | URL del proyecto Supabase |
| `TELEGRAM_CHAT_ID` | `7962087583` | Chat ID de Juan en Telegram (para notificaciones ARIA) |
| `RENDER_SERVICE_ID` | `srv-d70lsh9aae7s739d8lo0` | ID del servicio en Render |
| `BOOKING_TITLE` | `Evaluación Botox - Laser Place Miami` | Título de las citas en GHL |

### Teléfonos

| Número | Proveedor | ID en Vapi | Uso |
|--------|-----------|-----------|-----|
| `+17869835076` | Twilio | `e8b9ed6d-1b92-44c5-86f2-b818e3f0eb98` | Inbound principal (serverUrl configurado) |
| `+17867430129` | Vapi nativo | `37057bae-947a-4bda-bd59-6d44578d51aa` | Outbound / respaldo |

### Variables de Entorno en Render (secretos)

| Variable | Descripción | Dónde obtenerla |
|----------|-------------|-----------------|
| `VAPI_API_KEY` | `VAPI_KEY_REDACTED_ROTATED_2026_04_24` | Dashboard Vapi → API Keys |
| `GHL_PIT` | Private Integration Token de GHL API v2 | GHL → Settings → Integrations → Private Integration |
| `ANTHROPIC_API_KEY` | API key de Anthropic (para ARIA/Claude) | console.anthropic.com |
| `SUPABASE_SERVICE_KEY` | Service Role Key JWT de Supabase | supabase.com → proyecto → Settings → API Keys → service_role |
| `GMAIL_FROM` | `vitusmediard@gmail.com` | Email remitente para reportes ARIA |
| `GMAIL_APP_PASSWORD` | App Password de Gmail | Google Account → Security → App Passwords |
| `TELEGRAM_BOT_TOKEN` | Token del bot `@aria_elena_bot` | @BotFather en Telegram |
| `RENDER_SERVER_URL` | `https://elena-pdem.onrender.com` | Fijo |

### Accesos a Dashboards

| Sistema | URL | Credenciales |
|---------|-----|-------------|
| **Vapi** | `app.vapi.ai` | Cuenta de Juan |
| **Render** | `render.com` | Cuenta de Juan |
| **GitHub** | `github.com/stephaneecerebromaestro/elena` | Cuenta stephaneecerebromaestro |
| **GHL** | `app.gohighlevel.com` | Cuenta de Juan |
| **Supabase** | `supabase.com/dashboard/project/subzlfzuzcyqyfrzszjb` | Cuenta de Juan |
| **Twilio** | `console.twilio.com` | Cuenta de Juan |

---

## 4. Arquitectura Técnica Detallada

### Flujo de una llamada inbound completa

```
1. Lead llama a +17869835076 (Twilio)
2. Twilio → Vapi (serverUrl configurado en el phone number)
3. Vapi inicia la conversación con Elena (GPT-4o-mini + prompt V2.1.1)
4. Elena saluda: "Hola, habla Elena de Laser Place Miami..."
5. Durante la conversación, Elena ejecuta tool calls:
   a. get_contact({}) — busca el contacto por callerPhone
   b. check_availability({}) — obtiene slots del calendario GHL
   c. create_booking({contactId, startTime}) — crea la cita
6. Cada tool call va a: POST https://elena-pdem.onrender.com/api/vapi/server-url
7. El servidor inyecta callerPhone en todos los tool calls (fix B1)
8. Al terminar la llamada, Vapi envía end-of-call-report al servidor
9. El servidor clasifica el outcome (agendo/no_agendo/llamar_luego/no_contesto/error_tecnico)
10. Escribe el outcome en el campo elena_last_outcome del contacto en GHL
11. Activa el tag elena_resultado_botox → dispara el Workflow de GHL
12. ARIA (polling cada 3 min) detecta la llamada nueva y la audita con Claude
13. ARIA envía notificación Telegram a Juan con el resultado
```

### Servidor Flask — Endpoints principales

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/api/vapi/server-url` | POST | Receptor principal de tool calls de Vapi |
| `/update-date` | GET/POST | Actualiza la fecha en el prompt (llamar diariamente) |
| `/health` | GET | Health check — devuelve versión del servidor |
| `/aria/telegram/webhook` | POST | Webhook de Telegram para comandos ARIA |
| `/aria/vapi/end-of-call` | POST | Webhook alternativo de end-of-call (outbound) |
| `/aria/report/daily` | POST | Dispara reporte diario manualmente |
| `/aria/corrections/pending` | GET | Lista correcciones pendientes de ARIA |

### Tools que usa Elena (9 tools activos)

| Tool | Cuándo se usa | Lógica crítica |
|------|--------------|----------------|
| `get_contact` | Al inicio, cuando cliente dice "sí" | callerPhone SIEMPRE tiene prioridad (fix B1) |
| `create_contact` | Si no existe el contacto | callerPhone SIEMPRE tiene prioridad (fix B1) |
| `check_availability` | Cuando cliente quiere agendar | Devuelve 2 slots/día, máx 14 slots, 30 días |
| `create_booking` | Cuando cliente confirma slot específico | Tiene deduplicación — bloquea segunda cita mismo día |
| `get_appointment_by_contact` | Para verificar citas existentes | Filtra por calendarId |
| `reschedule_appointment` | Cambiar cita existente | Outcome = agendo si exitoso |
| `cancel_appointment` | Cancelar cita | — |
| `get_current_time` | Para calcular ventanas de callback | Devuelve hora Miami (EDT) |
| `schedule_callback` | Cuando cliente pide que lo llamen después | Escribe elena_callback_time y elena_callback_hours en GHL |

---

## 5. Elena — El Agente de Voz

### Configuración en Vapi

| Parámetro | Valor |
|-----------|-------|
| Nombre | Elena - Laser Place Miami |
| ID | `1631c7cf-2914-45f9-bf82-6635cdf00aba` |
| Modelo LLM | GPT-4o-mini (OpenAI) |
| Voz | ElevenLabs — ID: `b2htR0pMe28pYwCY9gnP` |
| Server URL | `https://elena-pdem.onrender.com/api/vapi/server-url` |
| Silence timeout | 30 segundos |
| Max duración | 600 segundos (10 min) |
| Background sound | Off |
| First message | "Hola, habla Elena de Laser Place Miami. Vi tu interés en el Botox y quería hacerte unas preguntas rápidas. ¿Tienes dos minutos?" |

### Prompt V2.1.1 — Arquitectura modular (4 bloques)

El prompt tiene 154 líneas y está en `/home/ubuntu/elena-vapi-server/system_prompt.txt` y en GitHub.

**Bloque 1 — System Kernel & Guardrails:** Identidad, reglas absolutas de comportamiento, STATE LOCK (fix B2/B3), manejo de silencios, buzón de voz, formato de horas.

**Bloque 2 — State Machine:** Flujo conversacional lineal en 5 estados (Apertura → Exploración → Validación → Propuesta → Confirmación). Excepción: si cliente dice "quiero agendar", salta directo a STATE 4.

**Bloque 3 — Objection Overrides:** Rescate de "llamar luego" (C2), Pivot C3 para rechazo de Botox con REGLA DE CONTEXTO SRA (fix B4), manejo de experiencia negativa previa, FAQs.

**Bloque 4 — Tool Execution Protocols:** Única fuente de verdad para el uso de tools. Cuándo y cómo usar cada herramienta. Regla de endCall.

### Lógica de Outcomes (Outcome Classifier en app.py)

El outcome se escribe en `elena_last_outcome` en el contacto de GHL. Árbol de decisión en orden de prioridad:

```
1. agendo
   → create_booking exitoso (appointmentId en tool result)
   → reschedule_appointment exitoso (newStartTime en tool result)

2. llamar_luego
   → schedule_callback exitoso

3. no_contesto
   → short_call: duración < 20s
   → shallow_call: 20-45s + customer-ended-call + ≤8 palabras + 0 tool calls
   → voicemail_by_customer: user_spoke=False + customer-ended-call
   → voicemail_by_elena: user_spoke=False + assistant-ended-call + duración < 45s
   → FIX F2: todos los mensajes del usuario son frases de buzón de voz

4. no_agendo (default)
   → Conversación real donde no se agendó
```

El tag `elena_resultado_botox` se agrega al contacto en GHL para disparar el workflow de seguimiento.

### Campos escritos en GHL por el servidor

| Campo GHL | Qué contiene |
|-----------|-------------|
| `elena_last_outcome` | agendo / no_agendo / llamar_luego / no_contesto / error_tecnico |
| `elena_resultado_botox` | Tag que dispara el workflow |
| `elena_call_duration` | Duración en segundos |
| `elena_total_calls` | Contador total de llamadas |
| `elena_conversations` | Contador de conversaciones reales (cliente habló) |
| `elena_callback_time` | ISO string del callback programado |
| `elena_callback_hours` | Horas hasta el callback (2/4/12/120) |
| `elena_success_eval` | true/false del analysisPlan de Vapi |

---

## 6. ARIA — El Sistema de Auditoría

### Qué hace ARIA

ARIA es un script Python (`aria_audit.py`) que corre como parte del servidor Render. Audita cada llamada de Elena usando Claude (Anthropic), detecta errores de playbook, y notifica a Juan por Telegram.

**ARIA NUNCA modifica el prompt de Elena ni el código del servidor.** Solo observa, audita y notifica.

### Componentes activos

| Componente | Estado | Detalle |
|-----------|--------|---------|
| Polling activo | ✅ Live | Cada 3 minutos — cubre inbound Twilio |
| Webhook end-of-call | ✅ Live | Para outbound cuando Vapi lo envía |
| Notificación Telegram | ✅ Live | 3 niveles: 🟢 (ok) / 🟡 (warning) / 🔴 (error) |
| Reporte diario | ✅ 8PM EDT | Cron en app.py |
| Reporte semanal | ✅ Domingo 8AM EDT | Cron en app.py |
| Comandos Telegram | ✅ Live | Ver lista abajo |
| Feedback loop | ✅ Live | Aprobar/rechazar correcciones via Telegram |

### Comandos Telegram disponibles

| Comando | Qué hace |
|---------|----------|
| `/score` | Score de Elena en las últimas 24h |
| `/audit` | Audita las últimas 5 llamadas manualmente |
| `/reporte` | Genera reporte inmediato |
| `/errores` | Lista los errores HIGH más frecuentes |
| `/eficacia` | Tasa de conversión y métricas clave |
| `/llamada [call_id]` | Detalle de una llamada específica |

### Tipos de errores que detecta ARIA

| Error | Severidad | Descripción |
|-------|-----------|-------------|
| `premature_endcall` | HIGH | Elena cuelga sin razón válida |
| `missed_close` | HIGH | No aprovecha oportunidad de agendar |
| `confusion_created` | MEDIUM | Da información contradictoria |
| `wrong_info` | MEDIUM | Información incorrecta sobre disponibilidad |
| `repeated_availability_check` | MEDIUM | Llama check_availability más de una vez |
| `playbook_violation` | MEDIUM | Flujo incorrecto |
| `language_switch` | LOW | Cambio de idioma no justificado |

### Tablas en Supabase

| Tabla | Descripción |
|-------|-------------|
| `call_audits` | Registro de cada llamada auditada (outcome, score, errores) |
| `aria_corrections` | Correcciones propuestas por ARIA pendientes de aprobación |
| `feedback_log` | Historial de aprobaciones/rechazos de Juan |
| `aria_daily_reports` | Reportes diarios generados |
| `aria_weekly_reports` | Reportes semanales generados |

---

## 7. GHL — CRM y Workflows

### Workflow principal: "Botox - Lead Nuevo AI (Principal)"

**Trigger:** Tag `botox_en_proceso` agregado al contacto.

**Estructura general del workflow:**

```
[TRIGGER: Tag botox_en_proceso]
       │
       ▼
[Condition 1: elena_last_outcome]
  ├── agendo → Update Stage: Agendado → Remove Tag → Remove from Workflow
  ├── no_agendo → Update Stage: Humano
  │              → Wait 2h → SMS Reactivación → Wait for Reply 24h
  │              → Si responde: Notificar equipo
  │              → Si no responde: Remove Tag → Remove from Workflow
  ├── llamar_luego → Update Stage: Llamar Luego
  │                → SMS Confirmación Callback
  │                → Condition (elena_callback_hours):
  │                  2h → Wait 2h → Go To siguiente llamada
  │                  4h → Wait 4h → Go To siguiente llamada
  │                  12h → Wait 12h → Go To siguiente llamada
  │                  120h → Wait 5d → Go To siguiente llamada
  └── no_contesto → [siguiente intento de llamada]

[Conditions 2, 3, 4: misma estructura — reintentos de llamada]

[Condition 5: último intento]
  └── None → Update Stage: Humano/Perdido → Remove Tag → Remove from Workflow
```

### SMS configurados en el workflow

**SMS Reactivación (no_agendo, 2h después):**
> "Hola {{contact.first_name}}, soy Elena de Laser Place. Vi que hablamos hace un rato pero no pudimos concretar tu cita para Botox. Si tienes alguna duda o prefieres agendar por aquí, solo dime. ¡Estoy atenta!"

**SMS Confirmación Callback (llamar_luego, inmediato):**
> "Hola {{contact.first_name}}, soy Elena. Dejé anotado llamarte más tarde como acordamos. Si prefieres adelantar y agendar tu cita por aquí mismo, solo respóndeme este mensaje. ¡Hablamos pronto!"

### Campos personalizados en GHL (Custom Fields)

Todos los campos `elena_*` son custom fields creados en GHL → Settings → Custom Fields → Contacts.

---

## 8. Supabase — Base de Datos

**URL:** `https://subzlfzuzcyqyfrzszjb.supabase.co`

**Schema SQL:** El archivo `aria_schema.sql` en el repositorio GitHub contiene el schema completo de las 5 tablas. Para recrear desde cero: Supabase Dashboard → SQL Editor → pegar contenido de `aria_schema.sql` → Run.

**Para consultar llamadas recientes:**
```sql
SELECT id, call_id, caller_phone, outcome, playbook_score, created_at
FROM call_audits
ORDER BY created_at DESC
LIMIT 20;
```

**Para ver errores frecuentes:**
```sql
SELECT error_type, COUNT(*) as frecuencia
FROM call_audits, jsonb_array_elements_text(errors_detected) as error_type
GROUP BY error_type
ORDER BY frecuencia DESC;
```

---

## 9. Procedimientos Operativos (SOPs)

### SOP-1: Actualizar el prompt de Elena

```bash
# 1. Editar el archivo
nano /home/ubuntu/elena-vapi-server/system_prompt.txt

# 2. Verificar que no hay errores de sintaxis obvios
wc -l system_prompt.txt  # debe ser ~154 líneas

# 3. Commit y push (Render auto-deploya)
cd /home/ubuntu/elena-vapi-server
git add system_prompt.txt
git commit -m "fix: descripción del cambio"
git push origin main

# 4. Sincronizar prompt a Vapi
python3.11 update_vapi_prompt.py

# 5. Verificar que Vapi recibió el prompt
# Output esperado: "Vapi updated OK" + "Prompt length: ~17000 chars"
```

### SOP-2: Actualizar el servidor (app.py)

```bash
# 1. Editar app.py
nano /home/ubuntu/elena-vapi-server/app.py

# 2. Verificar sintaxis Python
python3.11 -m py_compile app.py && echo "OK"

# 3. Commit y push (Render auto-deploya en ~3 minutos)
git add app.py
git commit -m "fix: descripción del cambio"
git push origin main

# 4. Verificar deploy
sleep 180 && curl -s https://elena-pdem.onrender.com/health | python3.11 -c \
  "import sys,json; d=json.load(sys.stdin); print('Version:', d.get('service'))"
```

### SOP-3: Verificar estado de todos los sistemas

```bash
# Render (servidor)
curl -s https://elena-pdem.onrender.com/health

# Vapi (prompt y tools)
python3.11 update_vapi_prompt.py  # modo dry-run — solo imprime estado

# GitHub (último commit)
cd /home/ubuntu/elena-vapi-server && git log --oneline -5

# Supabase (últimas 5 llamadas)
# → Ir a supabase.com/dashboard/project/subzlfzuzcyqyfrzszjb/editor
# → SELECT * FROM call_audits ORDER BY created_at DESC LIMIT 5;
```

### SOP-4: Diagnosticar una llamada específica

```python
# Obtener transcript completo de una llamada
import requests, json

VAPI_KEY = "VAPI_KEY_REDACTED_ROTATED_2026_04_24"
CALL_ID = "PEGAR_CALL_ID_AQUI"

resp = requests.get(
    f"https://api.vapi.ai/call/{CALL_ID}",
    headers={"Authorization": f"Bearer {VAPI_KEY}"},
    timeout=15
)
data = resp.json()
print(data.get("artifact", {}).get("transcript", ""))
```

### SOP-5: Emergencia — Elena no está respondiendo llamadas

1. Verificar que Render está online: `curl https://elena-pdem.onrender.com/health`
2. Si Render está caído: ir a `render.com` → elena-pdem → Manual Deploy
3. Verificar que el phone number Twilio tiene el serverUrl correcto en Vapi:
   - `app.vapi.ai` → Phone Numbers → `+17869835076` → serverUrl debe ser `https://elena-pdem.onrender.com/api/vapi/server-url`
4. Verificar que el asistente Vapi tiene el assistant ID correcto asignado al phone number

### SOP-6: Emergencia — Cambio urgente de prompt sin acceso al sandbox

1. Ir a `app.vapi.ai` → Assistants → "Elena - Laser Place Miami"
2. Click en "Edit"
3. Modificar el System Prompt directamente en el editor de Vapi
4. Click "Save"
5. **IMPORTANTE:** Luego sincronizar el cambio al archivo `system_prompt.txt` en GitHub para mantener consistencia

### SOP-7: Crear un segundo agente para otro tratamiento

**Pre-requisitos antes de empezar:**
- Elena V2.1.1 tiene 10+ conversaciones reales sin bugs críticos nuevos
- Tienes definido: tratamiento, objeciones típicas, ¿mismo calendario?, ¿misma doctora?

**Pasos:**
1. Duplicar el asistente en Vapi (botón "Duplicate")
2. Cambiar el nombre y el `firstMessage`
3. Crear nuevo `system_prompt_[tratamiento].txt` basado en V2.1.1 — modificar Bloques 2 y 3
4. Cambiar `BOOKING_TITLE` en las variables de entorno de Render (o crear un nuevo servicio)
5. Crear nuevo calendario en GHL si el tratamiento tiene horarios diferentes
6. Actualizar `GHL_CALENDAR_ID` en las variables de entorno
7. Duplicar el workflow de GHL y ajustar los SMS

---

## 10. Historial de Versiones y Bugs Resueltos

### V2.1.1 (31 marzo 2026) — Versión actual

**Prompt:** V2.1.1 (154 líneas, arquitectura modular de 4 bloques)
**Servidor:** v17.48

Bugs resueltos en esta versión (detectados en primera llamada real de V2):

| Bug | Descripción | Fix aplicado |
|-----|-------------|--------------|
| B1 | Elena hallucina teléfono (+17860000000), servidor lo acepta | `callerPhone` SIEMPRE tiene prioridad en inbound — Elena no puede sobreescribir el número real |
| B2 | Switch a inglés mid-conversación después de "Sí" | STATE LOCK en Bloque 1: una vez en STATE 4, NO reiniciar saludo ni cambiar idioma |
| B3 | `check_availability` llamado dos veces por pérdida de estado | Mismo STATE LOCK — el LLM no puede retroceder a STATE 1/2 después de recibir slots |
| B4 | SRA ofrecido para lip augmentation (tratamiento incorrecto) | REGLA DE CONTEXTO SRA: si cliente menciona otro tratamiento, primero preguntar si también quiere Botox |

### V2.1.0 (30 marzo 2026) — Primera versión del prompt V2

Reescritura completa del prompt de 596 líneas (V1) a 152 líneas (V2) con arquitectura modular. Pasó 86/86 items del inventario y 11/11 simulaciones antes del deploy.

### V1 → V2 — Por qué se reescribió

El prompt V1 acumuló 20+ capas de "FIX" durante semanas de producción, generando:
- Reglas contradictorias sobre `endCall` (causaba cuelgues prematuros)
- Instrucciones de `check_availability` en 4 lugares distintos
- Carga cognitiva innecesaria (tabla de cálculo de horas, campos de GHL que el LLM no usa)
- 20 `premature_endcall` y 19 `missed_close` en 87 llamadas auditadas

### Versiones del servidor (historial relevante)

| Versión | Fecha | Cambio principal |
|---------|-------|-----------------|
| v17.48 | 31/03/2026 | Fix B1 callerPhone inbound override; bump de versión |
| v17.47 | 30/03/2026 | Regla absoluta "quiero agendar" = check_availability |
| v17.46 | 28/03/2026 | 9 tools como inline model.tools (fix crítico de Vapi) |
| v17.44 | 28/03/2026 | Fix shallow_call para assistant-ended-call <45s |
| v17.42 | 28/03/2026 | Fix voicemail phrases, BUG3-v3, shallow_call |
| v17.41 | 28/03/2026 | check_availability 2 slots/día, reschedule = agendo |
| v17.40 | 28/03/2026 | 8 mejoras conversacionales al prompt |
| v17.35 | 28/03/2026 | 10 fixes de auditoría (B1-B10) |
| v17.39 | 28/03/2026 | Fix voicemail_by_elena, prohibición de create_booking prematuro |

---

## 11. Estado Actual y Próximos Pasos

### Estado al 31 marzo 2026

| Sistema | Estado | Versión/Commit |
|---------|--------|----------------|
| Vapi | ✅ Activo | Prompt V2.1.1 (17,138 chars) |
| Render | ✅ Healthy | `Elena AI Tool Server v17.48` |
| GitHub | ✅ Synced | commit `7418e08` en `main` |
| GHL Workflow | ✅ Publicado | SMS sequences activos |
| Supabase | ✅ Activo | ~88 registros en call_audits |
| ARIA | ✅ Live | Polling cada 3 min, Telegram activo |

### Próximos pasos priorizados

**Prioridad 1 (inmediata):** Monitorear las próximas 10-15 llamadas reales para confirmar que los 4 fixes de V2.1.1 funcionan en producción. Revisar en Supabase que los outcomes se clasifiquen correctamente y que los bookings en GHL tengan nombre/email/teléfono reales.

**Prioridad 2 (1-2 semanas):** Una vez que Elena tenga 10+ conversaciones reales sin bugs críticos nuevos, evaluar si lanzar un segundo agente para otro tratamiento. El runbook está en SOP-7.

**Prioridad 3 (futuro):** Implementar few-shot dinámico en ARIA — inyectar los últimos 10 feedbacks aprobados/rechazados de Juan en el prompt de Claude antes de cada auditoría, para que ARIA calibre con el criterio real de Juan.

### Riesgo residual conocido

El STATE LOCK previene el reinicio de estado, pero no garantiza que el LLM no tenga alucinaciones de contexto en conversaciones muy largas (>15 turnos). Si aparece ese patrón, el siguiente paso sería reducir el contexto de herramientas o agregar un resumen de estado explícito en el prompt.

---

*Documento generado el 31 marzo 2026. Para actualizar este documento, editar `/home/ubuntu/ELENA_ARIA_HANDOFF_MAESTRO.md` y hacer commit al repositorio.*
