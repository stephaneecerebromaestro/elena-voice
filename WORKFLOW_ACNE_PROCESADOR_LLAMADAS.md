# Workflow GHL: Elena Voice Acné — Procesador de Llamadas
# ════════════════════════════════════════════════════════════
# Propósito de este documento:
# Material de ENTRENAMIENTO para Elena Voice — descripción nodo
# por nodo del workflow GHL Procesador que registra outcomes
# de cada llamada de Elena Voice Acné.
#
# Workflow ID GHL: abc4e12e-d42a-45a7-bf93-77f40131f33b
# URL: app.gohighlevel.com/location/hzRj7DV9erP8tnPiTv7D/workflow/abc4e12e-d42a-45a7-bf93-77f40131f33b
# Workflow modelo: clonado de `Elena Voice Botox - Vapi - Procesador de Llamadas`
#                  (id 14371f66-5b48-4e3c-b440-e00beeb269f6)
# Estado: Draft (lista para publicar · 2026-04-18)
# ════════════════════════════════════════════════════════════

## Visión general — qué hace y qué NO hace

**Qué hace:**
Este workflow se dispara cada vez que Elena Voice Acné termina una llamada
(inbound u outbound) y `app.py` añade el tag `elena_resultado_acne` al contacto.
Las acciones que realiza:
1. Wait corto (~ 30s) para asegurar que todos los custom fields de la llamada
   estén persistidos en GHL (race condition con la API).
2. Lee `elena_last_outcome` y rutea a una de 7 ramas según el resultado.
3. En cada rama:
   - **WA - Internal Notification**: avisa al equipo interno por WhatsApp del
     resultado de la llamada (alerta humana, no para el paciente).
   - **Remove Tag** `elena_resultado_acne`: imprescindible para que el workflow
     pueda RE-disparar en la siguiente llamada del mismo contacto. Sin esto,
     el segundo `_add_tag_to_contact()` de Elena Voice no genera trigger
     porque el tag ya existe.

**Qué NO hace:**
- **NO toca el pipeline** (no mueve oportunidades). Eso lo hace el workflow
  paralelo `Elena Voice Acne - Lead Nuevo` (NODO 1) leyendo el mismo
  `elena_last_outcome` 15 min después.
- NO envía mensajes WA al paciente. El WA proactivo lo manda el workflow
  Lead Nuevo en sus ramas correspondientes.
- NO modifica datos clínicos ni triggers de otros workflows (excepto
  remover su propio tag).

**Por qué dos workflows separados (Procesador + Lead Nuevo) que leen lo mismo:**
- **Separation of concerns:** Procesador = "qué pasó en esta llamada"
  (tracking + alerta interna). Lead Nuevo = "qué hacemos con el lead ahora"
  (pipeline + siguientes acciones).
- **Robustez:** si Lead Nuevo cae o tiene un bug en su Wait 15min, el equipo
  interno igual recibe la notificación inmediata vía Procesador.
- **Disparadores distintos:** Procesador se activa por TAG (cualquier llamada
  inbound/outbound). Lead Nuevo se activa por FORM/TAG inicial y solo procesa
  llamadas dentro de su flujo lineal.

═══════════════════════════════════════════════════════════════════════════════

## TRIGGER

### Trigger único — Contact Tag Added

- **Tipo:** Contact Tag Added
- **Tag:** `elena_resultado_acne`

**Quién añade este tag:**
`app.py` de Elena Voice (`/root/agents/elena-voice/app.py`) en la función de
end-of-call handler, línea ~1580:
```python
_treatment = get_assistant_config(_eoc_assistant_id).get("treatment", "botox")
_add_tag_to_contact(contact_id, f"elena_resultado_{_treatment}")
```
El `_treatment` se resuelve desde el `assistantId` del call vía
`config.ASSISTANTS` dict (multi-tenant por tratamiento — fix introducido
2026-04-17 commit a930790).

**Por qué este patrón (escribir custom field + añadir tag aparte):**
- `elena_last_outcome` (custom field) es la **fuente de verdad** del resultado.
- El tag `elena_resultado_<treatment>` es solo el **disparador** del workflow.
- Si solo escribiéramos el custom field, GHL no tiene trigger nativo para
  "custom field changed to X" → necesitamos el tag para activar el workflow.
- Separación: el tag puede ir/venir muchas veces; el custom field siempre
  refleja la última llamada.

**Replicabilidad:** cambiar el tag a `elena_resultado_<tratamiento>` para cada
treatment. Asegurar que `config.ASSISTANTS` en `app.py` tiene el assistantId
mapeado correctamente al treatment string.

═══════════════════════════════════════════════════════════════════════════════

## NODO INICIAL — Wait

- **Tipo:** Time Delay
- **Wait:** ~30 segundos (verificar valor exacto en clon)

**Por qué este wait existe:**
`app.py` de Elena Voice escribe los custom fields y luego añade el tag. Los
writes son secuenciales pero la API de GHL no garantiza atomicidad ni
visibilidad inmediata entre llamadas distintas. Si el Condition leyera
`elena_last_outcome` 0ms después del trigger, podría no verlo todavía. 30s
es margen seguro.

**Replicabilidad:** el wait es función de la latencia GHL, no del tratamiento.
NO se cambia al replicar.

═══════════════════════════════════════════════════════════════════════════════

## NODO PRINCIPAL — Condition (7 branches)

Lee `Custom Field: elena_last_outcome` y rutea a la rama correspondiente.

### Configuración general
- **Action name:** `Condition`
- **Scenario recipe:** Build Your Own
- **7 branches**, todas leyendo el mismo custom field

### Las 7 ramas

| Rama | Condición | Acción específica además del WA Notification + Remove Tag |
|------|-----------|----------------------------------------------------------|
| Si agendó | `elena_last_outcome` IS `agendo` AND `Tags Includes agendo_consulta_acne` | (ninguna adicional, el WA notifica al equipo) |
| Si no contestó | `elena_last_outcome` IS `no_contesto` | (ninguna adicional) |
| Llamar Luego | `elena_last_outcome` IS `llamar_luego` | (ninguna adicional) |
| No Agendó | `elena_last_outcome` IS `no_agendo` | (ninguna adicional) |
| Error Técnico | `elena_last_outcome` IS `error_tecnico` | (ninguna adicional) |
| No Interesado | `elena_last_outcome` IS `no_interesado` | (ninguna adicional) |
| None | (cuando ninguna se cumple) | Solo Remove Tag (sin WA — caso edge, alerta no útil) |

**Por qué la rama "Si agendó" tiene condición extra (`agendo_consulta_acne`):**
Validación doble. `elena_last_outcome=agendo` indica que Elena Voice intentó
crear booking; `Tags Includes agendo_consulta_acne` confirma que el tool
`create_booking` retornó éxito y el tag de confirmación se añadió. Sin la doble
validación, un fallo silencioso del booking generaría notificación falsa de
"agendó" cuando no agendó realmente.

**Por qué la rama None existe:**
Failsafe — si por bug `elena_last_outcome` queda con un valor fuera de la
taxonomía, el flujo NO se queda atascado. Solo remueve el tag (para no
bloquear futuros disparos) y termina sin acción.

═══════════════════════════════════════════════════════════════════════════════

## NODOS POR RAMA

### Sub-nodo común 1 — WA - Internal Notification (todas las ramas excepto None)

**Tipo:** Send WhatsApp (acción nativa GHL)
**Acción:** notifica al equipo interno por WhatsApp con un texto que indica
qué pasó en la llamada (resultado + nombre paciente + link al contacto).

**Configuración GHL:**
- Acción: Send WhatsApp
- Action name: `WA - Internal Notification` (o similar por rama)
- From phone number: `+1 954-613-6159` (Acné)
- To: número WhatsApp del equipo interno (configurado por Juan en cada nodo)
- Mensaje: texto que incluye `{{contact.first_name}}`, `{{contact.elena_summary}}`,
  link al contacto en GHL, etc. — Juan ajusta wording por rama.

**Por qué WhatsApp y no email:**
- Velocidad: equipo lee WA antes que email
- Móvil-first: equipo opera desde celular
- Acuse de lectura nativo de WhatsApp

**Replicabilidad:** cambiar el FROM y el wording (mencionar tratamiento
correcto). Estructura idéntica.

---

### Sub-nodo común 2 — Remove Tag (todas las ramas, incluyendo None)

**Tipo:** Remove Contact Tag
**Action name:** `Remove Tag`
**Tags:** `elena_resultado_acne`
**Remove all tags:** OFF

**⚠️ CRÍTICO — sin este nodo el workflow no puede re-disparar:**

GHL trigger `Contact Tag Added` se dispara solo cuando un tag pasa de
"no presente" a "presente". Si el tag queda en el contacto después de la
primera llamada, las siguientes llamadas NO disparan el workflow (porque
GHL hace add idempotente — el tag ya existe, no es "added" por segunda vez).

Por eso CADA rama (incluyendo None) debe terminar con Remove Tag.
Sin esto, después de la primera llamada de un contacto, las subsiguientes
llamadas NO procesarán outcome (notificaciones perdidas, tracking roto).

**Replicabilidad:** cambiar el tag a `elena_resultado_<tratamiento>`.

═══════════════════════════════════════════════════════════════════════════════

## NOTAS HISTÓRICAS DE CONFIGURACIÓN (2026-04-17)

Cosas que se ajustaron al clonar de Botox y deben verificarse en futuros
clones a otros tratamientos:

1. **Tag del trigger:** cambiado de `elena_resultado_botox` → `elena_resultado_acne`.
2. **Tag de Remove Tag (cada rama):** cambiado igualmente.
3. **Branch "Si agendó":** la 2da condición `Tags Includes agendo_consulta_botox`
   se cambió a `agendo_consulta_acne`.
4. **Mensajes de WA Internal Notification:** texto modificado para decir "Acne"
   en lugar de "Botox" (Juan lo verificó personalmente).
5. **Spreadsheet rows del clon Botox:** ELIMINADOS por Juan. El clon Botox tenía
   nodos `Create Spreadsheet Row` que escribían a Google Sheets para tracking;
   esos sheets eran obsoletos y Juan ya no los usa.

═══════════════════════════════════════════════════════════════════════════════

## CHECKLIST DE PUBLICACIÓN

Antes de hacer Draft → Published de este workflow, confirmar:

- [ ] Trigger tag = `elena_resultado_acne` (no botox)
- [ ] Las 6 ramas con outcome (todas menos None) tienen `elena_last_outcome IS <valor>` correcto
- [ ] La rama "Si agendó" tiene la 2da condición `agendo_consulta_acne` (o se eliminó si no se necesita)
- [ ] Cada nodo `Remove Tag` tiene `elena_resultado_acne` (NO `elena_resultado_botox`)
- [ ] Mensajes de WA Internal Notification dicen "Acne" en el wording
- [ ] FROM phone number correcto en cada Send WhatsApp del workflow
- [ ] (Opcional) Equipo interno destinatario de WA Notification es el correcto para Acné

═══════════════════════════════════════════════════════════════════════════════

## REPLICABILIDAD A OTROS TRATAMIENTOS

Para crear el Procesador de un tratamiento nuevo (ej. Cicatrices):

1. **Clonar este workflow** desde GHL UI (Save As Template → Create New).
2. **Renombrar:** `Elena Voice <Tratamiento> - Vapi - Procesador de Llamadas`.
3. **Trigger tag:** `elena_resultado_<tratamiento>` (ej: `elena_resultado_cicatrices`).
4. **Remove Tag en cada rama:** mismo tag nuevo.
5. **Branch "Si agendó":** 2da condición → `agendo_consulta_<tratamiento>` (o eliminar
   si Vapi del nuevo tratamiento no usa este patrón de doble validación).
6. **Mensajes de WA Internal Notification:** cambiar wording al tratamiento
   nuevo (incluir nombre comercial si aplica — ej. "ScarlessBody" para Cicatrices).
7. **FROM phone number:** del nuevo tratamiento (consultar `config.ASSISTANTS`
   en `app.py` de Elena Voice o vía `GET /phone-number` de Vapi API).
8. **Verificar `app.py`:** que el `assistantId` del nuevo tratamiento esté
   registrado en `config.ASSISTANTS` con el `treatment` string correcto.
   Sin esto, `_add_tag_to_contact(...)` no genera el tag esperado.
9. **Publicar:** Draft → Published.

**Tiempo estimado de replicación: ~30 minutos** (clon + 4 ediciones + publicar).
