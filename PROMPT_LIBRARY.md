> ⚠️ **ARCHIVO HISTÓRICO** — Este documento NO refleja el estado actual del sistema. El documento vigente es `CLAUDE.md`. Se conserva como referencia del camino recorrido.

---

# Biblioteca de Prompts — Elena + ARIA
**Proyecto:** Voice Agent Builder — Laser Place Miami  
**Versión:** 1.0 | **Fecha:** 31 marzo 2026

> **Cómo usar este documento:** Copia el prompt de la categoría que necesitas, rellena los campos entre `[corchetes]`, y pégalo como primer mensaje en una tarea nueva de este proyecto. Cada prompt está diseñado para que el agente ejecute sin pedir aclaraciones.

---

## ÍNDICE

1. [Bugs y Fixes del Servidor (app.py)](#1-bugs-y-fixes-del-servidor-apppy)
2. [Bugs y Fixes del Prompt de Elena](#2-bugs-y-fixes-del-prompt-de-elena)
3. [Análisis de Llamadas Específicas](#3-análisis-de-llamadas-específicas)
4. [Análisis de Métricas y Rendimiento](#4-análisis-de-métricas-y-rendimiento)
5. [Modificaciones a Workflows de GHL](#5-modificaciones-a-workflows-de-ghl)
6. [Reescritura o Evolución del Prompt](#6-reescritura-o-evolución-del-prompt)
7. [Construcción de Nuevos Agentes](#7-construcción-de-nuevos-agentes)
8. [ARIA — Auditoría y Calibración](#8-aria--auditoría-y-calibración)
9. [Supabase — Consultas y Análisis de Datos](#9-supabase--consultas-y-análisis-de-datos)
10. [Deploy y Verificación de Sistemas](#10-deploy-y-verificación-de-sistemas)
11. [Documentación y Handoff](#11-documentación-y-handoff)
12. [Estrategia y Decisiones de Producto](#12-estrategia-y-decisiones-de-producto)

---

## 1. Bugs y Fixes del Servidor (app.py)

### 1A — Bug con evidencia de transcript

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Diagnosticar y corregir un bug en app.py (servidor Render v17.48).

SÍNTOMA OBSERVADO:
[Describe exactamente qué pasó — ej: "Elena creó una cita con el teléfono +17860000000 
en lugar del teléfono real del cliente"]

EVIDENCIA:
- Call ID de Vapi: [019d4181-6d00-777d-a054-...]
- Transcript relevante: [pega las líneas del transcript donde ocurre el error]
- Tool call problemático: [ej: create_contact args={"phone": "+17860000000"}]

COMPORTAMIENTO ESPERADO:
[Describe qué debería haber pasado — ej: "El servidor debe usar el callerPhone real, 
no el número que Elena proporciona"]

RESTRICCIONES:
- No tocar la lógica de llamadas outbound
- Mantener compatibilidad con los 9 tools actuales
- Bump de SERVER_VERSION después del fix

ENTREGABLE: Fix en app.py + commit a GitHub + verificación de que Render desplegó 
la nueva versión.
```

### 1B — Bug sin transcript (solo síntoma)

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Investigar y corregir un comportamiento anómalo en el servidor.

SÍNTOMA: [Describe el síntoma — ej: "GHL no está recibiendo el campo elena_callback_hours 
después de las llamadas con outcome llamar_luego"]

DATOS DISPONIBLES:
- Fecha aproximada del problema: [ej: desde el 30/03/2026]
- Frecuencia: [ej: ocurre en el 100% de las llamadas llamar_luego]
- Últimas llamadas afectadas: [call IDs si los tienes, o "no tengo IDs"]

PASOS ESPERADOS:
1. Obtener los últimos 5 transcripts de llamadas con outcome llamar_luego desde Vapi
2. Verificar en el código qué escribe elena_callback_hours y bajo qué condición
3. Identificar la causa raíz con evidencia del transcript
4. Proponer y aplicar el fix
5. Verificar en GHL que el campo se escribe correctamente después del fix

RESTRICCIONES: [lo que no se puede tocar]
```

---

## 2. Bugs y Fixes del Prompt de Elena

### 2A — Bug de comportamiento conversacional con transcript

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Corregir un comportamiento incorrecto de Elena en el prompt V2.1.1.

SÍNTOMA OBSERVADO:
[Describe exactamente qué dijo o hizo Elena — ej: "Elena ofreció el SRA para labios 
cuando el cliente preguntó por lip augmentation, sin preguntar primero si también 
quería Botox"]

EVIDENCIA DEL TRANSCRIPT:
[Pega las líneas exactas del transcript donde ocurre el error]
Call ID: [ID de Vapi]

COMPORTAMIENTO ESPERADO:
[Qué debería haber dicho o hecho Elena en ese momento]

BLOQUE DEL PROMPT AFECTADO:
[Si lo sabes — ej: "Bloque 3, sección C3" o "no sé cuál bloque"]

RESTRICCIONES:
- El fix debe ser quirúrgico — máximo 4-6 líneas nuevas
- No romper la cobertura de los 86 ítems del inventario original
- No agregar capas de FIX sobre FIX — integrar la regla en la arquitectura existente
- Bump de versión del prompt después del fix (ej: V2.1.1 → V2.1.2)

ENTREGABLE: Fix en system_prompt.txt + commit a GitHub + actualización en Vapi 
via update_vapi_prompt.py
```

### 2B — Mejora proactiva del prompt (sin bug específico)

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Mejorar el prompt de Elena en un área específica sin romper lo que ya funciona.

ÁREA A MEJORAR:
[Ej: "El manejo de objeciones cuando el cliente dice que ya se hizo Botox antes y 
no le gustó el resultado"]

DATOS DE CONTEXTO:
- Frecuencia del problema: [ej: "ocurrió en 3 de las últimas 10 llamadas"]
- Transcripts de referencia: [call IDs o fragmentos del transcript]
- Resultado actual de Elena: [qué dice hoy]
- Resultado deseado: [qué debería decir]

CRITERIO DE ÉXITO:
[Cómo sabremos que el fix funciona — ej: "Elena debe hacer 2 preguntas de diagnóstico 
antes de ofrecer el SRA, no ofrecerlo directamente"]

RESTRICCIONES:
- Máximo 6 líneas nuevas en el prompt
- No duplicar instrucciones que ya existen en otro bloque
- Mantener el tono premium y no agresivo de la marca
```

---

## 3. Análisis de Llamadas Específicas

### 3A — Diagnóstico de una llamada problemática

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Auditar en detalle la llamada [CALL_ID] y determinar qué salió mal, 
por qué, y si requiere un fix.

CALL ID: [019d4181-6d00-777d-a054-...]
FECHA: [30/03/2026 ~10:05 PM EDT]
TELÉFONO DEL CLIENTE: [+17865533777]
OUTCOME REPORTADO POR ARIA: [ej: no_agendo con errores confusion_created y wrong_info]

LO QUE YO OBSERVÉ:
[Describe con tus palabras qué pasó en la llamada — ej: "Elena dijo que no había 
disponibilidad el miércoles y 30 segundos después dijo que sí había"]

PREGUNTAS ESPECÍFICAS A RESPONDER:
1. ¿Fue un bug del servidor, del prompt, o del LLM?
2. ¿El outcome clasificado es correcto?
3. ¿Requiere un fix o fue un caso edge que no justifica cambios?

ENTREGABLE: Diagnóstico con evidencia del transcript + recomendación de acción 
(fix / no fix / monitorear).
```

### 3B — Comparar dos llamadas para identificar patrón

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Comparar [N] llamadas para identificar un patrón de error recurrente.

LLAMADAS A ANALIZAR:
- Call ID 1: [ID] — Outcome: [ej: no_agendo]
- Call ID 2: [ID] — Outcome: [ej: no_agendo]
- Call ID 3: [ID] — Outcome: [ej: no_agendo]

HIPÓTESIS A VALIDAR:
[Ej: "Creo que Elena está colgando prematuramente cuando el cliente hace una pausa 
larga antes de responder"]

ENTREGABLE: 
1. Confirmación o refutación de la hipótesis con evidencia del transcript
2. Si se confirma: causa raíz exacta (servidor vs prompt vs LLM) y fix recomendado
3. Si se refuta: cuál es el patrón real
```

---

## 4. Análisis de Métricas y Rendimiento

### 4A — Reporte de conversión del período

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Generar un reporte de rendimiento de Elena para el período [FECHA INICIO] 
a [FECHA FIN].

MÉTRICAS QUE NECESITO:
1. Total de llamadas procesadas
2. Distribución de outcomes (agendo / no_agendo / llamar_luego / no_contesto / error_tecnico)
3. Tasa de contacto (llamadas >30s / total)
4. Tasa de conversión (agendo / llamadas con conversación real)
5. Duración promedio de conversaciones reales
6. Top 3 errores de playbook detectados por ARIA

FUENTES DE DATOS:
- Vapi API (llamadas y transcripts)
- Supabase call_audits (outcomes y errores ARIA)

FORMATO DE SALIDA: Tabla resumen + 2-3 observaciones accionables.
No quiero teoría — quiero números reales y qué hacer con ellos.
```

### 4B — Identificar las llamadas con mayor potencial de recuperación

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: De las últimas [N] llamadas con outcome no_agendo, identificar cuáles 
tenían potencial real de conversión y qué falló exactamente.

CRITERIO DE "POTENCIAL REAL":
- El cliente habló más de 60 segundos
- El cliente no rechazó explícitamente el servicio
- El cliente mostró alguna señal de interés (preguntó precio, disponibilidad, etc.)

ENTREGABLE:
1. Lista de call IDs que cumplen el criterio
2. Para cada uno: qué momento exacto se perdió la oportunidad
3. Si el patrón es recurrente: recomendación de fix en el prompt
```

---

## 5. Modificaciones a Workflows de GHL

### 5A — Modificar una rama existente del workflow

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Modificar la rama [NOMBRE DE LA RAMA] del workflow 
"Botox - Lead Nuevo AI (Principal)" en GHL.

ESTADO ACTUAL DE LA RAMA:
[Describe las acciones actuales — ej: "Actualmente va directo a Remove Tag → 
Remove from Workflow sin ningún seguimiento"]

COMPORTAMIENTO DESEADO:
[Describe exactamente qué debe pasar — ej: "Después de 2 horas, enviar un SMS de 
reactivación. Si el cliente responde, notificar al equipo. Si no responde en 24h, 
remover del workflow"]

COPY DEL SMS (si aplica):
[Pega el texto exacto del SMS o pide que lo genere]

RESTRICCIONES:
- No tocar las otras ramas del workflow
- Mantener el tag elena_resultado_botox como trigger
- El workflow debe seguir siendo publicable sin errores

ENTREGABLE: Instrucciones paso a paso para implementar en GHL + screenshot de 
verificación si es posible.
```

### 5B — Crear un nuevo workflow desde cero

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Diseñar e implementar un nuevo workflow en GHL para [PROPÓSITO].

TRIGGER:
[Qué evento dispara el workflow — ej: "Tag botox_agendo_no_show agregado al contacto"]

ACTORES INVOLUCRADOS:
[Ej: "El contacto que no se presentó a su cita, el equipo de Laser Place Miami"]

FLUJO DESEADO:
[Describe el proceso en lenguaje natural — ej: "Si el cliente no se presentó, 
esperar 30 minutos, enviar SMS de reagendamiento, si responde notificar al equipo, 
si no responde en 24h marcar como perdido"]

CAMPOS CUSTOM DISPONIBLES:
[Lista los campos elena_* relevantes que el workflow puede usar como condiciones]

ENTREGABLE: Diagrama del workflow + instrucciones paso a paso para construirlo en GHL.
```

---

## 6. Reescritura o Evolución del Prompt

### 6A — Auditoría completa del prompt actual

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Auditar el prompt actual (V2.1.1, system_prompt.txt en GitHub) y determinar 
si necesita una evolución estructural o solo fixes puntuales.

DATOS DE CONTEXTO:
- Llamadas analizadas en el período: [N]
- Errores HIGH más frecuentes según ARIA: [lista — ej: premature_endcall x5, missed_close x4]
- Tasa de conversión actual: [X%]
- Tasa de conversión objetivo: [Y%]

TRANSCRIPTS DE REFERENCIA (llamadas problemáticas):
- Call ID 1: [ID] — Problema: [descripción]
- Call ID 2: [ID] — Problema: [descripción]

CRITERIO DE DECISIÓN:
- Si los errores son de estructura → reescritura de bloque
- Si los errores son de regla faltante → fix puntual de 4-6 líneas
- Si los errores son del LLM (no del prompt) → no tocar el prompt

ENTREGABLE: Diagnóstico con recomendación de acción + si se recomienda cambio, 
el cambio exacto con justificación.
```

### 6B — Reescritura de un bloque específico

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Reescribir el [BLOQUE X] del prompt V2.1.1 para mejorar [COMPORTAMIENTO ESPECÍFICO].

BLOQUE A REESCRIBIR: [Ej: Bloque 3 — Objection Overrides, sección de experiencia negativa previa]

PROBLEMA ACTUAL:
[Qué está fallando en ese bloque — con evidencia de transcript si tienes]

COMPORTAMIENTO OBJETIVO:
[Qué debe hacer Elena exactamente en ese escenario]

RESTRICCIONES:
- Mantener la arquitectura modular de 4 bloques
- No superar [N] líneas adicionales en el bloque
- El bloque reescrito debe pasar el inventario de 86 ítems
- No duplicar instrucciones de otros bloques

ENTREGABLE: Bloque reescrito listo para reemplazar en system_prompt.txt + 
justificación de cada cambio.
```

---

## 7. Construcción de Nuevos Agentes

### 7A — Clonar Elena para un nuevo tratamiento

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Crear un segundo agente de voz para [TRATAMIENTO] basado en la 
arquitectura de Elena V2.1.1.

TRATAMIENTO: [Ej: Fillers labiales / Depilación láser / Xeomin]
NOMBRE DEL AGENTE: [Ej: "Sofia" / "Valeria"]

DIFERENCIAS CON ELENA (BOTOX):
- Objeciones típicas del cliente: [lista]
- Proceso de booking: [¿mismo calendario que Laury? ¿misma doctora? ¿mismo horario?]
- Preguntas de calificación: [qué preguntar en el STATE 2]
- Precio aproximado o rango: [para manejar objeción de precio]
- Contraindicaciones clave: [para no agendar a quien no aplica]

INFRAESTRUCTURA:
- ¿Mismo servidor Render o nuevo servicio? [mismo / nuevo]
- ¿Mismo calendario GHL o nuevo? [mismo ID / nuevo — si nuevo, cuál es el ID]
- ¿Mismo workflow GHL o nuevo? [mismo / nuevo]

ENTREGABLE: 
1. Nuevo system_prompt_[tratamiento].txt basado en V2.1.1
2. Config del nuevo asistente en Vapi
3. Instrucciones para el deploy
4. Checklist de verificación antes de ir a producción
```

### 7B — Escalar a múltiples agentes (arquitectura)

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Diseñar la arquitectura para escalar de 1 agente (Elena/Botox) a 
[N] agentes para diferentes tratamientos de Laser Place Miami.

AGENTES PLANIFICADOS:
1. Elena — Botox (activo)
2. [Nombre] — [Tratamiento 2]
3. [Nombre] — [Tratamiento 3]

PREGUNTAS A RESOLVER:
1. ¿Un servidor Render o uno por agente?
2. ¿Una instancia de ARIA o una por agente?
3. ¿Un repo GitHub o uno por agente?
4. ¿Cómo se comparte la lógica común (GHL API, Supabase, outcomes)?

RESTRICCIONES:
- Presupuesto de infraestructura: [ej: mínimo posible / no importa]
- Tiempo de implementación: [ej: quiero el segundo agente en 2 semanas]
- Complejidad de mantenimiento: [prefiero simplicidad sobre optimización]

ENTREGABLE: Arquitectura recomendada con diagrama + plan de migración desde 
el monorepo actual.
```

---

## 8. ARIA — Auditoría y Calibración

### 8A — Calibrar ARIA con feedback real

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Calibrar el sistema de auditoría ARIA con los últimos [N] feedbacks 
que he aprobado/rechazado en Telegram.

CONTEXTO:
- He aprobado [N] correcciones de ARIA en los últimos [X] días
- He rechazado [N] correcciones (falsos positivos)
- El error más frecuente que ARIA está cometiendo: [ej: "marca como confusion_created 
  llamadas donde Elena en realidad manejó bien la situación"]

OBJETIVO DE LA CALIBRACIÓN:
[Ej: "Reducir los falsos positivos de confusion_created" / "Hacer que ARIA sea más 
estricta con premature_endcall"]

ENTREGABLE: Ajuste en el prompt de Claude en aria_audit.py + verificación con 
las últimas 5 llamadas auditadas.
```

### 8B — Agregar un nuevo tipo de error a ARIA

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Agregar un nuevo tipo de error al rubric de auditoría de ARIA.

NUEVO ERROR A DETECTAR:
- Nombre: [ej: wrong_slot_offered]
- Definición: [ej: "Elena ofreció un slot que no estaba en los resultados de 
  check_availability"]
- Severidad: [HIGH / MEDIUM / LOW]
- Evidencia en transcript: [cómo se ve este error en el transcript]
- Ejemplo real: [Call ID donde ocurrió, si tienes]

ENTREGABLE: Modificación en aria_audit.py + prueba con 3 llamadas históricas 
para verificar que ARIA detecta el error correctamente.
```

### 8C — Reporte de rendimiento de ARIA

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Evaluar qué tan bien está funcionando ARIA como sistema de auditoría.

PERÍODO: [FECHA INICIO] a [FECHA FIN]

MÉTRICAS QUE NECESITO:
1. Total de llamadas auditadas
2. Distribución de errores detectados por tipo y severidad
3. Tasa de falsos positivos (correcciones que rechacé)
4. Tasa de falsos negativos (errores que ARIA no detectó pero yo sí vi)
5. Confianza promedio de ARIA en sus diagnósticos

FUENTE: Supabase — tablas call_audits, aria_corrections, feedback_log

ENTREGABLE: Tabla de métricas + recomendación de si ARIA necesita recalibración.
```

---

## 9. Supabase — Consultas y Análisis de Datos

### 9A — Consulta específica de datos históricos

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Extraer y analizar datos específicos de Supabase.

PREGUNTA DE NEGOCIO:
[Ej: "¿Cuántos leads que dijeron llamar_luego terminaron agendando en los 
siguientes 5 días?"]

TABLAS RELEVANTES:
[Si lo sabes — ej: call_audits / aria_corrections / feedback_log]

PERÍODO: [FECHA INICIO] a [FECHA FIN]

FORMATO DE SALIDA: [Tabla / número único / comparativa / gráfico]

ENTREGABLE: Query SQL + resultado + interpretación en 2-3 líneas.
```

---

## 10. Deploy y Verificación de Sistemas

### 10A — Verificación de estado de todos los sistemas

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Verificar que todos los sistemas de Elena + ARIA están funcionando 
correctamente.

CONTEXTO:
[Ej: "Acabo de hacer un cambio en app.py y quiero confirmar que el deploy fue exitoso" 
/ "Elena no respondió una llamada hace 30 minutos y quiero saber si hay algo caído"]

SISTEMAS A VERIFICAR:
- Render: que el servidor esté healthy y en la versión correcta
- Vapi: que el prompt esté actualizado y los tools estén activos
- GitHub: que el último commit esté en main
- GHL: que el workflow esté publicado (no en draft)
- Supabase: que ARIA pueda escribir en call_audits

ENTREGABLE: Estado de cada sistema con versión/commit actual + cualquier anomalía detectada.
```

### 10B — Deploy de emergencia

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

URGENTE: Elena no está funcionando correctamente.

SÍNTOMA: [Describe exactamente qué está pasando — ej: "Las llamadas entran pero 
Elena no responde / El servidor devuelve 500 / Las citas no se están creando en GHL"]

ÚLTIMA VEZ QUE FUNCIONÓ BIEN: [Fecha/hora aproximada]
ÚLTIMO CAMBIO HECHO: [Qué se modificó antes de que empezara el problema]

ACCIÓN ESPERADA:
1. Diagnosticar la causa raíz
2. Aplicar el fix mínimo necesario para restaurar el servicio
3. Verificar que el servicio está restaurado
4. Documentar qué pasó y por qué

RESTRICCIONES: Velocidad sobre perfección — necesito el sistema funcionando lo antes posible.
```

---

## 11. Documentación y Handoff

### 11A — Actualizar el HANDOFF_MAESTRO

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Actualizar el HANDOFF_MAESTRO.md con los cambios recientes del sistema.

CAMBIOS A DOCUMENTAR:
1. [Ej: "Se agregó el agente Sofia para fillers — Vapi ID: XXXX, prompt en agents/fillers/"]
2. [Ej: "SERVER_VERSION bumpeado a v17.49 con fix de X"]
3. [Ej: "Nuevo workflow de GHL para no-shows creado"]

SECCIONES AFECTADAS:
[Ej: Sección 3 (Credenciales), Sección 5 (Elena), Sección 10 (Historial de versiones)]

ENTREGABLE: HANDOFF_MAESTRO.md actualizado + commit a GitHub.
```

---

## 12. Estrategia y Decisiones de Producto

### 12A — Decisión de arquitectura

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Ayudarme a tomar una decisión de arquitectura o producto.

DECISIÓN A TOMAR:
[Ej: "¿Debo crear un segundo agente para fillers ahora o esperar a que Elena tenga 
más llamadas estables?"]

CONTEXTO RELEVANTE:
- Estado actual del sistema: [ej: Elena lleva 15 llamadas post-V2.1.1 sin bugs críticos]
- Recursos disponibles: [tiempo, presupuesto, capacidad de monitoreo]
- Objetivo de negocio: [ej: quiero tener 3 agentes activos para junio]

LO QUE NECESITO:
- Evaluación honesta de riesgos y beneficios de cada opción
- Una recomendación clara con justificación
- Los próximos 3 pasos si decido proceder

FORMATO: Contexto rápido → opciones con pros/contras → recomendación → próximos pasos.
No quiero teoría — quiero una decisión accionable.
```

### 12B — Análisis de conversión y oportunidades de mejora

```
Contexto: Sistema Elena + ARIA. Antes de actuar, lee el HANDOFF_MAESTRO.md del repo 
stephaneecerebromaestro/elena en GitHub.

OBJETIVO: Identificar las palancas de mayor impacto para mejorar la tasa de 
conversión de Elena de [X%] a [Y%].

DATOS ACTUALES:
- Tasa de contacto: [X%]
- Tasa de conversión (conversación → cita): [X%]
- Outcome más frecuente: [ej: no_contesto 64%, no_agendo 20%, agendo 7%]
- Error más frecuente de ARIA: [ej: missed_close 10x en las últimas 50 llamadas]

RESTRICCIONES:
- No quiero cambios que requieran más de [N] horas de implementación
- No quiero tocar [componente X] en este momento
- Presupuesto adicional disponible: [sí / no]

ENTREGABLE: Top 3 palancas priorizadas por impacto/esfuerzo + plan de implementación 
para cada una.
```

---

## GUÍA DE USO RÁPIDO

| Si necesitas... | Usa el prompt... |
|-----------------|-----------------|
| Arreglar algo que el servidor hace mal | 1A o 1B |
| Arreglar algo que Elena dice mal | 2A o 2B |
| Entender por qué falló una llamada específica | 3A |
| Ver métricas del período | 4A |
| Recuperar leads perdidos | 4B |
| Cambiar el flujo de SMS en GHL | 5A |
| Crear un nuevo workflow de GHL | 5B |
| Auditar si el prompt necesita evolución | 6A |
| Reescribir un bloque del prompt | 6B |
| Lanzar un agente para otro tratamiento | 7A |
| Planificar la arquitectura multi-agente | 7B |
| Calibrar ARIA con tu feedback | 8A |
| Agregar un nuevo error a ARIA | 8B |
| Reporte de rendimiento de ARIA | 8C |
| Consultar datos históricos de Supabase | 9A |
| Verificar que todo está funcionando | 10A |
| Emergencia — algo está caído | 10B |
| Actualizar el HANDOFF_MAESTRO | 11A |
| Tomar una decisión de arquitectura | 12A |
| Mejorar la tasa de conversión | 12B |

---

*Documento generado el 31 marzo 2026. Para agregar nuevos prompts, seguir el mismo formato y hacer commit al repo stephaneecerebromaestro/elena.*
