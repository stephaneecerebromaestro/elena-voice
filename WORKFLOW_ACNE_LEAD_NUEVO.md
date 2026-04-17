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

### Rama: Llamar Luego (en construcción · 2026-04-17)

**Lógica de negocio:** el paciente contestó la llamada pero pidió/sugirió ser contactado más tarde. Elena Voice escribió `elena_last_outcome=llamar_luego` y además `elena_callback_hours` (2 / 4 / 12 / 120 = 5 días) según lo que el paciente dijo. Esta rama mueve la oportunidad al stage `Llamar Luego`, manda WA contextual (Mejora 5), espera el tiempo dinámico, y dispara la Llamada 2.

**Por qué esta rama es la implementación de Mejora 5 en Acné:**
El nodo `WA - Llamar Luego` aquí NO es un mensaje estático único. Es la **arquitectura de 2 niveles de fallback** que Juan definió:

```
[Custom Webhook → Elena Chat]
        ↓
   Elena Chat decide internamente:
   ├─ Gates OK + LLM OK → envía mensaje contextual vía GHL Conversations API
   ├─ Gates fallan (poco contexto) → envía template estático vía GHL Conversations API
   └─ Crash/timeout → no responde
        ↓
[Condition: webhook success?]
   ├─ SÍ → continúa
   └─ NO (timeout/error de Elena Chat) → [WA estático respaldo en GHL]
        ↓
[Wait dinámico (elena_callback_hours)]
        ↓
[Webhook Llamada 2]
```

**Por qué 2 niveles:**
- **Nivel 1 (Elena Chat interno):** decide LLM vs template fallback según hay contexto suficiente. Esto es Mejora 5 propiamente — la inteligencia conversacional.
- **Nivel 2 (GHL respaldo):** WA estático manual en GHL para el caso edge donde Elena Chat se cae completa. Garantiza que el paciente SIEMPRE recibe un mensaje, aunque haya outage.

**Por qué Elena Chat envía directo (no devuelve texto a GHL para que GHL envíe):**
- `send_whatsapp_message()` de Elena Chat ya usa GHL Conversations API (no Twilio). El stack es coherente.
- Reduce latencia de webhook (no espera roundtrip de payload de mensaje).
- Elena Chat puede registrar contexto del envío (qué template usó, qué LLM generó, qué contexto leyó) en sus propias tablas Supabase para análisis posterior.

#### Sub-acción L.1 — Stage - Llamar Luego (configurada · 2026-04-17)
- **Tipo:** Create Or Update Opportunity
- **Action name:** `Stage - Llamar Luego`
- **In Pipeline:** `Leads Nuevos - Acne`
- **In Pipeline Stage:** `Llamar Luego`
- **Allow opportunity to move to any previous stage:** ON
- **Allow duplicate opportunities:** OFF
- **Otros campos (Opportunity Name, Source, Value, Status):** vacíos — ya fueron seteados en ACCIÓN 2 al crear la oportunidad inicial. Este nodo solo cambia el stage.
- **Por qué solo stage change:** la oportunidad ya existe (creada en ACCIÓN 2), no hay que recrearla. Dejar campos vacíos = "no tocar". Solo el stage se actualiza.
- **Replicabilidad:** mismo patrón en todos los tratamientos. Cambiar `Pipeline` y `Stage` al equivalente del nuevo tratamiento.

#### Sub-acción L.2 — Custom Webhook → Elena Chat (mensaje contextual Mejora 5)

**Configuración GHL:**
- **Action name:** `Webhook - Elena Chat WA Llamar Luego`
- **Event:** `CUSTOM`
- **Method:** `POST`
- **URL:** `https://elena-lhr.onrender.com/webhook/ghl/followup`
- **Authorization:** `None` (API key irá en headers)
- **Headers:**
  - `Authorization: Bearer <ELENA_CHAT_WEBHOOK_TOKEN>` _(token a generar en Elena Chat — variable env)_
  - `Content-Type: application/json`
- **Content-Type:** `application/json`
- **Raw Body:**
  ```json
  {
    "contact_id": "{{contact.id}}",
    "treatment": "acne",
    "outcome": "llamar_luego",
    "callback_hours": "{{contact.elena_callback_hours}}",
    "patient_name": "{{contact.first_name}}",
    "phone": "{{contact.phone}}",
    "transcript_summary": "{{contact.elena_call_summary}}",
    "node_context": "wa_llamar_luego_post_llamada_1"
  }
  ```

**Por qué cada campo del body:**
- `contact_id`: Elena Chat lo usa para enviar el WA vía Conversations API GHL.
- `treatment`: routing interno en Elena Chat (qué bot/prompt usar).
- `outcome`: contexto crítico (paciente pidió ser llamado luego — el mensaje debe alinearse con eso).
- `callback_hours`: cuándo será el próximo intento, para que el mensaje pueda referenciar el timing ("te marco en X horas" si aplica).
- `patient_name`: personalización rápida sin tener que ir a GHL a leer.
- `phone`: redundante pero útil para logs/debug en Elena Chat.
- `transcript_summary`: resumen escrito por Elena Voice tras la llamada (custom field). ESTE es el contexto principal que el LLM usa para generar mensaje no-genérico.
- `node_context`: identifica desde qué nodo del workflow se llamó (para que Elena Chat pueda diferenciar "post-llamada-1-llamar-luego" vs "post-llamada-2-no-contesto" si en el futuro se reutiliza el endpoint).

**Branches del Custom Webhook (GHL nativo):**
- **Success branch:** continúa al Wait dinámico (Sub-acción L.4).
- **Failed branch:** ejecuta WA estático de respaldo (Sub-acción L.3).

**Replicabilidad para otra rama (ej. No Contestó) o tratamiento:**
- Cambiar `outcome` y `node_context`.
- Cambiar `treatment` para otro tratamiento.
- URL, headers, estructura — TODO igual.

#### Sub-acción L.3 — WA estático de respaldo (solo se ejecuta si Custom Webhook falla)

**Configuración GHL:**
- **Acción:** Send WhatsApp
- **Action name:** `WA - Llamar Luego (RESPALDO)`
- **Template:** `None - Free form message`
- **From phone number:** `+1 954-613-6159`
- **Mensaje:**
  ```
  Hola {{contact.first_name}}, te contacto más tarde como acordamos 😊

  Cualquier cosa que necesites, escríbeme por aquí.
  ```

**Por qué este mensaje (estático mínimo):**
- Es el ÚLTIMO recurso — solo se ejecuta si Elena Chat se cayó completa (raro). En condiciones normales, el paciente recibe un mensaje contextual generado por LLM o un template inteligente que Elena Chat eligió internamente.
- Genérico pero coherente con la promesa de la llamada ("te llamo más tarde").
- Sin referencias específicas a tiempo (no sabemos `callback_hours` aquí — eso lo manejaría Elena Chat con su template estático interno que sí tiene acceso al campo).

**Conexión:** este nodo solo se conecta al **Failed branch** del Custom Webhook anterior. Si el webhook tuvo éxito, este nodo NO se ejecuta.

**Replicabilidad:** mantener mensaje genérico de respaldo por nodo (Llamar Luego, No Contestó, etc.). Es seguro asumir que cuando este se ejecuta, Elena Chat está caída — por eso debe ser independiente de cualquier custom field complejo.

#### Sub-acción L.4 — Wait dinámico (`elena_callback_hours`)

_(pendiente — se documenta al configurar)_

#### Sub-acción L.5 — Webhook Llamada 2

_(pendiente — se documenta al configurar)_

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

_(Pendientes de configurar: rama Llamar Luego, rama No Contestó, rama None.)_
