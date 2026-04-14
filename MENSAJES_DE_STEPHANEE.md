# Mensajes de Stephanee para Elena Voice

## 2026-04-14 — Briefing y delegación de tareas

Hola Elena Voice. Soy Stephanee. Te paso contexto completo de dónde estamos y 3 tareas que vas a trabajar en paralelo a lo que hacemos Juan y yo. Léelo entero antes de arrancar.

---

## Lo que pasó hoy 14 abril (mientras tú dormías)

Juan y yo cerramos el **Bloque 2 completo (Operations Agent)** en Elena Ops:

- **T06 CI/CD Elena Chat** — 3 capas de protección sobre el repo `elena-chat-core`: GitHub Actions con 8 tests multi-bot + pre-push git hook + branch protection main (GitHub Pro $4/mes). Si rompes algo en producción, las 3 capas lo atrapan.
- **T07 Self-healing** — verificado end-to-end. Tumbamos vps-health, fue detectado en 3s, reparado en 6s, alerta Telegram funcional.
- **T10 Clasificación errores** — `error_classifier.py` integrado en `health_check.py`. Cada DOWN se clasifica N1/N2/N3 + tipo (infra/api/logic/data/config). Routing automático: N1 → self_heal, N2 → archivo `ALERTAS_DE_ELENA_OPS.md` en directorio del agente responsable, N3 → Telegram a Juan con "ACCION REQUERIDA".
- **Mejora 5 (coordinación Chat↔Voice)** — diferida hasta cerrar T01. Cuando Elena Chat termine sus 5 bots, retomamos.
- **T09 Redundancia Render** — diferido. No rentable hoy ($14-70/mes para cubrir riesgo de 10 min/año).
- **Elena Chat** — está codeando T01 (5 bots nuevos: Fillers, Bio, Acné, MatrixPro, Cicatrices). Bloqueada por GHL de Juan.

---

## Tu estado actual (lo que ya tienes funcionando)

| Componente | Estado | Detalle |
|------------|--------|---------|
| Servidor Flask | ✅ Producción | `elena-pdem.onrender.com`, srv-d70lsh9aae7s739d8lo0 |
| Vapi Botox | ✅ ACTIVO | assistant `1631c7cf-2914-45f9-bf82-6635cdf00aba`, número `+17869835076` |
| Vapi LHR | ✅ ACTIVO | assistant `3d5b77b5-f36c-4b95-88bc-4d6484277380`, número `+17867467292`, calendar `gQclGhEhZ2K1NkLal7pt` |
| Multi-tratamiento | ✅ Implementado | thread-local config en `app.py`, `set_active_config()` por assistantId |
| Auditoría | ✅ Hecha | 898 llamadas Botox analizadas (`AUDIT_898_CALLS.md`): 63% no contesta, 37% conecta, 16% conversaciones reales acaban en booking |
| Test real LHR | ✅ Hecho 2026-04-14 | Llamada real exitosa con número Miami |

**Fixes aplicados a ambos prompts el 13 abril:**
- firstMessage corto natural: "Hola, habla Elena de Laser Place. ¿Cómo estás?"
- No dar dirección por teléfono
- Anti-loop check_availability (máx 2 intentos)
- Slots redondeados a 30 min
- Asumir la venta, muletillas naturales
- No insistir si dice "No" inmediato
- Voice params: stability 0.4, speed 1.0, style 0.15

**Cross-treatment handling (decisión Juan):** NUNCA hagas handoff a otro número o bot. Si paciente pregunta por otro tratamiento, agendas igual la evaluación contigo. Pierde lead si haces handoff.

---

## Workflows GHL que disparan tus llamadas (CONTEXTO CRÍTICO)

Juan tiene workflows en GHL que disparan las llamadas a Vapi. Los conozco porque ayer me mostró screenshots:

**Workflow "Elena Voice Botox - Lead Nuevo" (probado y funcionando):**
- Trigger: Facebook Lead Form Botox + tag `botox_elena_...`
- WhatsApp Media Bienvenida + WhatsApp Bienvenida (inmediato)
- Wait 3 min
- **Hasta 5 llamadas** (#1 a #5 webhooks Vapi) con waits dinámicos entre ellas
- Después de cada llamada, Condition lee `elena_last_outcome` (custom field que TÚ llenas):
  - `agendo` → Stage Consulta Agendada → END
  - `no_agendo` → Stage Humano → SMS → END
  - `error_tecnico` → END
  - `no_interesado` → END
  - `llamar_luego` → WhatsApp + wait según `elena_callback_hours` (2h/4h/12h/5d) → siguiente llamada
  - `no_contesta` → SMS "no contesté" + wait → siguiente llamada

**REGLA DE ORO:** Tus custom fields `elena_last_outcome` y `elena_callback_hours` son lo que controla el workflow entero. Si dejas de actualizarlos correctamente, el workflow se rompe. Esto es CRÍTICO.

**Para LHR el workflow equivalente todavía no existe** — es trabajo de Juan en GHL (pendiente como T17).

---

## TUS 3 TAREAS DELEGADAS HOY

### Tarea 1 — CI/CD para tu propio repo (T19 simplificado)

Replica lo que hicimos para Elena Chat. Patrón a seguir está en `/root/agents/elena-chat/.github/workflows/ci.yml` y `/root/agents/elena-chat/tests/test_syntax.py` y `tests/test_bots_config.py`.

**Específico para ti:**
1. Crear `tests/test_syntax.py` que valide:
   - Imports de todos los módulos (`app`, `config`, `aria_audit`, etc.)
   - `config.ASSISTANTS` tiene Botox + LHR con todos los campos (`name`, `treatment`, `calendar_id`, `pipeline_id`, `booking_title`)
   - `set_active_config()` y `get_active_calendar_id()` existen en app.py
   - Endpoints Flask registrados (`/api/vapi/server-url`, `/health`, end-of-call)

2. Crear `.github/workflows/ci.yml` con:
   - Trigger: push + PR a main
   - Setup Python 3.12 + install requirements
   - Run tests
   - Env vars dummy para que imports no fallen

3. Crear `.githooks/pre-push` que corra los tests antes de cada push

4. **Después de pushear y verificar CI verde:** activar branch protection en main vía gh API:
```bash
gh api repos/stephaneecerebromaestro/elena-voice/branches/main/protection \
  --method PUT --input - << 'EOF'
{"required_status_checks":{"strict":true,"contexts":["Lint and test"]},"enforce_admins":false,"required_pull_request_reviews":null,"restrictions":null,"allow_force_pushes":false,"allow_deletions":false}
EOF
```

(Juan ya tiene GitHub Pro activo, branch protection funciona en repos privados.)

**Cuando termines, marca T19 como completo y avísame en este archivo (sección "Reportes a Stephanee").**

---

### Tarea 2 — Auditoría continua de llamadas

Hoy `AUDIT_898_CALLS.md` es estático (snapshot del 13 abril). Necesitamos que se actualice solo.

**Construye `scripts/audit_continuous.py`:**
- Cron semanal (lunes 8am Miami) que corre auditoría sobre últimas 7 días de llamadas
- Métricas a calcular por assistant (Botox y LHR separados):
  - Total llamadas, % contesta, % agendadas, costo promedio por booking
  - Top 3 razones de "no agendó" (analizar transcripts)
  - Patrones de error (loops check_availability, transfers raros, etc.)
  - Comparación vs semana anterior (mejora/empeora)
- Si conversión cae > 20% vs semana anterior → alerta Telegram a Juan
- Reporte se guarda en `audits/YYYY-MM-DD-weekly.md` y se envía resumen a Telegram

**Reglas:**
- NO llames a pacientes reales para testing. Usa data histórica de Vapi API.
- Usa el patrón de auditoría del 898 como base.
- Output legible para humano: tablas claras, conclusiones accionables, no data dump.

---

### Tarea 3 — Iteración de prompts basada en evidencia

Una vez que la Tarea 2 esté corriendo, usa los reportes para proponer cambios concretos a los prompts.

**Proceso:**
1. Identificar patrón de fallo en data (ej. "30% de pacientes interesados en Botox que preguntan por dolor terminan colgando")
2. Diseñar fix de prompt (ej. "agregar respuesta empática a pregunta de dolor: 'Es solo 3-5 minutos, prácticamente indoloro porque usamos crema anestésica'")
3. **NO aplicar directo a producción.** Documentar propuesta en `PROMPT_PROPOSALS.md` con: problema observado, fix propuesto, impacto esperado, riesgo
4. Avísame a mí (Stephanee) cuando tengas propuestas listas. Yo se las paso a Juan para aprobación.
5. Solo después de aprobación de Juan, aplicar cambio vía API Vapi y testear con 1-2 llamadas controladas

**Frecuencia:** Revisar y proponer cada 2 semanas (después de cada auditoría #2).

---

## Lo que NO debes hacer

- **NO hacer llamadas a pacientes reales para testing.** Solo data histórica o si Juan explícitamente pide test (ya pasó el de LHR).
- **NO modificar workflows GHL.** Eso es solo de Juan.
- **NO crear assistants nuevos en Vapi.** T17 (otros tratamientos) requiere workflow GHL nuevo de Juan primero.
- **NO tocar custom fields `elena_last_outcome` ni `elena_callback_hours` sin probar exhaustivo.** Rompes los workflows.
- **NO declarar algo completo sin evidencia verificable.** Regla #1 del ecosistema.

---

## Reportes a Stephanee

Cuando termines algo o necesites algo, escríbelo aquí abajo (yo lo leo periódicamente):

### [pendiente]
- (Vacío)

---

## Referencias rápidas

- **Tu CLAUDE.md:** `/root/agents/elena-voice/CLAUDE.md`
- **Repo:** `github.com/stephaneecerebromaestro/elena-voice`
- **Roadmap maestro:** `/root/stephanee/ROADMAP_MAESTRO.md`
- **Memoria Stephanee (compartida):** `/root/.claude/projects/-root/memory/`
- **Live session sync:** `/root/.claude/LIVE_SESSION.md`
- **Patrón CI/CD a copiar:** `/root/agents/elena-chat/.github/` y `/root/agents/elena-chat/tests/`

— Stephanee, 2026-04-14
