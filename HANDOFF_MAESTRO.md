> ⚠️ **ARCHIVO HISTÓRICO** — Este documento NO refleja el estado actual del sistema. El documento vigente es `CLAUDE.md`. Se conserva como referencia del camino recorrido.

---

# ELENA + ARIA — Documento de Handoff Maestro
**Versión:** 2.2.0 | **Fecha:** 4 abril 2026 | **Propietario:** Juan Garcia — Laser Place Miami

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

**ARIA** (Autonomous Review & Intelligence Agent) es el sistema de auditoría autónoma que monitorea cada llamada de Elena, detecta errores de playbook, clasifica outcomes, notifica a Juan por Telegram, genera reportes diarios/semanales, y extrae inteligencia de cliente (objeciones, zonas de interés, nivel de compra).

El sistema opera en español (primario) e inglés (secundario), con leads provenientes de Facebook Ads que entran a GHL con el tag `botox_en_proceso`.

### Métricas actuales (4 abril 2026)

| Métrica | Valor |
|---------|-------|
| Total llamadas procesadas | ~120+ |
| Tasa de contacto (>30s) | ~18% |
| Tasa de conversión (conversación → cita) | ~6.7% histórico / mejorando con V2.2.0 |
| Versión del servidor | v17.57 |
| Versión del prompt | V2.2.0 |
| Versión de ARIA | 3.1.1 |
| Modelo de auditoría | claude-sonnet-4-5 |

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
Render (Servidor Flask — app.py v17.57)
  URL: https://elena-pdem.onrender.com
  Endpoint principal: /api/vapi/server-url
       │
       ├──► GHL API v2 (contactos, calendario, citas)
       ├──► Supabase (call_audits, call_intelligence, aria_corrections)
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
3. Vapi inicia la conversación con Elena (GPT-4o-mini + prompt V2.2.0)
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
13. ARIA extrae inteligencia de cliente y la guarda en call_intelligence
14. ARIA envía notificación Telegram a Juan con el resultado
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

> **CRÍTICO:** `model.toolIds` está VACÍO intencionalmente. Los tools nativos de GHL fueron removidos porque su OAuth session (GoHighLevelMCPCredential) está rota/expirada. Todos los tools usan `model.tools` como inline server-url tools. NO agregar toolIds de vuelta sin verificar que el OAuth de GHL esté activo.

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

### Prompt V2.2.0 — Arquitectura modular (5 bloques)

El prompt tiene 179 líneas y está en `system_prompt.txt` en GitHub. Vapi tiene 19,567 chars activos (incluye la sección de fecha inyectada por `/update-date`).

**Bloque 1 — System Kernel & Guardrails:** Identidad, reglas absolutas de comportamiento, diccionario STT ampliado (fix V2.2), STATE LOCK (fix B2/B3), manejo de silencios, detección de sistemas automatizados e IVR (fix V2.2), formato de horas.

**Bloque 2 — State Machine:** Flujo conversacional lineal en 5 estados (Apertura → Exploración → Validación → Propuesta → Confirmación). Excepción ampliada: lista explícita de frases truncadas por STT que saltan al STATE 4 (fix V2.2). Confirmación antes de booking y verificación final de hora (fix V2.2).

**Bloque 3 — Objection Overrides:** Rescate de "llamar luego" (C2), Pivot C3 para rechazo de Botox con REGLA DE CONTEXTO SRA (fix B4), manejo de experiencia negativa previa, FAQs, regla dura de 2 rechazos con contador explícito (fix V2.2).

**Bloque 4 — Tool Execution Protocols:** Única fuente de verdad para el uso de tools. Regla anti-contradicción para check_availability (fix V2.2). Cuándo y cómo usar cada herramienta. Regla de endCall.

**Bloque 5 — Base de Conocimiento Clínico:** Información sobre Xeomin, proceso, resultados, zonas, cuidados post, contraindicaciones.

### Fixes aplicados en V2.2.0 (4 abril 2026)

| Fix | Descripción | Error resuelto |
|-----|-------------|----------------|
| F1 | Diccionario STT ampliado — "agendar un", "agendar un poquito", "agenda", "una cita" como triggers | `missed_close` ×18 |
| F2 | Excepción de intención ampliada — lista explícita de frases truncadas por STT | `missed_close` ×18 |
| F3 | Regla anti-contradicción — nunca declarar disponibilidad antes de ejecutar `check_availability` | `confusion_created` ×26 |
| F4 | Confirmación antes de booking — confirmar slot elegido antes de `create_booking` | `wrong_info` ×10 |
| F5 | Verificación final de hora — usar valor exacto del tool result en despedida | `wrong_info` ×10 |
| F6 | Regla dura de 2 rechazos — contador explícito, PROHIBIDO más pivots después del segundo No | `playbook_violation` ×34 |
| F7 | Detección de sistemas automatizados ampliada — IVR, asistentes de voz, "press 1", "please hold" | Reducción `premature_greeting` falsos |

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

ARIA es un script Python (`aria_audit.py`) que corre como parte del servidor Render. Audita cada llamada de Elena usando Claude (Anthropic), detecta errores de playbook, extrae inteligencia de cliente, y notifica a Juan por Telegram.

**ARIA NUNCA modifica el prompt de Elena ni el código del servidor.** Solo observa, audita, notifica y propone correcciones para aprobación de Juan.

### Componentes activos

| Componente | Estado | Detalle |
|-----------|--------|---------|
| Polling activo | ✅ Live | Cada 3 minutos — cubre inbound Twilio |
| Webhook end-of-call | ✅ Live | Para outbound cuando Vapi lo envía |
| Notificación Telegram | ✅ Live | 3 niveles: 🟢 (ok) / 🟡 (warning) / 🔴 (error) |
| Reporte diario | ✅ 8PM EDT | Cron en app.py |
| Reporte semanal | ✅ Sábado 9AM EDT | Cron en app.py — errores recurrentes de la semana |
| Comandos Telegram | ✅ Live | 18 comandos — ver lista abajo |
| Inteligencia de cliente | ✅ Live | Extrae zonas, objeciones, buying stage, etc. |
| Few-shot dinámico | ✅ Live | ARIA aprende de aprobaciones/rechazos previos de Juan |

### Comandos Telegram disponibles (ARIA v3.1.1)

| Categoría | Comando | Qué hace |
|-----------|---------|----------|
| **Reportes** | `/reporte hoy` | Reporte del día en curso |
| | `/reporte 2d` | Ayer + hoy |
| | `/reporte 7d` | Últimos 7 días con tabla |
| | `/reporte mes [nombre]` | Mes completo (ej: `/reporte mes marzo`) |
| **Audits** | `/audit 24h` | Re-audita últimas 24h |
| | `/audit 7d` | Re-audita + análisis de patrones |
| | `/audit mes [nombre]` | Auditoría profunda de un mes |
| **Diagnóstico** | `/errores [días]` | Top errores de Elena (ej: `/errores 7`) |
| | `/score` | Score de Elena últimos 7 días |
| | `/eficacia` | Precisión de ARIA vs clasificación original |
| | `/llamada [id]` | Detalle completo de una llamada específica |
| **Inteligencia** | `/intel [días]` | Zonas, objeciones, preguntas frecuentes |
| | `/leads calientes` | Leads con intención alta sin agendar |
| | `/backfill [días]` | Poblar inteligencia histórica (~$0.30-0.50) |
| **Sistema** | `/status` | Estado del sistema en tiempo real |
| | `/contacto [teléfono]` | Historial completo de un contacto |
| | `/tendencia` | Gráfico de conversión últimos 30 días |
| | `/ayuda` | Lista de todos los comandos |

### Tipos de errores que detecta ARIA (definiciones corregidas en V2.2)

| Error | Severidad | Condición para marcar |
|-------|-----------|----------------------|
| `missed_close` | HIGH | Solo si cliente expresó intención clara ("quiero agendar") Y Elena no ejecutó check_availability |
| `wrong_info` | MEDIUM | Solo si Elena confirmó hora/fecha DIFERENTE al tool result de create_booking |
| `playbook_violation` | MEDIUM | Ej: más de 2 pivots después de rechazo claro |
| `premature_endcall` | HIGH | NO marcar si outcome = no_contesto |
| `repeated_availability_check` | MEDIUM | NO marcar si cliente pidió un día diferente entre llamadas |
| `language_switch` | LOW | Solo si cliente habló 3+ frases en inglés y Elena siguió en español |
| `confusion_created` | MEDIUM | Solo si contradicción ocurrió ANTES de ejecutar check_availability |
| `premature_greeting` | MEDIUM | NO marcar en outbound — Elena siempre habla primero |
| `missed_objection` | MEDIUM | Objeción no manejada |
| `unnecessary_tool_call` | LOW | Tool llamado sin necesidad |

### Tablas en Supabase

| Tabla | Descripción |
|-------|-------------|
| `call_audits` | Registro de cada llamada auditada (outcome, score, errores, transcript) |
| `call_intelligence` | Inteligencia de cliente por llamada (zonas, objeciones, buying stage, etc.) — agregada en ARIA v3.0 |
| `aria_corrections` | Correcciones propuestas por ARIA pendientes de aprobación de Juan |
| `feedback_log` | Historial de aprobaciones/rechazos de Juan (usado para few-shot dinámico) |
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

**Schema SQL:** El archivo `aria_schema.sql` en el repositorio GitHub contiene el schema de las tablas base. La tabla `call_intelligence` fue agregada en ARIA v3.0 — su schema está en `migrations/001_call_intelligence.sql`.

**Para consultar llamadas recientes:**
```sql
SELECT id, vapi_call_id, phone_number, original_outcome, aria_outcome, playbook_adherence_score, created_at
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

**Para ver inteligencia de cliente reciente:**
```sql
SELECT phone_number, buying_stage, interest_level, objections, barriers, created_at
FROM call_intelligence
WHERE call_type = 'real_conversation'
ORDER BY created_at DESC
LIMIT 20;
```

**Para ver leads calientes (intención alta sin agendar):**
```sql
SELECT ci.phone_number, ci.interest_level, ci.buying_stage, ci.objections, ca.original_outcome
FROM call_intelligence ci
JOIN call_audits ca ON ci.vapi_call_id = ca.vapi_call_id
WHERE ci.interest_level >= 4
  AND ca.original_outcome != 'agendo'
  AND ci.created_at > NOW() - INTERVAL '7 days'
ORDER BY ci.interest_level DESC;
```

---

## 9. Procedimientos Operativos (SOPs)

### SOP-1: Actualizar el prompt de Elena

```bash
# 1. Editar el archivo en el sandbox
nano /home/ubuntu/elena-vapi-server/system_prompt.txt

# 2. Verificar líneas
wc -l system_prompt.txt

# 3. Commit y push (Render auto-deploya en ~3 min)
cd /home/ubuntu/elena-vapi-server
git add system_prompt.txt
git commit -m "fix: descripción del cambio — Elena VX.X.X"
git push origin main

# 4. Sincronizar prompt a Vapi INMEDIATAMENTE
python3 update_vapi_prompt.py
# Output esperado: "Vapi updated OK" + "Prompt length: ~19000-20000 chars" + "model.tools: 9"
```

> **IMPORTANTE:** `update_vapi_prompt.py` lee desde `/home/ubuntu/elena-vapi-server/system_prompt.txt`. Si editas en otro path, el script no lo verá.

### SOP-2: Actualizar el servidor (app.py)

```bash
# 1. Editar y verificar sintaxis
nano /home/ubuntu/elena-vapi-server/app.py
python3 -m py_compile app.py && echo "OK"

# 2. Commit y push
git add app.py
git commit -m "fix: descripción del cambio vX.X.XX"
git push origin main

# 3. Verificar deploy (esperar ~3 min)
sleep 180 && curl -s https://elena-pdem.onrender.com/health | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('Version:', d.get('service'))"
```

### SOP-3: Actualizar ARIA (aria_audit.py)

```bash
# 1. Editar y verificar sintaxis
nano /home/ubuntu/elena-vapi-server/aria_audit.py
python3 -m py_compile aria_audit.py && echo "OK"

# 2. Commit y push (ARIA se reinicia con el servidor — polling reanuda ~30s después)
git add aria_audit.py
git commit -m "fix(aria): descripción del cambio"
git push origin main
```

### SOP-4: Verificar estado de todos los sistemas

```bash
# Render
curl -s https://elena-pdem.onrender.com/health

# Vapi (prompt y tools)
python3 /home/ubuntu/elena-vapi-server/update_vapi_prompt.py

# GitHub (último commit)
cd /home/ubuntu/elena-vapi-server && git --no-pager log --oneline -5

# ARIA (via Telegram) → /status
```

### SOP-5: Diagnosticar una llamada específica

```python
import requests
VAPI_KEY = "VAPI_KEY_REDACTED_ROTATED_2026_04_24"
CALL_ID = "PEGAR_CALL_ID_AQUI"
resp = requests.get(f"https://api.vapi.ai/call/{CALL_ID}",
    headers={"Authorization": f"Bearer {VAPI_KEY}"}, timeout=15)
print(resp.json().get("artifact", {}).get("transcript", ""))
```

O más rápido: enviar `/llamada [call_id]` a ARIA por Telegram.

### SOP-6: Emergencia — Elena no está respondiendo llamadas

1. `curl https://elena-pdem.onrender.com/health` — verificar que Render está online
2. Si Render está caído: `render.com` → elena-pdem → Manual Deploy
3. Verificar que el phone number Twilio tiene el serverUrl correcto en Vapi:
   - `app.vapi.ai` → Phone Numbers → `+17869835076` → serverUrl = `https://elena-pdem.onrender.com/api/vapi/server-url`

### SOP-7: Emergencia — Cambio urgente de prompt sin acceso al sandbox

1. `app.vapi.ai` → Assistants → "Elena - Laser Place Miami" → Edit
2. Modificar el System Prompt directamente en el editor de Vapi → Save
3. **IMPORTANTE:** Luego sincronizar el cambio a `system_prompt.txt` en GitHub para mantener consistencia

### SOP-8: Sincronizar el sandbox con el HEAD real de GitHub

```bash
cd /home/ubuntu/elena-vapi-server
git fetch origin && git pull origin main
git --no-pager log --oneline -3
```

> **NOTA:** El sandbox puede quedar desactualizado si hubo commits desde otro entorno. Siempre hacer `git pull` antes de editar.

### SOP-9: Crear un segundo agente para otro tratamiento

**Pre-requisitos:** Elena V2.2.0 con 10+ conversaciones reales sin bugs críticos. Tratamiento, objeciones típicas y calendario definidos.

1. Duplicar el asistente en Vapi (botón "Duplicate")
2. Cambiar nombre y `firstMessage`
3. Crear `system_prompt_[tratamiento].txt` basado en V2.2.0 — modificar Bloques 2 y 3
4. Cambiar `BOOKING_TITLE` en Render env vars (o crear nuevo servicio)
5. Crear nuevo calendario en GHL si tiene horarios diferentes
6. Actualizar `GHL_CALENDAR_ID` en env vars
7. Duplicar el workflow de GHL y ajustar los SMS

---

## 10. Historial de Versiones y Bugs Resueltos

### V2.2.0 (4 abril 2026) — Versión actual

**Prompt:** V2.2.0 (179 líneas, 19,567 chars en Vapi) | **Servidor:** v17.57 | **ARIA:** v3.1.1 | **Commit:** `3c6b168`

7 fixes al prompt de Elena + 4 correcciones al clasificador de ARIA. Ver tabla completa en Sección 5.

### V2.1.1 (31 marzo 2026)

**Prompt:** V2.1.1 (154 líneas) | **Servidor:** v17.48 | **Commit:** `7418e08`

| Bug | Descripción | Fix aplicado |
|-----|-------------|--------------|
| B1 | Elena hallucina teléfono (+17860000000), servidor lo acepta | `callerPhone` SIEMPRE tiene prioridad en inbound |
| B2 | Switch a inglés mid-conversación después de "Sí" | STATE LOCK en Bloque 1 |
| B3 | `check_availability` llamado dos veces por pérdida de estado | STATE LOCK — el LLM no puede retroceder a STATE 1/2 |
| B4 | SRA ofrecido para lip augmentation (tratamiento incorrecto) | REGLA DE CONTEXTO SRA |

### Fixes del servidor post-31/03 (v17.49 → v17.57)

| Versión | Commit | Cambio |
|---------|--------|--------|
| v17.49 | `644af95` | ARIA coverage: non-daemon thread + Telegram independiente de Supabase |
| v17.50 | `473fca2` | ARIA cobertura 100%, dedup in-memory lock, no_interesado detection |
| v17.51 | `92fcdd3` | Gunicorn 4 workers → 1 worker + 8 threads (fix concurrencia) |
| v17.52 | `dac1413` | ARIA polling daemon=False + sleep inicial 30s |
| v17.53 | `00bba42` | ARIA race condition: lee GHL post-Claude para eliminar discrepancia |
| v17.57 | `e7d0816` | FIX H1/H2/H3: filtrar llamadas sin datos/transcript, forzar status=ended |

### ARIA — Evolución de versiones

| Versión | Commit | Cambio principal |
|---------|--------|-----------------|
| v3.0.0 | `4004a3a` | 13 comandos nuevos + inteligencia de cliente (call_intelligence) |
| v3.1.0 | `0ce8dd2` | Fix 13 bugs post-deploy |
| v3.1.1 | `07cb7d5` | Fix /tendencia + /backfill + mejoras /intel y /leads |
| v3.1.1+ | `830c1ac` | Fix Supabase 23514: audit_status + sanitizar outcomes inválidos |
| v3.1.1+ | `3c6b168` | 4 correcciones al clasificador de errores (falsos positivos) |

### V2.1.0 (30 marzo 2026)

Reescritura completa del prompt de 596 líneas (V1) a 152 líneas (V2) con arquitectura modular. Pasó 86/86 items del inventario y 11/11 simulaciones antes del deploy.

---

## 11. Estado Actual y Próximos Pasos

### Estado al 4 abril 2026

| Sistema | Estado | Versión/Commit |
|---------|--------|----------------|
| Vapi | ✅ Activo | Prompt V2.2.0 (19,567 chars), 9 tools, analysisPlan preservado |
| Render | ✅ Healthy | `Elena AI Tool Server v17.57` |
| GitHub | ✅ Synced | commit `3c6b168` en `main` |
| GHL Workflow | ✅ Publicado | SMS sequences activos |
| Supabase | ✅ Activo | call_audits + call_intelligence activas |
| ARIA | ✅ Live | v3.1.1, polling cada 3 min, 18 comandos Telegram, few-shot dinámico |

### Próximos pasos priorizados

**Prioridad 1 (inmediata):** Monitorear las próximas 20-30 llamadas con V2.2.0.
- `/score` debe subir de 29-42 a 60+ (falsos positivos eliminados)
- `/errores 7d` debe mostrar reducción drástica de `premature_greeting` y `premature_endcall`
- `confusion_created` nuevos = señal de que Fix 3 no funcionó

**Prioridad 2 (1-2 semanas):** Implementar Loop 2 — ARIA escribe inteligencia de cliente en GHL. Cuando Elena llama de vuelta al mismo número, `get_contact` devuelve `interest_level`, `buying_stage` y `objections` previos → Elena adapta su apertura. Todo el código base ya existe. Solo falta el puente.

**Prioridad 3 (2-4 semanas):** Con 20+ conversaciones reales sin bugs críticos nuevos, evaluar lanzar segundo agente para otro tratamiento. El runbook está en SOP-9.

### Riesgo residual conocido

El STATE LOCK previene el reinicio de estado, pero no garantiza que el LLM no tenga alucinaciones de contexto en conversaciones muy largas (>15 turnos). Si aparece ese patrón, el siguiente paso sería reducir el contexto de herramientas o agregar un resumen de estado explícito en el prompt.

El clasificador de ARIA ahora tiene definiciones más precisas para los 4 errores con mayor tasa de falsos positivos. Si aparecen nuevos patrones de falsos positivos, ajustar las cláusulas IMPORTANTE en `ARIA_SYSTEM_PROMPT` en `aria_audit.py`.

---

*Documento actualizado el 4 abril 2026. Para actualizar: editar `/home/ubuntu/elena-vapi-server/HANDOFF_MAESTRO.md` y hacer commit al repositorio.*
