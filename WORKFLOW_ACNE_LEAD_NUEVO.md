# Workflow GHL: Elena Voice Acné — Lead Nuevo
# ════════════════════════════════════════════════════════════
# Propósito de este documento:
# Material de ENTRENAMIENTO para Elena Voice — descripción nodo
# por nodo del workflow GHL que orquesta a Elena Voice Acné.
# Cada entrada: QUÉ hace, POR QUÉ existe, configuración EXACTA,
# notas operativas de Juan, cómo replicar en otro tratamiento.
#
# Workflow modelo: clonado de `Elena Voice Botox - Lead Nuevo`
# Workflow ID GHL: 223669ea-690f-4261-9fde-d9ba68772281
# URL: app.gohighlevel.com/location/hzRj7DV9erP8tnPiTv7D/workflow/223669ea-690f-4261-9fde-d9ba68772281
# Estado: Draft (en construcción · 2026-04-17)
# ════════════════════════════════════════════════════════════

## ⚠️ DECISIÓN ARQUITECTÓNICA CRÍTICA (2026-04-18)

**Plan original de Mejora 5 (mensaje WA contextual generado por LLM en cada
nodo post-llamada): RETIRADO** del workflow Lead Nuevo.

**Razón técnica:** Meta WhatsApp Business API NO permite enviar mensajes libres
proactivos. Solo permite:
- Templates pre-aprobados de tipo `Utility` o `Authentication` (sin contexto del
  contacto — solo variables predefinidas)
- Mensajes libres SOLO dentro de la ventana de 24h después de que el contacto
  responda a un mensaje previo

Esto aplica a TODOS los providers (GHL, Twilio, 360dialog, etc.) porque todos
pasan por la WABA architecture controlada por Meta. Templates de Marketing a
números US adicionalmente bloqueados desde abril 2025.

**Plan ACTUAL del workflow Lead Nuevo:**
1. Workflow envía templates Utility estáticos (`WA - Llamar luego`,
   `WA - No contesto N`) usando la acción nativa Send WhatsApp de GHL con su
   FROM number propio.
2. Cuando el paciente responde al WA → se abre ventana 24h → recién ahí Elena
   Chat (vía workflow Inbound separado) puede usar LLM con conversación libre.

**Lo que QUEDA preparado para futuro Mejora 5:**
- Endpoint `/webhook/ghl/followup` en Elena Chat (`elena-lhr` repo)
- Módulos `followup.py` + `followup_fallbacks.py` + auth Bearer
- Token registrado en GHL como custom value `Authorization Key`
- Env var `ELENA_CHAT_WEBHOOK_TOKEN` en Render

**Cuándo se activará Mejora 5:**
Cuando Juan cree templates Utility con variables que LLM puede rellenar
(ej: `acne_llamar_luego_2h` con variable `{{1}}` = nombre paciente). En esa
versión LLM no genera texto libre — elige template + rellena variables. Es
"Mejora 5 light" pero compatible con restricciones Meta.

═══════════════════════════════════════════════════════════════════════════════

## Visión general del flujo

Este workflow se dispara cuando entra un lead nuevo de Acné (Facebook Form)
o cuando Juan añade manualmente un contacto viejo via tag. Luego ejecuta
una secuencia de hasta N llamadas con Elena Voice Acné, gestiona outcomes
de cada llamada (`elena_last_outcome`), envía mensajes WhatsApp de
seguimiento y mueve al contacto por el pipeline GHL Acné según resultado.

GHL es la capa de orquestación. Elena Voice solo ejecuta llamadas y
escribe outcomes a custom fields. El workflow lee esos custom fields y
decide siguiente paso.

---

## INICIO — Triggers (entrada al workflow, no es nodo)

2 triggers en paralelo (OR). Cualquiera mete al contacto al flujo.

### Trigger 1.A — Facebook Lead Form Submitted
- **Page:** Laser Place Miami
- **Form:** `Matrix Pro - Acne Activo Form` _(pendiente confirmar si hay otros en la lista)_
- **Por qué:** Facebook Ads es el canal #1 de leads nuevos. Velocidad de respuesta correlaciona directamente con conversión (lead caliente convierte 3-5x mejor que lead enfriado en 24h). El trigger se configura con `Form is any of [...]` (no `Form is`) para poder sumar forms B-test después sin tocar el trigger.

### Trigger 1.B — Contact Tag Added
- **Tag:** `acne_elena_make_call`
- **Por qué:** Juan lo usa para añadir contactos viejos manualmente (reactivación de históricos, leads no-FB como referidos/walk-ins/IG DMs).

### Anti-duplicado
NO se hace en el trigger — GHL no permite condicionar triggers por estado del contacto. La protección está en el Nodo 2 (`¿Ya en proceso?`).

### Replicabilidad para otro tratamiento
Cambiar (a) Form FB al del nuevo tratamiento, (b) tag a `<tratamiento>_elena_make_call`. Lógica del trigger NO se toca — el patrón `FB Form OR Tag` aplica a todos.

---

---

## SETUP — Condition: ¿Ya en proceso?
_(Filtro de entrada anti-duplicado. NO se cuenta como Nodo numerado. Los "Nodos" del workflow son los Condition X numerados que aparecen después de cada llamada para rutear según outcome.)_

Bifurca el flujo según si el contacto ya está siendo procesado por este workflow o no. Es la primera y única protección anti-duplicado.

### Configuración
- **Action name:** `Condition - ¿Ya en proceso?`
- **Branches:**
  - **Ya En Proceso** → `Tags Includes acne_en_proceso` → END
  - **Nuevo** (None Branch — when no condition is met) → continúa al Nodo 2

### Por qué existe
Sin esta condición, un contacto que llene el form FB y luego reciba el tag manual (o viceversa) entraría 2 veces al flujo y recibiría 2 series de llamadas Elena Voice. Eso es: pérdida de credibilidad ante el paciente + costo doble en Vapi + spam.

### Por qué se hace acá y no en el trigger
GHL no permite condicionar triggers por estado del contacto. El patrón estándar es: trigger deja entrar a todos → primer Condition filtra.

### Replicabilidad para otro tratamiento
Cambiar el tag a `<tratamiento>_en_proceso`. El tag se "pone" en el Nodo 2 (Add Tag) y se "quita" al final del flujo (Remove Tag). La pareja put/quita debe ser exactamente el mismo string.

---

## ACCIÓN 1 — Add Tag

3 tags al contacto:
- `acne_en_proceso` — control de duplicado (lo lee Nodo 1, se quita al final del flujo)
- `acne_lead` — clasificación duradera (segmentación, audiencias, reportes)
- `ai_followup` — transversal a todos los tratamientos con IA, permite excluir de campañas manuales

**Replicabilidad:** `<tratamiento>_en_proceso` y `<tratamiento>_lead`. `ai_followup` no cambia.

---

## ACCIÓN 2 — Create Or Update Opportunity

Crea (o actualiza si existe) una oportunidad en el pipeline Acné. Es la representación comercial del lead — lo que aparece en el embudo de ventas.

### Configuración
- **Pipeline:** `Leads Nuevos - Acne` (`zHEfAknfdfzkqXC9seg3`)
- **Stage:** `Nuevo Lead` (`c630d861-cd7d-4266-8b50-9c980ef22feb`)
- **Opportunity name:** `ClearConfidence - {{contact.first_name}} {{contact.last_name}}`
- **Source:** `Facebook`
- **Value:** `400` (USD esperados de la consulta cerrada)
- **Status:** `open`
- **Allow opportunity to move to any previous stage:** ON
- **Allow duplicate opportunities:** OFF

### Por qué
Cada lead tiene una oportunidad asociada — sin ella, no existe en el embudo y no se mide nada. El stage inicial `Nuevo Lead` es el punto de entrada; conforme avanza el flujo (Llamada 1, Llamar Luego, Consulta Agendada, etc.), el workflow va moviendo la oportunidad por los stages del pipeline.

`ClearConfidence` es el branding interno del tratamiento de Acné — usar el branding (no el nombre médico) en el opportunity name facilita reconocimiento rápido en el pipeline.

`Allow move to previous stage = ON` porque pacientes pueden retroceder (ej: agendaron pero cancelan → vuelve a "Llamar Luego"). `Allow duplicate = OFF` para evitar oportunidades fantasma del mismo paciente.

### Replicabilidad
- Pipeline: `Leads Nuevos - <Tratamiento>`
- Opportunity name: `<NombreBranding> - {{contact.first_name}} {{contact.last_name}}`
- Value: usar el ticket promedio real del tratamiento (no inventar)

---

## ACCIÓN 3 — Internal Email

Notifica al equipo interno (Laury) que entró un lead nuevo de Acné. Visibilidad humana antes de que la IA arranque.

### Configuración
- **From Name:** `Laury`
- **From Email:** `laury@laserplacemiami.com`
- **To:** `laury@laserplacemiami.com` (Custom Email)
- **Subject:** `Nuevo Lead - Matrix Acne Activo`
- **Template:** `Matrix Acne Activo - Interno - Nuevo Lead` (creado 2025-09-16)

### Por qué
Aunque Elena Voice arranca automática, Laury debe saber que hay un lead nuevo por si quiere intervenir manualmente, hacer follow-up paralelo, o ajustar algo en GHL antes de que la primera llamada salga. Es el "loop humano" sobre el flujo automatizado.

### Replicabilidad
Crear template equivalente para cada tratamiento (`Matrix <Tratamiento> Activo - Interno - Nuevo Lead`). Subject = `Nuevo Lead - <Tratamiento>`. From/To = mismo equipo interno.

---

## ACCIÓN 4 — WA Media: Bienvenida (imagen)

Imagen before/after de paciente Acné con el tratamiento ClearConfidence.

**Por qué va antes del texto:** WhatsApp muestra primero la imagen + caption corto. La imagen captura atención visual en el preview de la notificación, antes de que el texto entre en juego. Sin imagen, el primer mensaje compite con todo el resto del feed de WhatsApp del paciente.

**Brief de la imagen (replicabilidad):** estilo amateur cellphone snapshot (no estudio), iluminación natural, paciente real o IA con look natural (sin photoshop excesivo, piel con textura real), demografía alineada al buyer persona (mujer joven LATAM 18-35 para Miami), branding "Laser Place Miami" sutil en esquina.

---

## ACCIÓN 5 — WA Bienvenida (mensaje texto)

**Mensaje:**
```
Hola {{contact.first_name}}, soy Elena de Laser Place Miami 😊

Te marco en un ratito para conversar sobre tu interés en eliminar el acné. ¿Cómo está tu piel hoy?
```

**Configuración GHL:**
- Action name: `WA - Bienvenida`
- Template: `None - Free form message`
- From phone number: `+1 954-613-6159` (número WhatsApp Acné de Laser Place Miami via Vapi)
- Enable branches: OFF

**Por qué corto y con pregunta abierta:** mensajes largos se sienten venta y no se terminan de leer. La pregunta `¿cómo está tu piel hoy?` invita a respuesta sin presionar — abre conversación natural que luego Elena Voice puede usar como contexto en su llamada.

**Por qué "te marco" en lugar de "te llamo":** registro coloquial latino, más cercano. Reduce sensación de bot/corporación.

**Replicabilidad:** cambiar tratamiento ("acné" → "<tratamiento>"), número WhatsApp y mantener estructura: saludo + anuncio de llamada + pregunta abierta empática.

---

## ACCIÓN 6 — Wait 3 min (con ventana horaria)

**Configuración GHL:**
- Wait for: `Time Delay`
- Wait: `3 minutes`
- Advance window: ON
- Resume on: `Mon, Tue, Wed, Thu, Fri, Sat` (NO Sun)
- Resume between hours: `09:30 AM – 7:45 PM` (zona horaria del location)

**Doble propósito (palabras de Juan):**
1. **Buffer post-bienvenida:** dar 3 min al paciente para que reciba el WA y eventualmente llame él mismo (el número de Elena aparece como último paso del form FB). Si llama el paciente primero, evita la llamada outbound innecesaria.
2. **Bloqueo madrugada/domingo:** Resume window evita que la primera llamada salga fuera de horario laboral o en domingo. Si el lead entra por FB el sábado a las 11 PM, la llamada espera hasta lunes 09:30 AM. Llamadas en madrugada queman el lead — el paciente bloquea el número.

**Replicabilidad:** mismo patrón aplica a todos los tratamientos. El horario `09:30 AM – 7:45 PM` y la exclusión del domingo son política operativa de Laser Place Miami, no específica de Acné.

---

## ACCIÓN 7 — Webhook: #1 Llamada 1 (dispara primera llamada Vapi)

Llama a la API de Vapi con POST para iniciar la primera llamada outbound Elena Voice Acné al paciente.

**Configuración GHL:**
- Action name: `Webhook - Llamada 1`
- Event: `CUSTOM`
- Method: `POST`
- URL: `https://api.vapi.ai/call/phone`
- Authorization: `None` (la auth va en headers, no en este selector)
- Headers:
  - `Authorization: Bearer VAPI_KEY_REDACTED_ROTATED_2026_04_24` _(Vapi API key del workspace de Laser Place)_
  - `Content-Type: application/json`
- Content-Type: `application/json`
- Raw Body:
  ```json
  {
    "assistantId": "77392648-047e-4a40-9f8a-4f125f2ed6d6",
    "customer": {
      "number": "+1{{contact.phone}}",
      "name": "{{contact.first_name}}"
    },
    "phoneNumberId": "a7fd1148-99c3-487d-827a-d0be99aabce7"
  }
  ```

**Por qué cada campo:**
- `assistantId`: identifica QUÉ assistant Vapi llama (cada tratamiento tiene su prompt + tools propios). Acné = `77392648-047e-4a40-9f8a-4f125f2ed6d6`.
- `customer.number`: el número del paciente. `+1{{contact.phone}}` asume número US sin código de país en GHL.
- `customer.name`: para que Elena salude por nombre desde el primer "hola".
- `phoneNumberId`: identifica DESDE QUÉ número Vapi sale la llamada (cada tratamiento tiene su número WhatsApp/teléfono propio para que el paciente reconozca de dónde viene). Acné sale del `+19546136159` (`phoneNumberId: a7fd1148-...`).

**Por qué `+1` hardcodeado:** GHL guarda contactos US sin código de país. Vapi exige formato E.164. Si en el futuro hay leads internacionales, esto rompe — habría que parsear.

**Replicabilidad para otro tratamiento:** cambiar SOLO 2 valores:
1. `assistantId` → el de Vapi del nuevo tratamiento (ver `/root/agents/elena-voice/config.py` → ASSISTANTS dict)
2. `phoneNumberId` → el del número Vapi del nuevo tratamiento (consultar via `GET https://api.vapi.ai/phone-number` con la Vapi key)

URL, auth, headers, estructura de body — TODO igual para todos los tratamientos.

---

## ACCIÓN 8 — Wait 15 min (post-llamada)

Tiempo de procesamiento entre el disparo de la llamada Vapi y la lectura del resultado.

**Configuración GHL:** Time Delay, 15 minutes (sin advance window).

**Por qué 15 minutos exactos (palabras de Juan):**
Es el tiempo total necesario para que (a) Elena Voice complete la llamada outbound, (b) la llamada termine y Vapi mande el end-of-call-report, (c) el `app.py` de Elena Voice procese el report y escriba `elena_last_outcome` (+ outcome label, stage, summary, etc.) al contacto en GHL via custom fields, y (d) el workflow paralelo `Elena Voice Acne - Vapi - Procesador de Llamadas` se dispare por el tag `elena_resultado_acne` y mueva al contacto al stage correcto del pipeline.

Si el wait fuera menor, el siguiente Condition leería `elena_last_outcome` antes de que esté escrito → ruteo errado a la rama `None` (ninguna condición se cumple) → escalación incorrecta.

**Replicabilidad:** 15 minutos aplica a todos los tratamientos (es función del tiempo promedio de llamada Elena Voice + procesamiento Vapi → GHL, no del tratamiento). Si en el futuro se acelera el procesamiento end-of-call, se puede reducir, pero requiere medir.

---

## NODO 1 — Condition 1 (post-llamada Llamada 1)

Bifurca el flujo según el resultado escrito por Elena Voice + el workflow Procesador en el custom field `elena_last_outcome`. Es el primer punto donde el workflow toma decisiones basadas en lo que pasó en la llamada real.

### Configuración general del Condition
- **Action name:** `Condition 1`
- **Scenario recipe:** Build Your Own
- **7 branches** leyendo `Custom field: elena_last_outcome`

### Ramas y stage destino (pipeline `Leads Nuevos - Acne`)

| Rama | Lee | Stage Acné destino |
|------|-----|---------------------|
| Sí Agendó | `agendo` | `Consulta Agendada` (`2f4ffa95-...`) |
| Llamar Luego | `llamar_luego` | `Llamar Luego` (`ebcfd7d1-...`) |
| No Contestó | `no_contesto` | `Llamada 1` (`55196fb9-...`) |
| No Agendó | `no_agendo` | `Seguimiento Humano` (`06eea70a-...`) |
| No Interesado | `no_interesado` | `No Interesado` (`6161b751-...`) |
| Error Técnico | `error_tecnico` | `Error Técnico` (`7ce479a7-...`) |
| None | (ninguna se cumple) | (sin stage change → notificación interna + reintento) |

### Por qué la condition lee `elena_last_outcome` y no un tag
Custom field es escrito por el `app.py` de Elena Voice tras cada llamada con valor inequívoco (taxonomía cerrada). Tags son aditivos y pueden mezclarse — un mismo contacto puede tener tags de varios tratamientos o estados. Custom field es la fuente de verdad por llamada.

### Rama: Llamar Luego (configurada · 2026-04-18 — final)

**Lógica de negocio:** el paciente contestó la llamada pero pidió/sugirió ser contactado más tarde. Elena Voice escribió `elena_last_outcome=llamar_luego` y `elena_callback_hours` (2 / 4 / 12 / 120 = 5 días) según lo que el paciente dijo. Esta rama mueve la oportunidad al stage `Llamar Luego`, manda WA template Utility de confirmación, espera el tiempo dinámico, y vuelve a disparar Llamada 1 (loop).

**Estructura final:**
```
[Stage - Llamar Luego]                ← cambia stage en pipeline Acné
[WA - Llamar luego]                    ← template Utility GHL nativo (heredado del clon Botox)
[Condition por horas (5 branches)]     ← lee elena_callback_hours
   ├─ 2h, 4h, 12h, 5d → Wait + Go To → Webhook Llamada 1
   └─ None → Wait default → Go To → Webhook Llamada 1
```

#### Sub-acción L.1 — Stage - Llamar Luego
- **Tipo:** Create Or Update Opportunity
- **Action name:** `Stage - Llamar Luego`
- **In Pipeline:** `Leads Nuevos - Acne`
- **In Pipeline Stage:** `Llamar Luego`
- **Allow opportunity to move to any previous stage:** ON
- **Allow duplicate opportunities:** OFF
- **Otros campos (Opportunity Name, Source, Value, Status):** vacíos — ya fueron seteados en ACCIÓN 2 al crear la oportunidad inicial. Este nodo solo cambia el stage.
- **Por qué solo stage change:** la oportunidad ya existe (creada en ACCIÓN 2), no hay que recrearla. Dejar campos vacíos = "no tocar". Solo el stage se actualiza.
- **Replicabilidad:** mismo patrón en todos los tratamientos. Cambiar `Pipeline` y `Stage` al equivalente del nuevo tratamiento.

#### Sub-acción L.2 — WA - Llamar luego (template Utility, heredado del clon)

**Configuración GHL:**
- **Acción:** Send WhatsApp (acción nativa GHL)
- **Action name:** `WA - Llamar luego`
- **Template:** `None - Free form message` (el sistema lo trata como Utility para flujos fuera de ventana 24h — verificado por Juan que GHL maneja esto internamente vía sus templates pre-aprobados)
- **From phone number:** `+1 954-613-6159` (número WhatsApp Acné)
- **Enable branches:** OFF
- **Mensaje (placeholder — Juan lo ajusta en GHL al final):**
  ```
  Hola {{contact.first_name}}, te marco más tarde como acordamos 😊
  Si necesitas algo antes, escríbeme por aquí.
  ```

**Por qué template estático y no LLM:**
Restricción de Meta WhatsApp Business — no permite mensajes libres proactivos. Ver "DECISIÓN ARQUITECTÓNICA CRÍTICA" al inicio del doc. Cuando el paciente responda a este mensaje, recién ahí se abre ventana 24h y Elena Chat (workflow Inbound separado) puede conversar libre con LLM.

**Por qué el mensaje confirma "te marco más tarde" sin especificar tiempo exacto:**
El callback exacto (2h, 4h, 12h, 5d) lo decide la Sub-acción L.3 con el wait dinámico. Hardcodear "te marco en 4 horas" en el template requeriría 4 templates separados o variables que dependen del valor numérico. Para el pilot, mensaje genérico es suficiente — replicabilidad y aprobación de templates más simple.

**Replicabilidad:** cambiar el `From phone number` al de cada tratamiento. Mensaje puede ser idéntico (no menciona el tratamiento explícitamente).

#### Sub-acción L.3 — Condition de horas + Wait dinámico + Go To loop (heredada del clon Botox)

**Tipo:** Condition con 5 branches que leen `elena_callback_hours`, cada branch ejecuta su Wait correspondiente y luego un Go To que vuelve al **Webhook Llamada 1** (ACCIÓN 7 del workflow).

**Configuración GHL:**

| Branch | Condición | Wait | Go To destino |
|--------|-----------|------|---------------|
| Llamar en 2h | `elena_callback_hours` IS `2` | 2 horas | Webhook Llamada 1 |
| Llamar en 4h | `elena_callback_hours` IS `4` | 4 horas | Webhook Llamada 1 |
| Llamar en 12h | `elena_callback_hours` IS `12` | 12 horas | Webhook Llamada 1 |
| Llamar en 5d | `elena_callback_hours` IS `120` | 5 días (120h) | Webhook Llamada 1 |
| None | (ninguna se cumple) | — | Webhook Llamada 1 (con wait default) |

**Por qué loop a Llamada 1 (no Llamada 2):**
Conceptualmente es un **reintento** del mismo paciente. Vapi mantiene contexto de llamadas anteriores en transcripts asociados al `customer.number`, así que Elena Voice cuando vuelve a marcar sabe que ya habló antes con el paciente. No hace falta tener un nodo `Webhook Llamada 2` separado — sería duplicación. El bucle Llamar Luego → Wait → Llamada 1 puede repetirse N veces hasta que el outcome cambie (paciente agenda, rechaza, error, etc.) y la Condition 1 lo rutee a otra rama.

**Por qué los valores son 2 / 4 / 12 / 120:**
Taxonomía cerrada que Elena Voice usa al escribir `elena_callback_hours` en GHL. Si paciente dice "llámame en una hora" → mapea a `2`. "Mañana" → `12` (siguiente día laboral). "La próxima semana" → `120`. Mantener taxonomía cerrada evita que Elena Voice escriba valores arbitrarios que el Condition no sabría rutear.

**Por qué la rama None existe y va al mismo Llamada 1:**
Si por bug Elena Voice escribiera un valor fuera de la taxonomía (ej: `48`), el flujo no se queda atascado — cae en None y reintenta. Es failsafe.

**Replicabilidad:** estas branches y valores son universales para todos los tratamientos. NO se cambia nada al replicar a otro tratamiento. La taxonomía `2/4/12/120` la mantiene Elena Voice global.

El flujo Llamar Luego cierra con el Go To → Llamada 1 (loop). No hay un "Webhook Llamada 2" separado para esta rama.

---

### Rama: Sí Agendó (configurada · 2026-04-17)
- **Condición:** `elena_last_outcome` is `agendo`
- **Stage destino:** `Cita Agendada`
- **Lógica de negocio:** lead que agendó cita en la llamada. Sale del flujo automatizado de seguimiento — el pipeline lo lleva el equipo de confirmaciones desde acá.
- **Acciones tras cambio de stage:** _(pendiente — agregar al configurar el resto del workflow post-NODO 1)_

### Rama: No Agendó (configurada · 2026-04-17)
- **Condición:** `elena_last_outcome` is `no_agendo`
- **Stage destino:** `Seguimiento Humano`
- **Lógica de negocio (palabras de Juan):** la llamada terminó sin agendar pero tampoco fue rechazo claro ni "llamar luego". El paciente queda en `Seguimiento Humano` para que un humano (Laury) revise el transcript de la llamada y decida:
  - Si fue **error de Elena** (Elena no entendió, perdió oportunidad, hubo bug conversacional) → humano llama al lead manualmente para recuperar.
  - Si fue **rechazo genuino** (paciente simplemente no quiso agendar pero no dijo "no me contacten") → humano lo saca del pipeline.
- **Por qué humano y no Elena reintenta:** un reintento ciego de Elena puede ser intrusivo; necesita criterio humano sobre el transcript antes de decidir el siguiente paso.

### Rama: Error Técnico (configurada · 2026-04-17)
- **Condición:** `elena_last_outcome` is `error_tecnico`
- **Stage destino:** `Error Técnico`
- **Lógica de negocio (palabras de Juan):** cualquier fallo técnico durante la llamada — Elena no pudo acceder al calendario, no pudo revisar disponibilidad, herramienta de booking falló, audio se cortó, etc. La llamada terminó sin completar el objetivo por causa NO atribuible al paciente.
- **Por qué stage propio (no se mezcla con No Agendó):** permite que el equipo técnico/Stephanee priorice estos casos como bugs operativos a investigar, separados del seguimiento comercial humano.

### Rama: No Interesado (configurada · 2026-04-17)
- **Condición:** `elena_last_outcome` is `no_interesado`
- **Stage destino:** `No Interesado`
- **Lógica de negocio (palabras de Juan):** el paciente dejó saber explícitamente que no quiere ser contactado, que no le interesa el servicio, o pidió no recibir más llamadas. **Decisión consciente y verbalizada del lead.**
- **Por qué stage propio:** queda registrado como "no contactar" — el equipo NO debe insistir, y el contacto debe excluirse de futuras campañas (audiencias FB, broadcasts, otros workflows). Diferente de `No Agendó` que sí tiene reintento humano.

### Rama: No Contestó (configurada · 2026-04-18 — final)

**Lógica de negocio:** el paciente no contestó la llamada (timeout, voicemail, ocupado). Diferente de Llamar Luego (donde sí hubo conversación). NO Contestó escala al siguiente intento (Llamada 2, 3, 4) con waits crecientes; cada intento manda template Utility WA propio.

**Estructura final (1er intento — post-Llamada 1):**
```
[Stage - Llamada 1]                       ← stage del pipeline (1ª llamada hecha)
[WA - No contesto 1]                       ← template Utility GHL nativo (heredado del clon)
[Wait 4h con 2 sub-branches]
   ├─ Contact Reply → Webhook Llamada 2   ← paciente respondió al WA → escala
   └─ Time Out      → Webhook Llamada 2   ← 4h sin respuesta → escala
```

#### Sub-acción NC.1 — Stage - Llamada 1
- Pipeline: `Leads Nuevos - Acne`
- Stage: `Llamada 1` (representa "1 llamada hecha" — el paciente no contestó la primera)

#### Sub-acción NC.2 — WA - No contesto 1 (template Utility, heredado del clon)

**Configuración GHL:**
- **Acción:** Send WhatsApp (acción nativa GHL)
- **Action name:** `WA - No contesto 1`
- **Template:** `None - Free form message`
- **From phone number:** `+1 954-613-6159`
- **Enable branches:** OFF
- **Mensaje (heredado del clon Botox, Juan ajusta wording final si quiere):**
  ```
  Intenté comunicarme contigo. ¿Prefieres que te llamemos más tarde hoy
  o mañana? O si prefieres, escríbeme por aquí mismo y te ayudo a agendar
  ahora. 😉
  ```

**Por qué template estático:** mismo motivo que L.2 — restricción Meta WhatsApp Business. Ver "DECISIÓN ARQUITECTÓNICA CRÍTICA" al inicio.

**Por qué este wording (3 palancas):**
1. "Intenté comunicarme contigo" → reconoce la llamada perdida sin ser agresivo
2. "¿Prefieres que te llamemos más tarde hoy o mañana?" → ofrece 2 opciones concretas (no abierto = más fácil de responder)
3. "O si prefieres, escríbeme por aquí mismo y te ayudo a agendar ahora" → habilita escalación inmediata por inbound (abre la ventana 24h al instante para conversación libre con Elena Chat)

**Replicabilidad:** mensaje aplica a cualquier tratamiento sin cambios (no menciona acné explícitamente).

#### Sub-acción NC.3 — Wait 4h con 2 sub-branches (heredado del clon)
- **Contact Reply branch:** se dispara si el paciente responde al WA dentro de 4h. → Webhook Llamada 2.
- **Time Out branch:** se dispara si pasaron 4h sin respuesta. → Webhook Llamada 2.

Ambas convergen al mismo destino. La diferencia se registra en GHL: Contact Reply queda como interacción inbound (útil para reportes y abre ventana 24h para Elena Chat conversación libre).

**Por qué ambos van a Llamada 2 (y no loop a Llamada 1 como Llamar Luego):**
En Llamar Luego el paciente sí contestó y pidió posponer → se reusa Llamada 1 como reintento horizontal. En No Contestó NO hubo conversación → escala al siguiente intento (Llamada 2) que es un nodo Webhook separado más adelante en el workflow. Es escalación, no reintento horizontal.

#### Estructura completa del patrón No Contestó (post-llamadas 2, 3, 4)

El workflow Lead Nuevo tiene N nodos `Webhook Llamada N` (1, 2, 3, 4) — cada uno seguido de Wait 15min → Condition N. La rama "No Contestó" de cada Condition repite el patrón NC.1-NC.3 con:
- Stage destino: `Llamada N` (2, 3, 4)
- Mensaje WA: `WA - No contesto N` (con wording escalado: más insistente / urgente conforme N crece)
- Wait time entre intentos: típicamente 4h, 24h, 72h (dependiendo de la política del clon Botox que Juan ya validó)
- Tras Llamada 4 sin respuesta: stage final `Sin Respuesta` (o equivalente — verificar en clon Botox)

**Replicabilidad:** este patrón completo aplica idéntico a todos los tratamientos. Solo cambian los nombres del pipeline/stages y el FROM phone number del WA.

---

### Rama: None del NODO 1 (fallback de seguridad)

**Lógica:** se dispara si `elena_last_outcome` no coincide con ninguno de los 6 valores esperados (agendo, llamar_luego, no_contesto, no_agendo, no_interesado, error_tecnico). Esto solo pasa si:
- Bug en `app.py` de Elena Voice escribe valor fuera de taxonomía
- Custom field se borra antes de que la Condition lo lea (race condition con el Wait 15min — improbable pero posible)
- Elena Voice no escribió el campo (llamada nunca se conectó por error de Vapi)

**Acción típica (heredada del clon Botox):**
- `WA - Internal Notification` al equipo (alerta humana de caso edge)
- Sin cambio de stage (la oportunidad queda donde estaba)
- Posiblemente reintento de Llamada 1 con Go To, o pasar a humano

Juan verifica en GHL qué hace exactamente el clon en esta rama.
