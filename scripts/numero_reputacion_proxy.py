#!/usr/bin/env python3
"""
Monitor PROXY de reputación de números de Elena Voice (2026-06-16).
No lee la etiqueta exacta (eso requiere Caller ID Reputation / Hiya API de pago).
En su lugar vigila el SÍNTOMA: cuando un número cae en "Spam Likely", la gente no
contesta → su connect-rate se desploma. Eso es la alarma temprana.

Cada semana (cron):
  1. Lee Vapi /call de los últimos 7 días, agrupa por número saliente (phoneNumberId).
  2. Calcula connect-rate (contestadas / total) y volumen por número.
  3. Guarda el snapshot en un historial JSON (construye su propia línea base).
  4. Si un número con volumen suficiente cae fuerte vs su promedio histórico → alerta a Juan.

Capa GRATIS. Cuando se contrate la API exacta (Caller ID Reputation), este monitor
se actualiza para leer la etiqueta real en vez del proxy.

Uso:
  python3 numero_reputacion_proxy.py            # corre + alerta si aplica
  python3 numero_reputacion_proxy.py --dry-run  # imprime, NO alerta
"""
import json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone

VAPI_KEY = open("/root/.secrets/vapi_key").read().strip()
TG_TOKEN = open("/root/.secrets/telegram_bot_token").read().strip()      # @claude_juan_bot
TG_CHAT = open("/root/.secrets/telegram_chat_id_juan").read().strip()     # 7962087583
HIST = "/root/stephanee/logs/numero_reputacion_historial.json"
MIN_VOL = 15          # mínimo de llamadas en la semana para que el número cuente
DROP_FACTOR = 0.6     # alerta si connect-rate < 60% de su promedio histórico
CONNECT_SECS = 15     # una llamada "conecta" si dura >= 15s (conversación real, no buzón)

def _vapi_get(path):
    r = urllib.request.Request(f"https://api.vapi.ai/{path}",
        headers={"Authorization": f"Bearer {VAPI_KEY}", "Accept": "application/json", "User-Agent": "curl/8.0"})
    d = json.load(urllib.request.urlopen(r, timeout=30))
    return d if isinstance(d, list) else d.get("results", [])

def vapi_calls():
    return _vapi_get("call?limit=1000")

def vapi_number_map():
    """phoneNumberId -> número legible (+1...)"""
    return {n.get("id"): n.get("number", n.get("id")) for n in _vapi_get("phone-number?limit=50")}

def tg(text):
    data = json.dumps({"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}).encode()
    r = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=data, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=15)).get("ok")

# 1. Agrupar llamadas de la semana por número saliente
since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
nummap = vapi_number_map()
calls = vapi_calls()
per = {}   # phoneNumberId -> {"num": str, "total": n, "connected": n}
for c in calls:
    if (c.get("startedAt") or "") < since:
        continue
    pid = c.get("phoneNumberId")
    if not pid:
        continue
    d = per.setdefault(pid, {"num": nummap.get(pid, pid), "total": 0, "connected": 0})
    d["total"] += 1
    # duración: si Vapi da startedAt y endedAt
    try:
        st = datetime.fromisoformat((c.get("startedAt") or "").replace("Z", "+00:00"))
        en = datetime.fromisoformat((c.get("endedAt") or "").replace("Z", "+00:00"))
        if (en - st).total_seconds() >= CONNECT_SECS:
            d["connected"] += 1
    except Exception:
        pass

# 2. Calcular connect-rate de esta semana
week = {}
for pid, d in per.items():
    if d["total"] > 0:
        week[pid] = {"num": d["num"], "total": d["total"],
                     "connect_rate": round(100 * d["connected"] / d["total"], 1)}

# 3. Cargar historial + detectar caídas
hist = {}
if os.path.exists(HIST):
    try: hist = json.load(open(HIST))
    except Exception: hist = {}

stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
alerts = []
for pid, w in week.items():
    h = hist.get(pid, {"snapshots": []})
    # La línea base solo cuenta semanas con volumen real, y hacen falta 3 (2026-10-28): el ≈5-oct avisó
    # «posible SPAM» comparando 49% contra un «promedio» de 100% hecho con 2 semanas de 1 llamada cada una.
    prev = [s["connect_rate"] for s in h["snapshots"] if s.get("total", 0) >= MIN_VOL]
    avg = sum(prev) / len(prev) if len(prev) >= 3 else None
    flag = (avg is not None and w["total"] >= MIN_VOL and avg > 0 and w["connect_rate"] < DROP_FACTOR * avg)
    if flag:
        alerts.append(f"{w['num']}: connect {w['connect_rate']}% (prom {avg:.0f}%), {w['total']} llamadas")
    # append snapshot (mantener últimas 12 semanas)
    h["num"] = w["num"]
    h["snapshots"] = (h.get("snapshots", []) + [{"date": stamp, "connect_rate": w["connect_rate"], "total": w["total"]}])[-12:]
    hist[pid] = h

if "--dry-run" in sys.argv:
    print(f"Semana {stamp} — connect-rate por número:")
    for pid, w in sorted(week.items(), key=lambda x: -x[1]["total"]):
        print(f"  {w['num']:16} connect {w['connect_rate']:5}%  ({w['total']} llamadas)")
    print(f"\nAlertas que se dispararían: {len(alerts)}")
    for a in alerts: print(f"  🚨 {a}")
    print(f"\n(historial NO guardado en dry-run; {len(hist)} números trackeados)")
    sys.exit(0)

# 4. Guardar historial + alertar
json.dump(hist, open(HIST, "w"), indent=2)
if alerts:
    msg = ("🚨 <b>Reputación de números — posible SPAM</b>\n"
           "Estos números cayeron fuerte en connect-rate (síntoma de etiqueta spam). "
           "Considera rotarlos y revisar su estado:\n\n" + "\n".join(f"• {a}" for a in alerts)
           + "\n\n<i>Proxy de connect-rate. Para etiqueta exacta: Caller ID Reputation API.</i>")
    print("alerta enviada:", tg(msg))
else:
    print(f"OK — {len(week)} números monitoreados, sin caídas anómalas esta semana.")
