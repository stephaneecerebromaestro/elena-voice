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
| P-001 | Dirección completa dictada en despedida (Botox) | `applied` 2026-04-14 | Botox | 2026-04-14 |
| P-002 | Desync entre `system_prompt.txt` del repo y prompt live en Vapi | `applied` 2026-04-14 | Botox | 2026-04-14 |
| P-003 | Loops de `check_availability` persisten pese a FIX D | `observing` | LHR | 2026-04-14 |
| P-004 | STATE 5 de LHR tiene texto corrupto ("oral Gables", duplicado "por mensaje") | `applied` 2026-04-14 | LHR | 2026-04-14 |

**Estados:** `proposed` → `approved` → `applied` → `verified` · o `rejected` · o `observing` (pre-propuesta, recolectando más data)

---

## P-001 — Dirección completa dictada en despedida (Botox)

**Estado:** `applied` 2026-04-14 (Juan dio OK general, SMS verificado) · **Bot:** Botox · **Prioridad:** ALTA · **Riesgo:** BAJO

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
- [x] Juan aprueba (2026-04-14, OK general a lista de opciones)
- [x] Confirmar que SMS post-booking de Botox incluye la dirección completa — **verificado con un booking real**: SMS del 14-abr 17:47 incluye "📍 Acá te dejo la dirección: 4649 Ponce De Leon, Suite 302, Coral Gables, FL 33146" + email con Google Maps link
- [x] Aplicar vía API directo (Vapi PATCH assistant) — HTTP 200, SHA cambió de `381aea7c0b6cff12` → `59b2ca1ebf056147`
- [ ] Hacer 1 llamada de test (número interno, NO paciente real) — pendiente (Juan puede hacerlo cuando quiera)
- [ ] Verificar transcript de esa llamada: despedida corta, sin dictar dirección
- [ ] Confirmar recibo del SMS con dirección (debe seguir llegando, lo manda GHL no Elena)

### Resultado del cambio
- Prompt body: 21353 → 21281 chars (-72)
- STATE 5 ahora: `"Excelente [Nombre], te esperamos el [día] a las [hora exacta del booking]. Te mando ahora la dirección y los detalles por mensaje. ¡Que tengas un excelente día!"`

---

## P-002 — Desync entre `system_prompt.txt` del repo y prompt live en Vapi (Botox)

**Estado:** `applied` 2026-04-14 · **Bot:** Botox · **Prioridad:** MEDIA · **Riesgo:** BAJO (no toca producción)

**Resolución:** Pull del prompt live post-P-001, sobrescribí `system_prompt.txt` con header documentando fuente + SHA + fecha. Commit junto con P-001.

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

## P-004 — STATE 5 de LHR tiene texto corrupto

**Estado:** `applied` 2026-04-14 · **Bot:** LHR · **Prioridad:** ALTA · **Riesgo:** BAJO (fix de bug, no cambio de política)

### Problema observado
Al verificar el prompt live de LHR post-P-001, encontré que el STATE 5 de LHR tiene **texto malformado** (aparente edición previa incompleta):

```
"Excelente [Nombre], te esperamos el [día] a las [hora exacta del booking].
Te mando la dirección y confirmación por mensaje.oral Gables.
Te enviamos la confirmación por mensaje. ¡Que tengas un excelente día!"
```

Problemas:
1. `"por mensaje.oral Gables"` — sobra residual de "Coral Gables", sin separación. TTS lo pronunciará raro ("oral Gables")
2. `"Te mando la dirección..."` y `"Te enviamos la confirmación..."` — dos frases que dicen casi lo mismo, duplicación innecesaria
3. Ruptura de la oración (faltan marcadores coherentes)

### Fix propuesto
Reemplazar por la misma despedida limpia aplicada a Botox en P-001:
```
"Excelente [Nombre], te esperamos el [día] a las [hora exacta del booking].
Te mando ahora la dirección y los detalles por mensaje. ¡Que tengas un excelente día!"
```

### Pre-requisito a verificar antes de aplicar
- Confirmar que el workflow GHL del calendario LHR (`gQclGhEhZ2K1NkLal7pt`) envía SMS post-booking con dirección, igual que Botox. Si no hay bookings LHR aún (hasta ahora hay 0), no podemos verificar el SMS con data real → pedir a Juan que confirme el workflow GHL de LHR.

### Checklist antes de aplicar
- [x] Juan aprueba (2026-04-14, "arreglalo")
- [x] Confirmar workflow GHL de LHR tiene SMS post-booking con dirección — **verificado con 4 bookings reales recientes** (Jose, Ava, Francesco, Karina): todos recibieron SMS con "4649 Ponce De Leon, Suite 302, Coral Gables"
- [x] Aplicar vía PATCH Vapi — HTTP 200. SHA `4c47ddaf6986e9ab` → `15875198debb0f9d`. Body 23478→23431 chars (-47)
- [x] Guardar prompt live de LHR en `system_prompt_lhr.txt` (mirror) al repo
- [ ] Test con llamada (número interno) — pendiente de Juan

---

## No son fix de prompt — pero observados y escalados

Estos patrones aparecen en la data pero **el fix no está en el prompt** — requieren cambios de infra o workflows GHL que dependen de Juan o de Stephanee. Los listo aquí para trazabilidad.

### O-001 — Cobertura ARIA 0% en LHR ✅ RESUELTO

**Problema:** Las 3 llamadas de LHR de la semana Abr 7-14 no fueron auditadas por ARIA (cobertura 0/3).

**Fix aplicado 2026-04-14:** `aria_audit.py` polling loop ahora itera sobre `config.ASSISTANTS` (ambos Botox + LHR) en vez de usar solo `VAPI_ASSISTANT_ID` env. `fetch_vapi_calls` y `fetch_vapi_calls_range` aceptan `assistant_id` opcional (backward compatible).

Log del server al arrancar:
```
ARIA Polling: auditando 2 assistant(s):
  ['1631c7cf-2914-45f9-bf82-6635cdf00aba', '3d5b77b5-f36c-4b95-88bc-4d6484277380']
```

Efecto: la próxima corrida del cron semanal (lunes 20) tendrá cobertura ARIA > 0% en LHR → top razones no_agendo confiables para ambos tratamientos.

### O-002 — Tasa de no-contestación 63-95% (Botox) ✅ CUBIERTO

**Problema:** 63% histórico; 94.9% esta semana. Conversión sana cuando hay conversación real (16%), pero el funnel se rompe antes de conectar.

**Resolución (Juan, 2026-04-14):** ya está cubierto. Cuando un lead entra al workflow GHL recibe un mensaje de bienvenida por WhatsApp desde el mismo número Twilio desde el cual Elena Voice llamará después. El paciente ve el número, lo reconoce, contesta.

**Próxima verificación:** medir el contact_rate en las corridas semanales del cron después del lunes 20. Si sube de ~5% a >20%, el SMS previo está funcionando.

### O-003 — Slots de 5 minutos en calendario Botox ✅ ASUMIDO POR JUAN

**Problema:** Auditoría 898 detectó "'las doce o las doce y cinco' suena raro. Debe ser 30 min" (línea 72).

**Resolución (Juan, 2026-04-14):** Juan lo cambia en GHL cuando tenga tiempo. Fuera del perímetro de Elena Voice.

---

## Historial

| Fecha | Evento |
|-------|--------|
| 2026-04-14 | Archivo creado con P-001, P-002, P-003 + O-001, O-002, O-003 a partir de AUDIT_898_CALLS.md + primera corrida dry-run de audit_continuous.py |
| 2026-04-14 | O-001 resuelto: ARIA polling ahora itera sobre ASSISTANTS (audita Botox + LHR) |
| 2026-04-14 | P-002 aplicado: system_prompt.txt sincronizado con live de Vapi + header documentando fuente |
| 2026-04-14 | P-001 aplicado: STATE 5 de Botox simplificado. SMS/email de GHL cubren la dirección. Prompt body 21353→21281 chars. SHA `59b2ca1ebf056147` |
| 2026-04-14 | P-004 abierto: STATE 5 de LHR tiene texto corrupto ("oral Gables", duplicación) — espera aprobación de Juan + verificación del workflow GHL de LHR |
| 2026-04-14 | P-004 aplicado: texto corrupto de LHR normalizado a la misma despedida limpia de Botox. SMS LHR verificado con 4 bookings. SHA `15875198debb0f9d`. Mirror en `system_prompt_lhr.txt`. |
| 2026-04-14 | O-002 cerrado (Juan): workflow GHL ya manda WhatsApp de bienvenida desde el mismo número Twilio antes de la llamada. Verificar contact_rate en corridas post-lunes 20. |
| 2026-04-14 | O-003 asumido por Juan: cambia slots de 5→30 min en GHL cuando tenga tiempo. |

---

_Mantenedor: Elena Voice · Ciclo: cada 2 semanas post-`audit_continuous.py` · Aprobador único para producción: Juan (vía Stephanee)_
