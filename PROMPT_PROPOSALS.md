# PROMPT_PROPOSALS.md — Propuestas de mejora para prompts Elena Voice
# Laser Place Miami · Creado 2026-04-14 · Elena Voice
# ════════════════════════════════════════════════════════════

> **Flujo de aprobación (regla dura):**
> 1. Elena Voice identifica patrón en data (auditorías + transcripts)
> 2. Escribe propuesta aquí con: problema, evidencia, fix, impacto esperado, riesgo
> 3. Stephanee la revisa y se la pasa a Juan
> 4. **Juan aprueba o rechaza.** Sin aprobación explícita de Juan, nada se aplica a producción
> 5. Si aprueba: Elena Voice aplica vía API Vapi, testea con 1-2 llamadas controladas (números internos, NO pacientes reales), reporta resultado
> 6. La propuesta pasa a estado `applied` con fecha y commit

**Frecuencia de revisión:** cada 2 semanas tras cada corrida de `audit_continuous.py`.

**Fuentes de evidencia:**
- `AUDIT_898_CALLS.md` (Stephanee, 13 abril, 898 llamadas Botox)
- `audits/YYYY-MM-DD-weekly.md` (cron semanal, a partir del 20 abril)
- Inspección directa de prompts live en Vapi API vs `system_prompt.txt` del repo

---

## Índice

| ID | Título | Estado | Bot | Fecha |
|----|--------|--------|-----|-------|
| P-001 | Dirección completa dictada en despedida (Botox) | `proposed` | Botox | 2026-04-14 |
| P-002 | Desync entre `system_prompt.txt` del repo y prompt live en Vapi | `proposed` | Botox | 2026-04-14 |
| P-003 | Loops de `check_availability` persisten pese a FIX D | `observing` | LHR | 2026-04-14 |

**Estados:** `proposed` → `approved` → `applied` → `verified` · o `rejected` · o `observing` (pre-propuesta, recolectando más data)

---

## P-001 — Dirección completa dictada en despedida (Botox)

**Estado:** `proposed` · **Bot:** Botox · **Prioridad:** ALTA · **Riesgo:** BAJO

### Problema observado
En STATE 5 del prompt live de Botox, Elena cierra la llamada dictando por voz la dirección completa:

> "…te esperamos el [día] a las [hora exacta del booking] **en Ponce De Leon Boulevard cuatro seis cuatro nueve, Suite tres cero dos, Coral Gables**. Te enviamos la confirmación por mensaje."

### Evidencia

**1. Patrón detectado por Stephanee en auditoría de 898 llamadas (13 abril):**
> "Dirección larga por teléfono — 'Ponce de León Boulevard cuatro seis cuatro nueve Suite tres cero dos' suena robótico. Pacientes preguntan '¿me mandan la dirección?' (2 de 20 bookings exitosos)." — `AUDIT_898_CALLS.md` línea 70

**2. Recomendación INMEDIATA del audit (línea 139):**
> "No dar dirección por teléfono — 'Te mando la info por mensaje'"

**3. Stephanee reportó en briefing 2026-04-14 que este fix ya estaba aplicado a ambos prompts. VERIFICACIÓN en Vapi API (14 abril):**
- Botox assistant `1631c7cf-...` STATE 5 live **todavía dicta la dirección completa**
- Es decir, el fix se reportó aplicado pero **no está en producción**

### Fix propuesto

Reemplazar el bloque STATE 5 del prompt live de Botox:

**Antes:**
```
"Excelente [Nombre], te esperamos el [día] a las [hora exacta del booking] en
Ponce De Leon Boulevard cuatro seis cuatro nueve, Suite tres cero dos, Coral Gables.
Te enviamos la confirmación por mensaje. ¡Que tengas un excelente día!"
```

**Después:**
```
"Excelente [Nombre], te esperamos el [día] a las [hora exacta del booking].
Te mando ahora la dirección y los detalles por mensaje. ¡Que tengas un excelente día!"
```

### Impacto esperado
- Despedida ~40% más corta (de ~28s a ~16s aprox en TTS). Menor costo Vapi por booking.
- Menos oportunidad de error de STT/TTS ("cuatro seis cuatro nueve" es riesgoso)
- Consistente con el workflow GHL que ya envía SMS de confirmación con dirección tras el booking

### Riesgo
- **BAJO.** Requisito: confirmar que el workflow GHL de Botox efectivamente envía la dirección en el SMS de confirmación (Stephanee lo mencionó en el briefing pero verificar antes de aplicar).
- Si el SMS no envía la dirección, el paciente queda sin la info → regresión. Mitigación: verificar con Juan o inspeccionar el workflow GHL antes de aplicar.

### Checklist antes de aplicar
- [ ] Juan aprueba
- [ ] Confirmar que SMS post-booking de Botox incluye la dirección completa
- [ ] Aplicar vía `update_vapi_prompt.py` o API directo
- [ ] Hacer 1 llamada de test (número interno, NO paciente real)
- [ ] Verificar transcript: despedida corta, sin dictar dirección
- [ ] Confirmar recibo del SMS con dirección
- [ ] Marcar `applied` con commit hash y fecha

---

## P-002 — Desync entre `system_prompt.txt` del repo y prompt live en Vapi (Botox)

**Estado:** `proposed` · **Bot:** Botox · **Prioridad:** MEDIA · **Riesgo:** BAJO (no toca producción)

### Problema observado

El archivo `system_prompt.txt` del repo (v2.2.0, línea 39) tiene el firstMessage antiguo y largo:

> "Hola, habla Elena de Laser Place Miami. **Vi tu interés en el Botox y quería hacerte unas preguntas rápidas. ¿Tienes dos minutos?**"

Pero Vapi live (14 abril) ya tiene la versión corta aplicada:

> "Hola, habla Elena de Laser Place. ¿Cómo estás?"

Hay drift entre la fuente de verdad documental y lo que corre en producción. Afecta a futuras ediciones: si alguien edita `system_prompt.txt` creyendo que es lo live, puede re-introducir el firstMessage largo por accidente.

### Evidencia

**1. `system_prompt.txt` línea 39** — todavía la versión antigua
**2. Vapi API (Botox `firstMessage`)** — "Hola, habla Elena de Laser Place. ¿Cómo estás?"
**3. Lo mismo pasa con STATE 5 (ver P-001) pero al revés:** el repo y live ambos tienen la dirección vieja

### Fix propuesto

Esto **NO es un cambio de producción**. Es hygiene de repo:

1. Pull del prompt live de Botox desde Vapi API (ya lo hice como parte de la investigación)
2. Sobrescribir `system_prompt.txt` con el contenido live exacto (21353 chars) → commit
3. Documentar en header del archivo: "Fuente de verdad: este archivo refleja el prompt live de Vapi al [fecha]. Cualquier edición debe aplicarse vía `update_vapi_prompt.py` Y commitear."
4. Agregar test en `tests/test_bots_config.py` que compare SHA de `system_prompt.txt` vs prompt live de Vapi y warn si diverge (opcional, puede ser ruidoso)

### Impacto esperado
- Fuente de verdad única para debugging futuro
- Evita re-introducción accidental de regresiones ya arregladas
- No toca producción

### Riesgo
- BAJO — solo edición de archivo de repo. Pero: si en el futuro alguien asume que editar `system_prompt.txt` actualiza Vapi (no lo hace — requiere `update_vapi_prompt.py`), puede creer que aplicó un fix que no aplicó. Mitigar con header claro.

---

## P-003 — Loops de `check_availability` persisten pese a FIX D (LHR)

**Estado:** `observing` (n=3 insuficiente) · **Bot:** LHR · **Prioridad:** BAJA-MEDIA

### Problema observado

El prompt live de LHR **ya tiene FIX D**:
> "NO llamar `check_availability` más de una vez por llamada, a menos que el cliente pida un día y los slots que ya tienes sean de una semana anterior."

Sin embargo, en la primera corrida de `audit_continuous.py` (ventana 2026-04-07 → 2026-04-14), **1 de 3 llamadas de LHR invocó `check_availability` ≥3 veces** (33%).

### Evidencia
- `audits/` dry-run del 14 abril: `check_availability_loop_calls = 1` sobre 3 llamadas LHR
- Stephanee en `AUDIT_898_CALLS.md` (Botox): "hasta 5 veces en una llamada cuando paciente pide otro horario. **Solución ya aplicada:** anti-loop de slots"

### Por qué NO propongo fix todavía
- n=3 es ruido estadístico. 1/3 es alto en porcentaje pero bajo en absoluto
- ARIA todavía no audita LHR (cobertura 0/3 esta semana) — falta contexto del transcript para saber si fue loop mal manejado o caso legítimo (paciente pidiendo varios días consecutivos)
- El FIX D ya está en el prompt, el LLM puede estarlo incumpliendo por lenguaje suave ("a menos que…") — solo fortalecer si el patrón se repite

### Acción
- Esperar 2-3 corridas más del cron semanal (3 lunes = 3 semanas)
- Si el ratio supera **10% sostenido en n≥20**, escribir una propuesta formal con fortalecimiento del wording (ej. "MÁXIMO 2 invocaciones POR LLAMADA, sin excepción")
- Paralelamente: arreglar cobertura ARIA de LHR (ver sección "No son fix de prompt" abajo)

---

## No son fix de prompt — pero observados y escalados

Estos patrones aparecen en la data pero **el fix no está en el prompt** — requieren cambios de infra o workflows GHL que dependen de Juan o de Stephanee. Los listo aquí para trazabilidad.

### O-001 — Cobertura ARIA 0% en LHR

**Problema:** Las 3 llamadas de LHR de la semana Abr 7-14 no fueron auditadas por ARIA (cobertura 0/3).

**Fix probable:** `aria_audit.py` audita por `VAPI_ASSISTANT_ID` (una sola variable). Para auditar ambos bots, el polling debe iterar sobre `config.ASSISTANTS`.

**Owner:** Elena Voice (es fix de código, no de prompt) — pendiente, abriré task aparte.

### O-002 — Tasa de no-contestación 63-95% (Botox)

**Problema:** 63% histórico; 94.9% esta semana. Conversión sana cuando hay conversación real (16%), pero el funnel se rompe antes de conectar.

**Fix probable (NO de prompt):**
- SMS previo a la llamada en el workflow GHL
- Filtrar leads por recency (<48h)
- Máximo 2 intentos por número

**Owner:** Juan (workflows GHL). Stephanee lo documentó en `AUDIT_898_CALLS.md` como "MEDIO PLAZO".

### O-003 — Slots de 5 minutos en calendario Botox

**Problema:** Auditoría 898 detectó "'las doce o las doce y cinco' suena raro. Debe ser 30 min" (línea 72).

**Fix probable (NO de prompt):** cambio de configuración del calendario GHL de Botox a intervalos de 30 min.

**Owner:** Juan (GHL).

---

## Historial

| Fecha | Evento |
|-------|--------|
| 2026-04-14 | Archivo creado con P-001, P-002, P-003 + O-001, O-002, O-003 a partir de AUDIT_898_CALLS.md + primera corrida dry-run de audit_continuous.py |

---

_Mantenedor: Elena Voice · Ciclo: cada 2 semanas post-`audit_continuous.py` · Aprobador único para producción: Juan (vía Stephanee)_
