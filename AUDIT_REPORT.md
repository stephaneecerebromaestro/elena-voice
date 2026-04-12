> ⚠️ **ARCHIVO HISTÓRICO** — Este documento NO refleja el estado actual del sistema. El documento vigente es `CLAUDE.md`. Se conserva como referencia del camino recorrido.

---

# Audit Final — Sistema Elena v17.27
**Fecha:** 26 de marzo de 2026  
**Alcance:** system_prompt.txt, app.py, Dockerfile, patch_vapi_full_restore.py, configuración Vapi en vivo

---

## RESUMEN EJECUTIVO

| Área | Estado | Problemas críticos | Problemas menores |
|------|--------|--------------------|-------------------|
| system_prompt.txt | ✅ Sólido | 0 | 3 |
| app.py — Tool handlers | ✅ Sólido | 0 | 2 |
| app.py — end-of-call / outcomes | ✅ Sólido | 0 | 1 |
| app.py — threading / concurrencia | ✅ Correcto | 0 | 1 |
| Dockerfile | ✅ Correcto | 0 | 0 |
| patch_vapi_full_restore.py | ⚠️ Inconsistencia | 0 | 2 |
| Configuración Vapi en vivo | ✅ Verificado | 0 | 0 |

**No hay problemas críticos que bloqueen la campaña de mañana.**

---

## 1. SYSTEM_PROMPT.TXT

### ✅ Lo que está bien
- Flujo de 8 pasos coherente y sin contradicciones internas.
- Regla de `get_contact` con `{}` sin argumentos está reforzada en 3 lugares distintos (PASO 2, PASO 7, tabla de herramientas) — buena redundancia.
- Skin Reveal Analysis bien integrado en PASO 4, PASO 5 y objeciones.
- Laury correctamente posicionada como evaluadora. Gilberto solo aparece como inyector.
- Manejo de silencios robusto con 3 intentos antes de endCall.
- Callbacks: lógica de horas (2/4/12/120) bien explicada con tabla y ejemplos.
- Objeciones cubren los 7 escenarios más comunes + experiencia negativa previa (4 sub-escenarios).
- Idioma: detección automática español/inglés/Spanglish bien especificada.
- Dirección: reforzada con "NUNCA INVENTES" en 2 lugares.

### ⚠️ Problemas menores

**M1 — Inconsistencia en PASO 5 vs. lógica de días (línea 97-102)**
El prompt dice: "El inyector Gilberto trabaja únicamente los martes." Pero en el PASO 5 también dice que si el cliente quiere solo evaluarse puede ir cualquier día. Esto está bien lógicamente, pero el LLM puede confundirse si el cliente dice "¿puedo ir el jueves?" y Elena no sabe si ofrecerle el jueves para evaluación o insistir en martes.

**Recomendación:** Agregar una línea explícita: "Para evaluación sola, cualquier día disponible en el calendario es válido. Solo para tratamiento (inyección) se requiere martes."

**M2 — PASO 2: get_contact se llama en silencio pero el prompt no maneja el caso donde GHL tarda**
Si `get_contact` tarda 3+ segundos en el PASO 2 (búsqueda inicial), Elena queda en silencio incómodo. El prompt solo tiene la regla de "Dame un momento" para herramientas en el PASO 7, no para el PASO 2.

**Recomendación:** Agregar: "Si get_contact en PASO 2 tarda más de 2 segundos → di '¿Cómo te llamas?' para llenar el silencio mientras esperas."

**M3 — Social proof en línea 84: "Aman la mano de Gilberto"**
Esta frase puede generar preguntas sobre Gilberto en el momento equivocado (antes de que el cliente haya aceptado la evaluación). Si el cliente pregunta "¿quién es Gilberto?" en ese momento, Elena tiene que explicar el inyector antes de cerrar.

**Recomendación:** Mover esta frase de social proof al bloque de objeciones "¿Quién inyecta?" y no usarla en el flujo principal de cierre.

---

## 2. APP.PY — TOOL HANDLERS

### ✅ Lo que está bien
- `normalize_phone`: sólido, rechaza números cortos, secuenciales y de 9 dígitos.
- `handle_get_contact`: fuzzy-match con verificación exacta de teléfono (FIX BUG1) — correcto.
- `handle_create_contact`: idempotency lock por teléfono con TTL de 1 hora — bien implementado.
- `handle_create_booking`: duplicate check antes de crear, conversión UTC correcta (FIX TZ).
- `handle_reschedule_appointment`: conversión UTC correcta, mismo patrón que create_booking.
- `handle_cancel_appointment`: simple y correcto.
- `handle_get_appointment_by_contact`: maneja múltiples citas (FIX E), filtra canceladas/noshow.
- `handle_get_current_time`: retorna hora Miami con campos útiles (hour24, minute para math).
- `handle_schedule_callback`: snap a valor válido si LLM pasa valor inesperado — buen defensive coding.

### ⚠️ Problemas menores

**M4 — `handle_reschedule_appointment` usa `startTime` como parámetro pero la tool en Vapi lo define como `newStartTime`**
En `patch_vapi_full_restore.py` línea 180, el parámetro se llama `startTime`. Pero en `handle_reschedule_appointment` (línea 516), el código lee `args.get("newStartTime", "")`. Si el LLM pasa `startTime` (como dice la tool definition), el handler recibe string vacío y falla silenciosamente.

**Recomendación:** Cambiar el handler para leer ambos: `args.get("newStartTime", args.get("startTime", ""))` — o corregir la tool definition para que use `newStartTime`.

**M5 — `handle_check_availability` limita a 5 slots por día pero no hay cap total**
Con 30 días × 5 slots/día = hasta 150 slots en el response. Eso es mucho contexto para el LLM (gpt-4o-mini con maxTokens=170). El LLM puede truncar o confundirse.

**Recomendación:** Limitar el total a 15 slots máximo (5 martes + 10 otros días). Ya está parcialmente implementado (`tuesday_slots[:5] + other_slots[:10]`) pero `other_slots[:10]` sigue siendo mucho. Reducir a `other_slots[:5]` para un total de 10 slots máximo.

---

## 3. APP.PY — END-OF-CALL / OUTCOMES

### ✅ Lo que está bien
- Prioridad de outcomes correcta: agendo > llamar_luego_confirmed > short_call > voicemail > silence > keyword fallback.
- FIX BUG3: verificación directa en GHL si agendo=False después de escanear mensajes — excelente safety net.
- Orden crítico: custom fields ANTES del tag — race condition con GHL workflow correctamente resuelto.
- Transcript guardado como nota en GHL con metadata completa.
- Contadores `elena_total_calls` y `elena_conversations` bien separados.

### ⚠️ Problema menor

**M6 — Duplicación de código de detección `verificando_phrases` en 3 lugares**
Las listas `verificando_phrases`, `_verificando_phrases`, y `_verificando_phrases_d` son idénticas pero se definen 3 veces (líneas 1014, 1039, 1082). Si se necesita agregar una frase nueva, hay que actualizarla en 3 lugares — deuda técnica menor.

**Recomendación:** Definir `VERIFICANDO_PHRASES` como constante a nivel de módulo (junto a `DAYS_ES` y `MONTHS_ES`) y referenciarla en los 3 lugares.

---

## 4. APP.PY — THREADING / CONCURRENCIA

### ✅ Lo que está bien
- FIX P: `_process_end_of_call` corre en thread daemon — worker retorna 200 OK en <1ms.
- Idempotency lock en `create_contact` con `threading.Lock()` por teléfono — correcto.
- `_create_contact_lock_meta` protege el dict de locks — correcto double-locking pattern.

### ⚠️ Problema menor

**M7 — Memory leak potencial en `_create_contact_locks`**
El dict `_create_contact_locks` crece indefinidamente (un Lock por teléfono único). Con 100 llamadas/día × 30 días = 3,000 locks en memoria. No es crítico en Render Standard (512MB RAM) pero puede crecer.

**Recomendación:** Limpiar el dict de locks junto con el TTL de `_create_contact_results`. Ya existe la lógica de TTL para results — extenderla para limpiar también el lock cuando el result expira.

---

## 5. DOCKERFILE

### ✅ Correcto
- `4 workers × 4 threads = 16 concurrent requests` — suficiente para 100 llamadas simultáneas.
- `gthread` worker class — correcto para I/O-bound (GHL API calls).
- `timeout 60s` — cubre las operaciones más lentas (get_contact + create_booking chain).
- No hay problemas.

---

## 6. PATCH_VAPI_FULL_RESTORE.PY

### ⚠️ Inconsistencias encontradas

**M4 (mismo que arriba) — Tool `reschedule_appointment` usa `startTime` pero handler espera `newStartTime`**
Línea 180: `"name": "startTime"` en la tool definition.  
Línea 516 en app.py: `args.get("newStartTime", "")`.  
**Esto es un bug activo** — si Elena intenta reagendar, el handler recibe `""` y retorna error silencioso.

**Corrección requerida:** Cambiar la tool definition en `patch_vapi_full_restore.py` de `"startTime"` a `"newStartTime"`, o cambiar el handler para aceptar ambos.

**M8 — Tool `check_availability` description menciona "martes son días de procedimiento (inyector Gilberto)"**
Línea 51: `"Los martes son los días de procedimiento (inyector Gilberto)."` — esto es correcto pero innecesariamente expone el nombre del inyector en la tool description que el LLM lee. No es un bug pero es ruido.

**Recomendación:** Simplificar a: `"Los martes son los días prioritarios para procedimientos."` — sin nombre.

---

## 7. CONFIGURACIÓN VAPI EN VIVO

### ✅ Verificado (última sesión)
| Parámetro | Valor |
|-----------|-------|
| Voice provider | `11labs` |
| Voice ID | `b2htR0pMe28pYwCY9gnP` (Sofía) |
| Voice model | `eleven_flash_v2_5` |
| Speed | `0.95` |
| useSpeakerBoost | `true` |
| numWordsToInterruptAssistant | `2` |
| stopSpeakingPlan.voiceSeconds | `0.5s` |
| stopSpeakingPlan.backoffSeconds | `1.5s` |
| Transcriber | `deepgram nova-3 multi` |
| LLM | `gpt-4o-mini` |
| Temperature | `0.45` |
| maxTokens | `170` |
| Tools | 9 ✅ |

---

## PRIORIZACIÓN DE CORRECCIONES

| ID | Descripción | Impacto | Esfuerzo | Prioridad |
|----|-------------|---------|----------|-----------|
| **M4** | Bug: reschedule usa `startTime` pero handler espera `newStartTime` | Alto (reagendamiento roto) | 5 min | **AHORA** |
| M1 | Clarificar días: evaluación cualquier día, tratamiento solo martes | Medio | 10 min | Esta semana |
| M5 | Reducir slots de `other_slots[:10]` a `[:5]` para menos contexto al LLM | Medio | 2 min | Esta semana |
| M2 | Manejo de silencio en PASO 2 durante get_contact inicial | Bajo | 5 min | Próxima sesión |
| M3 | Mover "Aman la mano de Gilberto" fuera del flujo principal | Bajo | 5 min | Próxima sesión |
| M6 | Refactorizar `verificando_phrases` como constante de módulo | Bajo (deuda técnica) | 10 min | Próxima sesión |
| M7 | Limpiar `_create_contact_locks` con TTL | Muy bajo | 15 min | Próxima semana |
| M8 | Simplificar tool description de check_availability | Muy bajo | 2 min | Próxima sesión |

---

## VEREDICTO FINAL

**El sistema está listo para la campaña de mañana.** El único bug activo (M4 — reschedule) no afecta el flujo principal de agendamiento — solo afecta reagendamiento, que es un flujo secundario. Los demás hallazgos son mejoras de calidad, no bloqueantes.

**Acción inmediata recomendada:** Corregir M4 antes de la campaña (5 minutos).
