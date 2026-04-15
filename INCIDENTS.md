# INCIDENTS.md — Registro de incidentes operacionales de Elena Voice

> Este archivo documenta incidentes operacionales que NO produjeron cambios
> de código (ej. fixes vía API externa: Twilio, Vapi, GHL, Render) y por
> tanto no aparecen en `git log`. Sin este archivo, futuras auditorías no
> tienen forma de saber qué pasó. Cada entrada incluye: contexto, evidencia
> verificable (timestamps, output crudo de comandos), y acción tomada.

---

## 2026-04-14 ~21:55 UTC — Twilio voice_url borrado por configuración WhatsApp

### Síntoma reportado por Juan
> "estoy trabajando con elena chat en los chat bots, configuramos todo para
> que los mismo numero de twilio que usa elena chat lhr y botox sean los
> mismo del chat de whatsapp. Para poder verifficar los numbers tuve que
> configurar un forward de las llamadas del numero a mi telefono. creo que
> al hacer eso desconfigure la conexion con vapi"

Llamadas entrantes a `+1 786-746-7292` (LHR) y `+1 786-983-5076` (Botox)
no llegaban a Vapi.

### Diagnóstico

Verificación vía Twilio API
(`GET /2010-04-01/Accounts/{SID}/IncomingPhoneNumbers.json`) mostró:

```
+17867467292  voice_url: (vacío)  status_callback: https://api.vapi.ai/twilio/status
+17869835076  voice_url: (vacío)  status_callback: https://api.vapi.ai/twilio/status
```

El `voice_url` (que dirige las llamadas entrantes a Vapi) había quedado
en blanco. El `status_callback` y `sms_url` sobrevivieron, así que el
número aparecía como "activo" en Vapi UI — falso positivo.

**Causa raíz:** al configurar forwarding/verificación de WhatsApp, el flow
de Twilio sobrescribió `voice_url`. Vapi seguía registrado del lado de
Vapi pero Twilio no sabía a dónde mandar las llamadas entrantes.

### Fix aplicado

PATCH a Twilio API (timestamp registrado en `date_updated`):

```bash
TSID=$TWILIO_ACCOUNT_SID
VAPI_URL=https://api.vapi.ai/twilio/inbound_call
for sid in $LHR_PHONE_SID $BOTOX_PHONE_SID; do
  curl -u "$TSID:$TTOK" -X POST \
    "https://api.twilio.com/2010-04-01/Accounts/$TSID/IncomingPhoneNumbers/$sid.json" \
    --data-urlencode "VoiceUrl=$VAPI_URL" \
    --data-urlencode "VoiceMethod=POST"
done
```

Output del PATCH (capturado en log de sesión 2026-04-14):
```
=== Patching PN080c4736778c756a5eb74b6540521c56 ===
  number:     +17867467292
  voice_url:  https://api.vapi.ai/twilio/inbound_call
  method:     POST
  updated_at: Tue, 14 Apr 2026 21:55:58 +0000
=== Patching PNf71ddcca3b94eff95b179b8fc6c3f385 ===
  number:     +17869835076
  voice_url:  https://api.vapi.ai/twilio/inbound_call
  method:     POST
  updated_at: Tue, 14 Apr 2026 21:55:58 +0000
```

### Verificación

1. **API check** (re-fetch tras el PATCH): ambos `voice_url` =
   `https://api.vapi.ai/twilio/inbound_call`, `voice_method=POST`.
2. **Test funcional por Juan:** "Listo ya los probe y si me responden."
   Ambos números reciben llamadas y Vapi responde correctamente.
3. **Re-verificación post-auditoría 2026-04-15** (este archivo):

```
+17867467292  voice_url: https://api.vapi.ai/twilio/inbound_call  date_updated: Tue, 14 Apr 2026 21:55:58 +0000
+17869835076  voice_url: https://api.vapi.ai/twilio/inbound_call  date_updated: Tue, 14 Apr 2026 21:55:58 +0000
```

### Lección operativa para el futuro

**Siempre que Juan toque la config de un número Twilio compartido con WhatsApp**
(verificación, forwarding, sender registration), revisar el `voice_url` después
porque Twilio puede borrarlo silenciosamente. El `status_callback` sobrevive y
da falsa señal de "todo bien" en Vapi UI.

**Health check sugerido (no implementado todavía):** agregar a Elena Ops una
verificación periódica que `voice_url` de los números Twilio activos apunte a
`api.vapi.ai/twilio/inbound_call`. Si está vacío o cambia → alerta N3 a Juan.

### Anti-pattern detectado (auditoría Stephanee 2026-04-14 22:55)

Reporté este fix en mi resumen de sesión sin commit que respaldara la
acción. Stephanee lo marcó como CRITICAL #1 — "ítem inventado en resumen
ejecutivo". El fix sí ocurrió y hay evidencia verificable (timestamps de
Twilio API), pero al no producir un commit no había rastro en `git log`.
**Este archivo (INCIDENTS.md) existe precisamente para resolver ese gap.**
