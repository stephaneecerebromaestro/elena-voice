"""v18.py — tools y guardas nuevas de Elena Voice (proyecto OPTIMIZACIÓN ELENA VOICE, v18).

POR QUÉ ESTE ARCHIVO EXISTE APARTE DE app.py
  app.py (2.500 líneas) atiende llamadas de pacientes reales ahora mismo. Todo lo nuevo vive aquí y se
  engancha en app.py con pocas líneas, así lo nuevo se puede leer, probar y revertir sin releer el backend
  entero. Las dependencias (GHL, zona horaria, contactos) se INYECTAN con `wire()` desde app.py: este módulo
  no importa app.py, así que no hay import circular.

QUÉ TRAE (ARQUITECTURA.md §2.2 del proyecto)
  · `registrar_baja(motivo)`        — «no me llamen» / «no me interesa» dejan de ser una promesa vacía.
  · `pedir_persona(motivo, cuando)` — tarea para recepción en GHL con el motivo y la franja. **Arregla un
    defecto vivo:** `request_human_handoff` está registrada en Vapi pero NO existía en el backend, así que
    devolvía «Función no reconocida» (12 ejecuciones en 14 d). El alias la hace funcionar.
  · `programar_llamada(fecha_hora_iso, motivo)` — la hora exacta que pidió la paciente, validada, y
    **devuelve la frase que Elena debe decir** para que no prometa lo que el sistema no cumple.
  · `confirmar_horario(startTime)` — repite la hora en palabras y habilita la reserva.
  · `contexto_paciente()` — quién es, su segmento, si ya tiene cita, qué pasó en la última llamada.
  · `enviar_info(tipo)` — dirección/horarios por escrito. **APAGADO por defecto**: mandar un mensaje a una
    paciente real necesita que Juan apruebe la plantilla (línea roja de la casa). Apagado, registra y
    devuelve la frase, pero NO envía.
  · Guardas: reserva solo sobre un horario ofrecido y confirmado; tools de acción bloqueadas en buzón.

CÓMO SE ACTIVAN LAS GUARDAS (sin riesgo para producción)
  Por BRAZO, leyendo `?arm=` de la URL de la tool — NO por `assistantId` (los dos brazos del A/B son
  versiones del MISMO asistente). `arm=b` → guardas activas. Sin parámetro o `arm=a` → comportamiento de
  hoy, intacto. **Excepción deliberada:** el gate de buzón se aplica SIEMPRE a `pedir_persona`, porque sin
  él recepción recibiría una tarea por cada buzón (10 de las 12 ejecuciones de 14 d fueron buzones).

AVISOS: van a GHL (tarea + nota en la ficha), que es donde trabaja recepción, y al log de Render. No hay
  otro canal: el disco de Render es efímero y el chat de Juan no es un canal de operación.

DESVÍO DOCUMENTADO respecto de ARQUITECTURA.md
  El plan llamaba `elena_callback_at` al campo de la hora exacta. **No se crea campo nuevo**: ya existe
  `elena_callback_time` (ISO, verificado en los 104 campos de GHL el 2026-10-05) y es exactamente eso.
  La rama de GHL de F3a leerá `elena_callback_time`.
"""
import json
import os
import re
import threading
from datetime import datetime, timedelta

# ── dependencias inyectadas desde app.py (wire) ───────────────────────────────────────────────
D = {}


def wire(**deps):
    """app.py llama esto una vez con sus helpers. Nada en este módulo toca GHL por su cuenta."""
    D.update(deps)


# ── banderas (todo lo que puede tocar a una paciente nace apagado) ────────────────────────────
def promete_hora():
    """ON por defecto desde v18.6 (2026-10-06): los 6 workflows de GHL ya esperan hasta `elena_callback_time`
    (F3a, verificado con una llamada real: GHL programó 3:05:40 pm = lo que guardó Elena). Con
    ELENA_PROMETE_HORA=0 Elena vuelve a prometer solo la franja (si algún día una escalera deja de cumplirla)."""
    return os.environ.get("ELENA_PROMETE_HORA", "1") != "0"


def enviar_info_activo():
    """ON solo con la plantilla aprobada por Juan (línea roja: mensaje a paciente real)."""
    return os.environ.get("ELENA_ENVIAR_INFO", "0") == "1"


def guardas_activas(arm):
    return (arm or "a").lower() == "b"


# ── memoria por llamada (acotada: el proceso de Render es largo) ───────────────────────────────
_MAX = 400
_slots = {}        # call_id → [startTime ISO ofrecidos]
_confirmado = {}   # call_id → startTime ISO confirmado por la paciente
_turnos = {}       # call_id → True si ya hubo un turno de paciente ≥3 palabras
_lock = threading.Lock()


def _poner(d, k, v):
    if not k:
        return
    with _lock:
        d[k] = v
        if len(d) > _MAX:
            for vieja in list(d)[: len(d) - _MAX]:
                d.pop(vieja, None)


def recordar_slots(call_id, resultado):
    """Guarda los horarios que `check_availability` acaba de ofrecer en esta llamada."""
    isos = []
    if isinstance(resultado, dict):
        for clave in ("slots", "availableSlots", "horarios", "times"):
            for s in (resultado.get(clave) or []):
                if isinstance(s, str):
                    isos.append(s)
                elif isinstance(s, dict):
                    for k in ("startTime", "start", "iso", "slot"):
                        if s.get(k):
                            isos.append(s[k])
                            break
    if isos:
        _poner(_slots, call_id, isos)
    return isos


# ── hora en palabras (las cifras se dicen en palabras; el backend las convierte) ───────────────
_UNID = {1: "una", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis", 7: "siete", 8: "ocho",
         9: "nueve", 10: "diez", 11: "once", 12: "doce"}
_MIN = {0: "", 15: " y cuarto", 30: " y media", 45: " y cuarenta y cinco"}
_NUM = {1: "uno", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis", 7: "siete", 8: "ocho", 9: "nueve",
        10: "diez", 11: "once", 12: "doce", 13: "trece", 14: "catorce", 15: "quince", 16: "dieciséis",
        17: "diecisiete", 18: "dieciocho", 19: "diecinueve", 20: "veinte", 30: "treinta", 40: "cuarenta", 50: "cincuenta"}


def minutos_en_palabras(m):
    """1-59 en palabras («veintiocho», «treinta y cinco»): la voz leía «y 28» como cifra (v18.6)."""
    if m in _NUM:
        return _NUM[m]
    dec, uni = divmod(m, 10)
    return f"veinti{_NUM[uni]}" if dec == 2 else f"{_NUM[dec * 10]} y {_NUM[uni]}"


def hora_en_palabras(dt):
    h12 = dt.hour % 12 or 12
    franja = "de la mañana" if dt.hour < 12 else ("de la tarde" if dt.hour < 19 else "de la noche")
    minutos = _MIN.get(dt.minute, f" y {minutos_en_palabras(dt.minute)}" if dt.minute else "")
    return f"{_UNID[h12]}{minutos} {franja}"


def dia_en_palabras(dt, ahora):
    dias = (dt.date() - ahora.date()).days
    if dias == 0:
        return "hoy"
    if dias == 1:
        return "mañana"
    if dias < 7:
        return f"el {D['DAYS_ES'][dt.weekday()]}"
    return f"el {D['DAYS_ES'][dt.weekday()]} {dt.day} de {D['MONTHS_ES'][dt.month - 1]}"


def a_las(dt):
    """«a la una…» pero «a las dos…»: el artículo concuerda con la hora (banco E1: «a las una» sonaba mal)."""
    return ("a la " if dt.hour % 12 == 1 else "a las ") + hora_en_palabras(dt)


def cuando_en_palabras(dt, ahora):
    return f"{dia_en_palabras(dt, ahora)} {a_las(dt)}"


# ── utilidades de contacto ────────────────────────────────────────────────────────────────────
def _contacto(args):
    """contactId directo, o el de GHL por el teléfono de quien habla. ('' si no se encuentra.)"""
    if args.get("contactId"):
        return args["contactId"]
    tel = args.get("callerPhone", "")
    if not tel:
        return ""
    r = D["handle_get_contact"]({"callerPhone": tel}) or {}
    return r.get("contactId", "") if r.get("found") else ""


def _log(evento, datos):
    """Una línea por evento en el log de Render (es el registro durable: el disco de Render es efímero)."""
    print(f"[v18][{evento}] " + json.dumps(datos, ensure_ascii=False, default=str), flush=True)


# ── 1 · registrar_baja ────────────────────────────────────────────────────────────────────────
FRASES_BAJA = {
    "no_llamar": "Entendido, te saco de nuestra lista ahora mismo y no te volvemos a llamar. Gracias por decírmelo.",
    "no_interesa": "Perfecto, lo anoto y no te insistimos más. Si algún día lo quieres ver, aquí estamos.",
}


def handle_registrar_baja(args):
    """Ejecuta la baja de verdad: tag `no_contactar` (frena TODAS las escaleras, TANDA 1 #13 = A),
    `dnd` si pidió no ser llamada, nota con el motivo y el id de la llamada. Devuelve la despedida."""
    motivo = (args.get("motivo") or "no_interesa").strip().lower()
    if motivo not in FRASES_BAJA:
        motivo = "no_interesa"
    cid = _contacto(args)
    call_id = args.get("_call_id", "")
    hecho = {"tag": False, "dnd": False, "nota": False}
    if cid:
        hecho["tag"] = bool(D["_add_tag_to_contact"](cid, "no_contactar"))
        hecho["tag_motivo"] = bool(D["_add_tag_to_contact"](cid, f"baja_{motivo}"))
        if motivo == "no_llamar":
            hecho["dnd"] = bool(D["ghl_v2_put"](f"/contacts/{cid}", {"dnd": True}))
        ahora = datetime.now(D["TZ"]).strftime("%Y-%m-%d %H:%M")
        hecho["nota"] = bool(D["_add_note_to_contact"](
            cid, f"BAJA registrada por Elena ({motivo}) el {ahora} ET · llamada {call_id} · "
                 f"tag no_contactar aplicado. No volver a llamar."))
    _log("registrar_baja", {"contacto": cid, "motivo": motivo, "call_id": call_id, **hecho})
    return {"success": bool(cid), "motivo": motivo, "aplicado": hecho,
            "frase": FRASES_BAJA[motivo],
            "message": FRASES_BAJA[motivo] if cid else
            "Entendido, lo anoto. (No encontré tu ficha; queda registrado en la llamada.)"}


# ── 2 · pedir_persona (sustituye a request_human_handoff, que hoy devuelve error) ──────────────
def handle_pedir_persona(args):
    """Crea una tarea para recepción con el motivo y la franja, deja la nota en la ficha, y devuelve la
    frase que Elena dice. NO promete una hora concreta: promete el aviso."""
    motivo = (args.get("motivo") or "quiere hablar con una persona").strip()
    cuando = (args.get("cuando") or "").strip()
    cid = _contacto(args)
    call_id = args.get("_call_id", "")
    ahora = datetime.now(D["TZ"])
    vence = (ahora + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S%z")
    tarea = False
    if cid:
        try:
            r = D["ghl_v2_post"](f"/contacts/{cid}/tasks", {
                "title": "Llamar a esta paciente — lo pidió a Elena",
                "body": f"Motivo: {motivo}" + (f" · Cuándo pidió: {cuando}" if cuando else "")
                        + f" · Llamada {call_id} · Registrado {ahora.strftime('%Y-%m-%d %H:%M')} ET",
                "dueDate": vence, "completed": False}, contacts_version=True)
            tarea = bool(r)
        except Exception as e:
            _log("pedir_persona_error", {"error": str(e), "contacto": cid})
        D["_add_note_to_contact"](cid, f"PIDIÓ HABLAR CON UNA PERSONA · {motivo}"
                                       + (f" · {cuando}" if cuando else "")
                                       + f" · llamada {call_id} · {ahora.strftime('%Y-%m-%d %H:%M')} ET"
                                       + ("" if tarea else " · ⚠️ la tarea de GHL no se pudo crear"))
    _log("pedir_persona", {"contacto": cid, "motivo": motivo, "cuando": cuando, "tarea": tarea,
                           "call_id": call_id})
    frase = ("Claro. Le aviso ahora mismo al equipo para que te llame una persona"
             + (f", {cuando}" if cuando else " lo antes posible") + ". ¿Te parece bien?")
    return {"success": True, "tarea_creada": tarea, "frase": frase, "message": frase}


# ── 3 · programar_llamada (la hora exacta, validada, con la frase) ────────────────────────────
HORIZONTE_MIN = timedelta(minutes=10)
HORIZONTE_MAX = timedelta(days=60)
VENTANA = (8, 20)           # 8:00–19:59 ET (criterio conservador de §501.616(6))


def _ajustar(dt):
    """Mete la hora pedida dentro de la ventana legal y fuera del domingo. Devuelve (dt, movido)."""
    movido = False
    if dt.hour < VENTANA[0]:
        dt = dt.replace(hour=10, minute=0, second=0, microsecond=0); movido = True
    elif dt.hour >= VENTANA[1]:
        dt = (dt + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0); movido = True
    if dt.weekday() == 6:      # domingo: la clínica no abre
        dt = (dt + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0); movido = True
    return dt, movido


def _bucket(horas):
    """Compatibilidad con la escalera de hoy, que solo entiende 2/4/12/120."""
    if horas <= 3:
        return 2
    if horas <= 8:
        return 4
    if horas <= 66:
        return 12
    return 120


def hora_para_ghl(dt):
    """Lo que se guarda en `elena_callback_time`: hora LOCAL de Miami SIN zona («2026-10-06 11:09:00»).

    Medido en GHL el 2026-10-06 (workflow de prueba F3a, Wait «Until a specific date/time» en modo Dynamic):
    con la zona escrita («…-0400» o «…-04:00») GHL la aplica DOS veces y espera 4 h de más (5 h en invierno);
    sin zona la toma en la zona de la cuenta (America/New_York) y suelta al minuto. Por eso aquí nunca va %z.
    """
    local = D["TZ"].normalize(dt.astimezone(D["TZ"]))
    return local.strftime("%Y-%m-%d %H:%M:%S")


def handle_programar_llamada(args):
    ahora = datetime.now(D["TZ"])
    crudo = (args.get("fecha_hora_iso") or "").strip()
    motivo = (args.get("motivo") or "").strip()
    try:
        dt = datetime.fromisoformat(crudo.replace("Z", "+00:00"))
        dt = dt.astimezone(D["TZ"]) if dt.tzinfo else D["TZ"].localize(dt)
    except Exception:
        return {"success": False, "error": "fecha_hora_iso no es una fecha válida",
                "frase": "Te llamo yo cuando quieras — ¿qué día y a qué hora te sirve?"}
    delta = dt - ahora
    if delta < HORIZONTE_MIN:
        dt = ahora + timedelta(minutes=10)
    elif delta > HORIZONTE_MAX:
        return {"success": False, "error": "fuera del horizonte de 60 días",
                "frase": "Eso queda muy lejos para agendarlo ahora; te llamo antes y lo vemos."}
    dt, movido = _ajustar(dt)
    horas = max(1, int(round((dt - ahora).total_seconds() / 3600)))
    bucket = _bucket(horas)
    cid = _contacto(args)
    escrito = {}
    if cid:
        escrito["elena_callback_time"] = bool(D["_update_contact_custom_field"](cid, "elena_callback_time", hora_para_ghl(dt)))
        escrito["elena_callback_hours"] = bool(D["_update_contact_custom_field"](cid, "elena_callback_hours", str(bucket)))
        escrito["nota"] = bool(D["_add_note_to_contact"](
            cid, f"Pidió que la llamemos {cuando_en_palabras(dt, ahora)} ({dt.strftime('%Y-%m-%d %H:%M')} ET)"
                 + (f" · motivo: {motivo}" if motivo else "")
                 + (" · ajustado al horario de la clínica" if movido else "")))
    if promete_hora():
        frase = f"Perfecto, te llamo {cuando_en_palabras(dt, ahora)}."
    else:
        frase = (f"Perfecto, te llamamos {dia_en_palabras(dt, ahora)}."
                 if bucket != 2 else "Perfecto, te llamamos en un rato.")
    _log("programar_llamada", {"contacto": cid, "pedido": crudo, "guardado": dt.isoformat(),
                               "movido": movido, "bucket": bucket, "promete_hora": promete_hora(),
                               "escrito": escrito, "call_id": args.get("_call_id", "")})
    return {"success": True, "callbackTime": dt.isoformat(), "hours": bucket, "ajustado": movido,
            "cuando": cuando_en_palabras(dt, ahora), "frase": frase, "message": frase}


# ── 4 · confirmar_horario (habilita la reserva) ───────────────────────────────────────────────
def handle_confirmar_horario(args):
    start = (args.get("startTime") or "").strip()
    call_id = args.get("_call_id", "")
    ofrecidos = _slots.get(call_id) or []
    if ofrecidos and start not in ofrecidos:
        return {"success": False, "error": "ese horario no está entre los que ofrecí",
                "frase": "Déjame revisar la agenda otra vez para darte una hora que sí tengo."}
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(D["TZ"])
    except Exception:
        return {"success": False, "error": "startTime inválido",
                "frase": "¿Me repites qué día y hora te sirve?"}
    _poner(_confirmado, call_id, start)
    ahora = datetime.now(D["TZ"])
    frase = f"Entonces te dejo {cuando_en_palabras(dt, ahora)}. ¿Lo confirmo?"
    _log("confirmar_horario", {"call_id": call_id, "startTime": start})
    return {"success": True, "startTime": start, "cuando": cuando_en_palabras(dt, ahora),
            "frase": frase, "message": frase}


# ── 5 · contexto_paciente (se ejecuta al descolgar, en silencio) ───────────────────────────────
TAGS_LISTA_SMS = ("radiesse_sms", "sms_junio", "lista_sms")


def handle_contexto_paciente(args):
    cid = _contacto(args)
    if not cid:
        return {"encontrada": False, "segmento": "desconocido"}
    try:
        c = (D["ghl_v2_get"](f"/contacts/{cid}", contacts_version=True) or {}).get("contact") or {}
    except Exception as e:
        return {"encontrada": False, "error": str(e)}
    tags = [t.lower() for t in (c.get("tags") or [])]
    campos = {}
    for f in (c.get("customFields") or []):
        k = f.get("key") or f.get("id") or ""
        campos[k.replace("contact.", "")] = f.get("value") or f.get("field_value")
    creado = c.get("dateAdded") or ""
    dias = None
    try:
        dias = (datetime.now(D["TZ"]) - datetime.fromisoformat(creado.replace("Z", "+00:00")).astimezone(D["TZ"])).days
    except Exception:
        pass
    cita = None
    try:
        ev = (D["ghl_v2_get"](f"/contacts/{cid}/appointments", contacts_version=True) or {}).get("events") or []
        ahora = datetime.now(D["TZ"])
        futuras = []
        for a in ev:
            try:
                st = datetime.fromisoformat((a.get("startTime") or "").replace("Z", "+00:00")).astimezone(D["TZ"])
                if st > ahora and (a.get("appointmentStatus") or "").lower() not in ("cancelled",):
                    futuras.append((st, a))
            except Exception:
                continue
        if futuras:
            st, a = sorted(futuras, key=lambda x: x[0])[0]
            cita = {"cuando": cuando_en_palabras(st, ahora), "startTime": st.isoformat(),
                    "estado": a.get("appointmentStatus")}
    except Exception:
        pass
    if cita:
        segmento = "con_cita"
    elif any(t in tags for t in TAGS_LISTA_SMS):
        segmento = "lista_sms"
    elif dias is not None and dias <= 30:
        segmento = "nuevo"
    elif dias is not None:
        segmento = "base_vieja"
    else:
        segmento = "desconocido"
    if args.get("_entrante") and not cita:
        segmento = "entrante"
    ctx = {"encontrada": True, "nombre": (c.get("firstName") or "").strip(), "segmento": segmento,
           "dias_desde_lead": dias, "cita_futura": cita,
           "ultimo_outcome": campos.get("elena_last_outcome"),
           "callback_pedido": campos.get("elena_callback_time"),
           "idioma_previo": campos.get("elena_language"),
           "no_contactar": "no_contactar" in tags,
           "tratamiento_origen": next((t for t in tags if any(x in t for x in (
               "botox", "filler", "radiesse", "acne", "cicatri", "rejuven", "depilacion", "lhr"))), None)}
    _log("contexto_paciente", {"contacto": cid, "segmento": segmento, "cita": bool(cita),
                               "no_contactar": ctx["no_contactar"]})
    return ctx


# ── 6 · enviar_info (APAGADO hasta que Juan apruebe la plantilla) ──────────────────────────────
DIRECCION_VOZ = "cuatro seis cuatro nueve, Ponce de León, suite tres cero dos, Coral Gables"
PLANTILLAS = {
    "direccion": ("Hola{nombre}, soy Elena de Laser Place Miami. Estamos en 4649 Ponce De Leon Blvd, "
                  "Suite 302, Coral Gables FL 33146. Horario: lunes a viernes de 10 a 6 y sabados de 10 a 2."),
    "horarios": ("Hola{nombre}, soy Elena de Laser Place Miami. Nuestro horario es lunes a viernes de 10 a 6 "
                 "y sabados de 10 a 2. Cuando quieras, te agendamos tu evaluacion gratis."),
}


def handle_enviar_info(args):
    tipo = (args.get("tipo") or "direccion").strip().lower()
    if tipo not in PLANTILLAS:
        tipo = "direccion"
    cid = _contacto(args)
    # Apagado, la frase NO promete un mensaje: lo dice en voz alta. Prometer un envío que no ocurre es
    # exactamente lo que mata llamadas («te mando la dirección por mensaje» = 10 llamadas perdidas,
    # DATOS-DISENO §8) y además sería mentir. Encendido, sí lo anuncia.
    dicho = (f"Estamos en {DIRECCION_VOZ}. Se aparca en la calle y nosotros te validamos el parqueo."
             if tipo == "direccion" else
             "Abrimos de lunes a viernes de diez a seis, y sábados de diez a dos.")
    frase = ((f"Te lo mando ahora por mensaje. {dicho}") if enviar_info_activo() else dicho)
    if not enviar_info_activo():
        _log("enviar_info_apagado", {"contacto": cid, "tipo": tipo,
                                     "nota": "ELENA_ENVIAR_INFO=0 — la plantilla espera el OK de Juan"})
        return {"success": False, "enviado": False, "motivo": "plantilla pendiente de aprobación de Juan",
                "frase": frase, "message": frase}
    enviado = False
    if cid:
        nombre = ""
        try:
            c = (D["ghl_v2_get"](f"/contacts/{cid}", contacts_version=True) or {}).get("contact") or {}
            nombre = (" " + (c.get("firstName") or "").strip()).rstrip()
        except Exception:
            pass
        try:
            r = D["ghl_v2_post"]("/conversations/messages",
                                 {"type": "SMS", "contactId": cid,
                                  "message": PLANTILLAS[tipo].format(nombre=nombre)})
            enviado = bool(r)
        except Exception as e:
            _log("enviar_info_error", {"error": str(e), "contacto": cid})
    _log("enviar_info", {"contacto": cid, "tipo": tipo, "enviado": enviado})
    return {"success": True, "enviado": enviado, "frase": frase, "message": frase}


# ── guardas ───────────────────────────────────────────────────────────────────────────────────
TOOLS_ACCION = ("create_booking", "schedule_callback", "programar_llamada", "pedir_persona",
                "request_human_handoff", "enviar_info", "registrar_baja")
PALABRAS_MIN = 3


def hubo_turno_de_paciente(message, call_id):
    """¿Habló la paciente al menos un turno de ≥3 palabras? Si el payload no trae transcripto,
    devuelve None = «no se puede saber» (y entonces NO se bloquea: un falso bloqueo cuesta una cita)."""
    if _turnos.get(call_id):
        return True
    msgs = ((message.get("artifact") or {}).get("messages")
            or ((message.get("call") or {}).get("artifact") or {}).get("messages") or [])
    if not msgs:
        return None
    for m in msgs:
        if (m.get("role") or "").lower() in ("user", "customer", "human"):
            texto = (m.get("message") or m.get("content") or "")
            if len(re.findall(r"\w+", texto)) >= PALABRAS_MIN:
                _poner(_turnos, call_id, True)
                return True
    return False


def gate_buzon(fn_name, message, call_id, arm):
    """(permitido, motivo). Se aplica a las tools de ACCIÓN. `pedir_persona` SIEMPRE (protege a recepción
    de una tarea por cada buzón); el resto solo en el brazo B. Las lecturas nunca se bloquean."""
    if fn_name not in TOOLS_ACCION:
        return True, ""
    siempre = fn_name in ("pedir_persona", "request_human_handoff")
    if not (siempre or guardas_activas(arm)):
        return True, ""
    hablo = hubo_turno_de_paciente(message, call_id)
    if hablo is False:
        return False, "no_permitido_en_buzon"
    return True, ("sin_datos_de_transcripto" if hablo is None else "")


def gate_reserva(args, call_id):
    """(permitido, motivo) para `create_booking` en el brazo B: el horario tiene que ser uno de los
    ofrecidos en ESTA llamada y haber pasado por `confirmar_horario`."""
    start = (args.get("startTime") or args.get("start") or "").strip()
    ofrecidos = _slots.get(call_id) or []
    if ofrecidos and start and start not in ofrecidos:
        return False, "horario_no_ofrecido"
    if _confirmado.get(call_id) != start:
        return False, "sin_confirmar_horario"
    return True, ""


TOOLS = {
    "registrar_baja": handle_registrar_baja,
    "pedir_persona": handle_pedir_persona,
    "request_human_handoff": handle_pedir_persona,   # arregla la tool viva que no tenía handler
    "programar_llamada": handle_programar_llamada,
    "confirmar_horario": handle_confirmar_horario,
    "contexto_paciente": handle_contexto_paciente,
    "enviar_info": handle_enviar_info,
}
