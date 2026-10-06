"""test_v18.py — varas de v18. Cada guarda lleva CONTROL POSITIVO (LL-182): se prueba que BLOQUEA lo que
debe bloquear, no solo que deja pasar lo bueno. Sin red: GHL va con dobles.
"""
import os
import re
import sys
import pathlib
from datetime import datetime, timedelta

import pytest
import pytz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import v18  # noqa: E402

TZ = pytz.timezone("America/New_York")
DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]


class GHLFalso:
    """Doble de GHL: registra lo que se le pide y no sale a la red."""

    def __init__(self, contacto=None, citas=None):
        self.tags, self.campos, self.notas, self.puts, self.posts = [], {}, [], [], []
        self.contacto = contacto or {}
        self.citas = citas or []

    def get(self, path, params=None, contacts_version=False):
        if path.endswith("/appointments"):
            return {"events": self.citas}
        return {"contact": self.contacto}

    def post(self, path, data, contacts_version=False):
        self.posts.append((path, data))
        return {"ok": True}

    def put(self, path, data):
        self.puts.append((path, data))
        return {"ok": True}

    def tag(self, cid, tag):
        self.tags.append(tag)
        return True

    def campo(self, cid, k, v):
        self.campos[k] = v
        return True

    def nota(self, cid, body):
        self.notas.append(body)
        return True


@pytest.fixture(autouse=True)
def limpio():
    v18._slots.clear(); v18._confirmado.clear(); v18._turnos.clear()
    os.environ.pop("ELENA_PROMETE_HORA", None)
    os.environ.pop("ELENA_ENVIAR_INFO", None)
    yield


def cablear(g, encontrada=True):
    v18.wire(TZ=TZ, DAYS_ES=DAYS_ES, MONTHS_ES=MONTHS_ES,
             ghl_v2_get=g.get, ghl_v2_post=g.post, ghl_v2_put=g.put,
             handle_get_contact=lambda a: {"found": encontrada, "contactId": "C1"},
             _add_tag_to_contact=g.tag, _update_contact_custom_field=g.campo,
             _add_note_to_contact=g.nota)
    return g


# ── hora en palabras ──────────────────────────────────────────────────────────────────────────
def test_hora_en_palabras():
    cablear(GHLFalso())
    assert v18.hora_en_palabras(TZ.localize(datetime(2026, 10, 6, 10, 0))) == "diez de la mañana"
    assert v18.hora_en_palabras(TZ.localize(datetime(2026, 10, 6, 17, 30))) == "cinco y media de la tarde"
    assert v18.hora_en_palabras(TZ.localize(datetime(2026, 10, 6, 12, 15))) == "doce y cuarto de la tarde"


# ── gate de reserva (control positivo: sin confirmar NO se reserva) ────────────────────────────
def test_reserva_sin_confirmar_se_bloquea():
    cablear(GHLFalso())
    v18.recordar_slots("call1", {"slots": ["2026-10-07T10:00:00-04:00"]})
    ok, motivo = v18.gate_reserva({"startTime": "2026-10-07T10:00:00-04:00"}, "call1")
    assert ok is False and motivo == "sin_confirmar_horario"


def test_reserva_con_confirmacion_pasa():
    cablear(GHLFalso())
    v18.recordar_slots("call1", {"slots": ["2026-10-07T10:00:00-04:00"]})
    v18.handle_confirmar_horario({"startTime": "2026-10-07T10:00:00-04:00", "_call_id": "call1"})
    ok, motivo = v18.gate_reserva({"startTime": "2026-10-07T10:00:00-04:00"}, "call1")
    assert ok is True and motivo == ""


def test_reserva_en_horario_no_ofrecido_se_bloquea():
    cablear(GHLFalso())
    v18.recordar_slots("call1", {"slots": ["2026-10-07T10:00:00-04:00"]})
    ok, motivo = v18.gate_reserva({"startTime": "2026-10-07T16:00:00-04:00"}, "call1")
    assert ok is False and motivo == "horario_no_ofrecido"


def test_confirmar_horario_rechaza_el_que_no_se_ofrecio():
    cablear(GHLFalso())
    v18.recordar_slots("call1", {"slots": ["2026-10-07T10:00:00-04:00"]})
    r = v18.handle_confirmar_horario({"startTime": "2026-10-07T20:00:00-04:00", "_call_id": "call1"})
    assert r["success"] is False and "revisar la agenda" in r["frase"]


# ── gate de buzón ─────────────────────────────────────────────────────────────────────────────
MSG_BUZON = {"artifact": {"messages": [{"role": "bot", "message": "Hola, habla Elena"},
                                       {"role": "user", "message": "sí"}]}}
MSG_HABLA = {"artifact": {"messages": [{"role": "bot", "message": "Hola"},
                                       {"role": "user", "message": "sí mira cuéntame del precio"}]}}


def test_buzon_bloquea_accion_en_brazo_b():
    cablear(GHLFalso())
    ok, motivo = v18.gate_buzon("create_booking", MSG_BUZON, "c1", "b")
    assert ok is False and motivo == "no_permitido_en_buzon"


def test_buzon_deja_pasar_si_la_paciente_hablo():
    cablear(GHLFalso())
    ok, _ = v18.gate_buzon("create_booking", MSG_HABLA, "c2", "b")
    assert ok is True


def test_lectura_nunca_se_bloquea():
    cablear(GHLFalso())
    for tool in ("contexto_paciente", "get_contact", "check_availability"):
        assert v18.gate_buzon(tool, MSG_BUZON, "c3", "b")[0] is True


def test_sin_transcripto_no_bloquea_pero_lo_dice():
    cablear(GHLFalso())
    ok, motivo = v18.gate_buzon("create_booking", {}, "c4", "b")
    assert ok is True and motivo == "sin_datos_de_transcripto"


def test_produccion_arm_a_no_cambia_pero_pedir_persona_si():
    """El brazo A queda EXACTAMENTE como v17.57 para la reserva; `pedir_persona` se protege siempre."""
    cablear(GHLFalso())
    assert v18.gate_buzon("create_booking", MSG_BUZON, "c5", "a")[0] is True
    assert v18.gate_buzon("pedir_persona", MSG_BUZON, "c5", "a")[0] is False
    assert v18.gate_buzon("request_human_handoff", MSG_BUZON, "c5", "a")[0] is False


# ── programar_llamada ─────────────────────────────────────────────────────────────────────────
def test_programar_en_20_minutos():
    g = cablear(GHLFalso())
    cuando = datetime.now(TZ) + timedelta(minutes=20)
    if cuando.hour < 8 or cuando.hour >= 20 or cuando.weekday() == 6:
        pytest.skip("la hora de la corrida cae fuera de la ventana; cubierto por los tests de ajuste")
    r = v18.handle_programar_llamada({"fecha_hora_iso": cuando.isoformat(), "callerPhone": "+1786"})
    assert r["success"] and r["hours"] == 2 and r["ajustado"] is False
    assert g.campos["elena_callback_time"].startswith(cuando.strftime("%Y-%m-%d %H"))
    assert g.campos["elena_callback_hours"] == "2"


def test_programar_de_noche_se_mueve_a_la_manana():
    g = cablear(GHLFalso())
    manana_11pm = (datetime.now(TZ) + timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)
    r = v18.handle_programar_llamada({"fecha_hora_iso": manana_11pm.isoformat(), "callerPhone": "+1786"})
    assert r["success"] and r["ajustado"] is True
    assert g.campos["elena_callback_time"].endswith(" 10:00:00")
    assert any("ajustado al horario de la clínica" in n for n in g.notas)


def test_programar_en_domingo_se_mueve_al_lunes():
    g = cablear(GHLFalso())
    d = datetime.now(TZ) + timedelta(days=1)
    while d.weekday() != 6:
        d += timedelta(days=1)
    r = v18.handle_programar_llamada({"fecha_hora_iso": d.replace(hour=11, minute=0).isoformat(),
                                      "callerPhone": "+1786"})
    guardado = datetime.fromisoformat(g.campos["elena_callback_time"])
    assert r["ajustado"] is True and guardado.weekday() == 0


def test_programar_mas_de_60_dias_se_rechaza():
    cablear(GHLFalso())
    r = v18.handle_programar_llamada({"fecha_hora_iso": (datetime.now(TZ) + timedelta(days=90)).isoformat()})
    assert r["success"] is False and "muy lejos" in r["frase"]


def test_fecha_basura_no_rompe():
    cablear(GHLFalso())
    r = v18.handle_programar_llamada({"fecha_hora_iso": "el jueves"})
    assert r["success"] is False and "qué día y a qué hora" in r["frase"]


def test_la_frase_promete_la_hora_por_defecto_y_se_puede_apagar():
    """v18.6: F3a está en los 6 workflows (GHL espera hasta la hora guardada), así que la promesa es verdad y va ON
    por defecto. CONTROL POSITIVO: con ELENA_PROMETE_HORA=0 Elena vuelve a decir solo la franja."""
    cablear(GHLFalso())
    manana_5pm = (datetime.now(TZ) + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
    r = v18.handle_programar_llamada({"fecha_hora_iso": manana_5pm.isoformat(), "callerPhone": "+1786"})
    assert "a las cinco de la tarde" in r["frase"], r["frase"]
    os.environ["ELENA_PROMETE_HORA"] = "0"
    r2 = v18.handle_programar_llamada({"fecha_hora_iso": manana_5pm.isoformat(), "callerPhone": "+1786"})
    assert "a las" not in r2["frase"] and "mañana" in r2["frase"], r2["frase"]


@pytest.mark.parametrize("minuto,esperado", [(5, "cinco"), (12, "doce"), (16, "dieciséis"), (20, "veinte"), (28, "veintiocho"),
                                             (35, "treinta y cinco"), (41, "cuarenta y uno"), (59, "cincuenta y nueve")])
def test_minutos_en_palabras(minuto, esperado):
    assert v18.minutos_en_palabras(minuto) == esperado
    assert v18.hora_en_palabras(TZ.localize(datetime(2026, 10, 6, 15, minuto))) == f"tres y {esperado} de la tarde"


def test_hora_en_palabras_nunca_lleva_cifras():
    for m in range(60):
        assert not re.search(r"\d", v18.hora_en_palabras(TZ.localize(datetime(2026, 10, 6, 9, m)))), m


def test_schedule_callback_dice_la_hora_en_palabras(monkeypatch):
    """La Elena de hoy (brazo A) leía «3:05 pm» como «tres o cinco» (llamada real de Juan, 2026-10-06 13:05)."""
    import app as flask_app
    monkeypatch.setattr(flask_app, "handle_get_contact", lambda a: {"found": True, "contactId": "C1"})
    monkeypatch.setattr(flask_app, "_update_contact_custom_field", lambda cid, k, v: True)
    r = flask_app.handle_schedule_callback({"hours": 2, "callerPhone": "+17865550100"})
    assert not re.search(r"\d", r["callbackHuman"]), r["callbackHuman"]        # ni «3:05» ni «6 de octubre»
    assert "pm" not in r["callbackHuman"] and "am" not in r["callbackHuman"]
    assert r["message"] == f"Perfecto, te llamo {r['callbackHuman']}."
    assert r["callbackHuman"].startswith(("hoy ", "mañana ")) and " a la" in r["callbackHuman"]


# ── elena_callback_time en el formato que GHL entiende (F3a, medido 2026-10-06) ───────────────
# Con zona escrita («-0400» / «-04:00») el Wait dinámico de GHL esperó 4 h de más; sin zona acertó al minuto.
_ZONA = re.compile(r"(?:[+-]\d{2}:?\d{2}|Z)$")


def _tiene_zona(valor):
    return bool(_ZONA.search(valor.strip()))


@pytest.mark.parametrize("valor", ["2026-10-06T11:07:00-0400", "2026-10-06T11:08:00-04:00",
                                   "2026-10-06T15:07:00+00:00", "2026-10-06T15:07:00Z"])
def test_control_positivo_el_detector_de_zona_caza(valor):
    """Los formatos que GHL desplazó 4 h tienen que delatarse; si no, los tests de abajo no prueban nada."""
    assert _tiene_zona(valor)


def test_control_negativo_la_hora_local_no_tiene_zona():
    assert not _tiene_zona("2026-10-06 11:09:00")


def test_programar_escribe_hora_local_sin_zona():
    g = cablear(GHLFalso())
    d = datetime.now(TZ) + timedelta(days=1)
    while d.weekday() in (5, 6):   # entre semana: 15:30 cae dentro del horario y no se ajusta
        d += timedelta(days=1)
    pedido = d.replace(hour=15, minute=30, second=0, microsecond=0)
    r = v18.handle_programar_llamada({"fecha_hora_iso": pedido.isoformat(), "callerPhone": "+1786"})
    guardado = g.campos["elena_callback_time"]
    assert r["success"] and not _tiene_zona(guardado), guardado
    assert guardado == pedido.strftime("%Y-%m-%d 15:30:00")


def test_hora_para_ghl_pasa_a_miami_con_el_cambio_de_horario():
    cablear(GHLFalso())
    assert v18.hora_para_ghl(pytz.utc.localize(datetime(2026, 10, 6, 15, 9))) == "2026-10-06 11:09:00"  # EDT
    assert v18.hora_para_ghl(pytz.utc.localize(datetime(2026, 12, 1, 15, 0))) == "2026-12-01 10:00:00"  # EST


def test_schedule_callback_de_produccion_tambien_escribe_sin_zona(monkeypatch):
    """La tool viva del brazo A escribe el mismo campo: misma regla."""
    import app as flask_app
    escritos = {}
    monkeypatch.setattr(flask_app, "handle_get_contact", lambda a: {"found": True, "contactId": "C1"})
    monkeypatch.setattr(flask_app, "_update_contact_custom_field",
                        lambda cid, k, v: escritos.__setitem__(k, v) or True)
    r = flask_app.handle_schedule_callback({"hours": 4, "callerPhone": "+17865550100"})
    guardado = escritos["elena_callback_time"]
    assert r["success"] and not _tiene_zona(guardado), guardado
    esperado = (datetime.now(TZ) + timedelta(hours=4)).replace(tzinfo=None)
    assert abs((datetime.strptime(guardado, "%Y-%m-%d %H:%M:%S") - esperado).total_seconds()) < 120


# ── fin de llamada: «llamar luego» deja SIEMPRE una hora fresca (F3a, v18.5) ──────────────────
def test_fin_de_llamada_usa_la_hora_de_schedule_callback():
    import app as flask_app
    assert flask_app._callback_time_para_ghl("2026-10-07T15:30:00-0400", 12) == "2026-10-07 15:30:00"


def test_fin_de_llamada_usa_la_hora_de_programar_llamada():
    import app as flask_app
    assert flask_app._callback_time_para_ghl("2026-10-07T15:30:00-04:00", 12) == "2026-10-07 15:30:00"


@pytest.mark.parametrize("sin_tool", ["", "basura", "2026-10-07 15:30:00"])
def test_fin_de_llamada_sin_tool_valida_pone_ahora_mas_horas(sin_tool):
    """Por palabras clave no hay tool: antes el campo quedaba vacío o viejo y GHL habría llamado al instante."""
    import app as flask_app
    guardado = flask_app._callback_time_para_ghl(sin_tool, 4)
    assert not _tiene_zona(guardado), guardado
    esperado = (datetime.now(TZ) + timedelta(hours=4)).replace(tzinfo=None)
    assert abs((datetime.strptime(guardado, "%Y-%m-%d %H:%M:%S") - esperado).total_seconds()) < 120


def test_fin_de_llamada_escribe_la_hora_en_la_rama_llamar_luego():
    """Guarda de estructura: el bloque que escribe elena_callback_hours también escribe elena_callback_time.
    Control positivo: quitando esa escritura del código (como estaba hasta v18.4) este test falla."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    i = src.index('if outcome == "llamar_luego" and callback_hours_confirmed > 0:')
    bloque = src[i:i + 600]
    assert '"elena_callback_hours"' in bloque and '"elena_callback_time"' in bloque
    assert "_callback_time_para_ghl(callback_time_tool" in bloque


# ── registrar_baja ────────────────────────────────────────────────────────────────────────────
def test_baja_no_llamar_aplica_tag_dnd_y_nota():
    g = cablear(GHLFalso())
    r = v18.handle_registrar_baja({"motivo": "no_llamar", "callerPhone": "+1786", "_call_id": "x1"})
    assert r["success"] and "no te volvemos a llamar" in r["frase"]
    assert "no_contactar" in g.tags and "baja_no_llamar" in g.tags
    assert g.puts and g.puts[0][1] == {"dnd": True}
    assert any("BAJA registrada por Elena" in n for n in g.notas)


def test_baja_no_interesa_no_pone_dnd():
    g = cablear(GHLFalso())
    v18.handle_registrar_baja({"motivo": "no_interesa", "callerPhone": "+1786"})
    assert "no_contactar" in g.tags and not g.puts


def test_baja_sin_ficha_no_miente():
    cablear(GHLFalso(), encontrada=False)
    r = v18.handle_registrar_baja({"motivo": "no_llamar", "callerPhone": "+1786"})
    assert r["success"] is False and "No encontré tu ficha" in r["message"]


# ── pedir_persona ─────────────────────────────────────────────────────────────────────────────
def test_pedir_persona_crea_tarea_y_nota():
    g = cablear(GHLFalso())
    r = v18.handle_pedir_persona({"motivo": "quiere hablar con Laury", "cuando": "mañana en la mañana",
                                  "callerPhone": "+1786", "_call_id": "x2"})
    assert r["success"] and r["tarea_creada"] is True
    assert g.posts[0][0] == "/contacts/C1/tasks"
    assert any("PIDIÓ HABLAR CON UNA PERSONA" in n for n in g.notas)
    assert "una persona" in r["frase"]


def test_alias_de_la_tool_viva():
    """request_human_handoff está registrada en Vapi y no tenía handler: el alias la arregla."""
    assert v18.TOOLS["request_human_handoff"] is v18.handle_pedir_persona


# ── contexto_paciente ─────────────────────────────────────────────────────────────────────────
def test_contexto_segmento_nuevo():
    hace_3d = (datetime.now(TZ) - timedelta(days=3)).isoformat()
    g = cablear(GHLFalso(contacto={"firstName": "Ana", "tags": ["botox_lead"], "dateAdded": hace_3d,
                                   "customFields": [{"key": "contact.elena_language", "value": "es"}]}))
    c = v18.handle_contexto_paciente({"callerPhone": "+1786"})
    assert c["segmento"] == "nuevo" and c["nombre"] == "Ana"
    assert c["tratamiento_origen"] == "botox_lead" and c["idioma_previo"] == "es"


def test_contexto_segmento_base_vieja_y_no_contactar():
    hace_200d = (datetime.now(TZ) - timedelta(days=200)).isoformat()
    cablear(GHLFalso(contacto={"tags": ["no_contactar"], "dateAdded": hace_200d, "customFields": []}))
    c = v18.handle_contexto_paciente({"callerPhone": "+1786"})
    assert c["segmento"] == "base_vieja" and c["no_contactar"] is True


def test_contexto_con_cita_futura_manda_sobre_el_resto():
    manana = (datetime.now(TZ) + timedelta(days=1)).replace(hour=11, minute=0)
    g = cablear(GHLFalso(contacto={"tags": ["lista_sms"], "dateAdded": datetime.now(TZ).isoformat(),
                                   "customFields": []},
                         citas=[{"startTime": manana.isoformat(), "appointmentStatus": "confirmed"}]))
    c = v18.handle_contexto_paciente({"callerPhone": "+1786"})
    assert c["segmento"] == "con_cita" and c["cita_futura"]["estado"] == "confirmed"
    assert "mañana a las once" in c["cita_futura"]["cuando"]


def test_contexto_ignora_cita_cancelada():
    manana = (datetime.now(TZ) + timedelta(days=1)).replace(hour=11, minute=0)
    cablear(GHLFalso(contacto={"tags": [], "dateAdded": datetime.now(TZ).isoformat(), "customFields": []},
                     citas=[{"startTime": manana.isoformat(), "appointmentStatus": "cancelled"}]))
    c = v18.handle_contexto_paciente({"callerPhone": "+1786"})
    assert c["cita_futura"] is None and c["segmento"] == "nuevo"


# ── enviar_info (control positivo: apagado NO manda nada) ──────────────────────────────────────
def test_enviar_info_apagado_no_manda():
    g = cablear(GHLFalso())
    r = v18.handle_enviar_info({"tipo": "direccion", "callerPhone": "+1786"})
    assert r["enviado"] is False and g.posts == []
    assert "Ponce de León" in r["frase"]


def test_enviar_info_encendido_manda_una_sola_vez():
    os.environ["ELENA_ENVIAR_INFO"] = "1"
    g = cablear(GHLFalso(contacto={"firstName": "Ana"}))
    r = v18.handle_enviar_info({"tipo": "direccion", "callerPhone": "+1786"})
    assert r["enviado"] is True and len(g.posts) == 1
    assert g.posts[0][0] == "/conversations/messages"
    assert "Ana" in g.posts[0][1]["message"] and "Suite 302" in g.posts[0][1]["message"]


def test_articulo_de_la_hora():
    """«a la una» (no «a las una»): lo cazó el banco de pruebas en la primera llamada real."""
    cablear(GHLFalso())
    assert v18.a_las(TZ.localize(datetime(2026, 10, 6, 13, 0))) == "a la una de la tarde"
    assert v18.a_las(TZ.localize(datetime(2026, 10, 6, 13, 30))) == "a la una y media de la tarde"
    assert v18.a_las(TZ.localize(datetime(2026, 10, 6, 1, 0))) == "a la una de la mañana"
    assert v18.a_las(TZ.localize(datetime(2026, 10, 6, 10, 0))) == "a las diez de la mañana"
