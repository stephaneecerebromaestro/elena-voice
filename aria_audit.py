"""
ARIA — Auditoría y Revisión Inteligente Automatizada
Sistema de auditoría automática de llamadas para Elena AI Voice Agent
Versión: 2.0.0 — 30 marzo 2026

Arquitectura:
  TIEMPO REAL (por llamada):
    1. Vapi dispara webhook end-of-call → /aria/vapi/end-of-call en app.py
    2. ARIA audita con Claude (30-60s post-llamada)
    3. Si hay discrepancia → Telegram inmediato con botones ✅/❌
    4. Juan aprueba/rechaza → GHL se actualiza automáticamente

  REPORTES AUTOMÁTICOS:
    - Diario 8PM EDT: resumen del día + eficacia de ARIA
    - Semanal Domingo 8AM EDT: últimos 7 días + tendencias + score Elena

  COMANDOS ON-DEMAND (Telegram):
    /audit 2d, /audit 30d, /reporte hoy, /reporte semana
    /errores, /eficacia, /llamada [call_id], /score

  ALERTAS AUTOMÁTICAS:
    - Degradación de score Elena (>10 puntos en 3 días)
    - Patrones de error (mismo error >5 veces en un día)

IMPORTANTE: Este script es completamente independiente de app.py.
No modifica ningún archivo de Elena. Solo lee datos de Vapi/GHL y escribe en Supabase.
"""

import os
import json
import logging
import requests
import smtplib
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from anthropic import Anthropic

# ============================================================
# CONFIGURACIÓN
# ============================================================

# Todas las credenciales se leen desde variables de entorno de Render.
# Se usa os.getenv() con default vacío para evitar KeyError al importar
# el módulo desde el web service (app.py). Las funciones validan internamente.
VAPI_API_KEY = os.getenv("VAPI_API_KEY", "")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
GHL_PIT = os.getenv("GHL_PIT", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://subzlfzuzcyqyfrzszjb.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Notificaciones
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "vitusmediard@gmail.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
RENDER_SERVER_URL = os.getenv("RENDER_SERVER_URL", "https://elena-pdem.onrender.com")

# Configuración de auditoría
AUDIT_LOOKBACK_HOURS = int(os.getenv("AUDIT_LOOKBACK_HOURS", "25"))
AUDIT_BATCH_SIZE = int(os.getenv("AUDIT_BATCH_SIZE", "50"))
CONFIDENCE_THRESHOLD_CORRECTION = float(os.getenv("CONFIDENCE_THRESHOLD_CORRECTION", "0.85"))
ARIA_VERSION = "2.0.0"

# FIX #1: Modelo correcto verificado en la cuenta
AUDIT_MODEL = "claude-sonnet-4-5"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ARIA] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("aria")

# ============================================================
# CLIENTE ANTHROPIC
# ============================================================
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)


# ============================================================
# TELEGRAM — NOTIFICACIONES
# ============================================================

def telegram_send(text: str, reply_markup: dict = None, chat_id: str = None) -> Optional[dict]:
    """
    Enviar un mensaje al chat de Juan via Telegram Bot API.
    Soporta botones inline para aprobación/rechazo.
    """
    _token = os.environ.get("TELEGRAM_BOT_TOKEN") or TELEGRAM_BOT_TOKEN
    _chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID") or TELEGRAM_CHAT_ID

    if not _token or not _chat_id:
        log.warning("Telegram no configurado — saltando notificación")
        return None

    payload = {
        "chat_id": _chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{_token}/sendMessage",
            json=payload,
            timeout=10
        )
        if r.status_code == 200:
            result = r.json()
            msg_id = result.get("result", {}).get("message_id")
            log.info(f"Telegram message sent — msg_id={msg_id}")
            return result.get("result")
        else:
            log.error(f"Telegram send error: {r.status_code} — {r.text[:200]}")
            return None
    except Exception as e:
        log.error(f"Telegram exception: {e}")
        return None


def telegram_notify_call(
    call_id: str,
    phone: str,
    original_outcome: str,
    aria_outcome: str,
    confidence: float,
    reasoning: str,
    errors: list,
    playbook_score: float,
    contact_name: str = None,
    call_ended_at: str = None,
    duration_seconds: int = None,
    has_discrepancy: bool = False,
    correction_id: str = None,
) -> bool:
    """
    Notificacion TOTAL por cada llamada completada (monitoreo total).
    Tres niveles visuales:
      ROJO     - DISCREPANCIA: GHL y ARIA no coinciden (requiere accion)
      AMARILLO - ALERTA: Coinciden pero hay errores HIGH/CRITICAL de playbook
      VERDE    - OK: Todo correcto, sin errores graves
    Siempre incluye: nombre, hora, outcome, score, reasoning, errores.
    Solo cuando hay discrepancia: botones APROBAR/RECHAZAR.
    """
    import pytz
    confidence_pct = int((confidence or 0) * 100)
    playbook_text = f"{playbook_score*100:.0f}%" if playbook_score is not None else "N/A"
    phone_display = phone[-10:] if phone and len(phone) >= 10 else phone or "N/A"

    # Nivel de alerta
    high_errors = [e for e in (errors or []) if e.get("severity", "").upper() in ("HIGH", "CRITICAL")]
    if has_discrepancy:
        level_icon = "\U0001f534"
        level_label = "DISCREPANCIA DETECTADA"
    elif high_errors:
        level_icon = "\U0001f7e1"
        level_label = "ALERTA DE CALIDAD"
    else:
        level_icon = "\U0001f7e2"
        level_label = "LLAMADA OK"

    # Nombre del contacto
    name_line = ("\U0001f464 <b>" + contact_name + "</b>\n") if contact_name else ""

    # Fecha/hora EDT
    datetime_line = ""
    if call_ended_at:
        try:
            edt = pytz.timezone("America/New_York")
            dt_str = call_ended_at.replace("Z", "+00:00")
            dt_utc = datetime.fromisoformat(dt_str)
            dt_edt = dt_utc.astimezone(edt)
            datetime_line = "\U0001f550 " + dt_edt.strftime("%d/%m/%Y %I:%M %p") + " EDT"
        except Exception:
            datetime_line = "\U0001f550 " + call_ended_at[:16].replace("T", " ") + " UTC"

    # Duracion
    dur_text = ""
    if duration_seconds:
        m, s = divmod(int(duration_seconds), 60)
        dur_text = f" \u00b7 {m}m{s:02d}s"

    # Outcome labels
    OUTCOME_LABELS = {
        "agendo": "\u2705 Agendo",
        "no_agendo": "\U0001f4cb No agendo",
        "no_contesto": "\U0001f4f5 No contesto",
        "llamar_luego": "\U0001f504 Llamar luego",
        "error_tecnico": "\u2699\ufe0f Error tecnico",
        "numero_invalido": "\U0001f6ab Numero invalido",
    }
    aria_label = OUTCOME_LABELS.get(aria_outcome or "", aria_outcome or "?")
    orig_label = OUTCOME_LABELS.get(original_outcome or "", original_outcome or "sin dato")

    # Errores de playbook
    errors_section = ""
    if errors:
        severity_icon = {"CRITICAL": "\U0001f534", "HIGH": "\U0001f7e0", "MEDIUM": "\U0001f7e1", "LOW": "\u26aa"}
        lines = [
            "  " + severity_icon.get(e.get("severity","").upper(), chr(8226)) + " " + e.get("type","?") + ": " + e.get("description","")[:80]
            for e in errors[:5]
        ]
        errors_section = "\n\n\u26a0\ufe0f <b>Errores de playbook:</b>\n" + "\n".join(lines)

    # Cuerpo del mensaje
    sep = "\u2501" * 24
    header = level_icon + " <b>ARIA \u00b7 " + level_label + "</b>\n" + sep
    meta = name_line + "\U0001f4de <code>+" + phone_display + "</code>  " + datetime_line + dur_text

    if has_discrepancy:
        outcome_block = (
            "\U0001f4cb GHL: <b>" + orig_label + "</b>\n"
            "\U0001f916 ARIA: <b>" + aria_label + "</b>  (" + str(confidence_pct) + "% confianza)\n"
            "\U0001f4ca Playbook: " + playbook_text
        )
    else:
        outcome_block = (
            "\U0001f916 Outcome: <b>" + aria_label + "</b>  (" + str(confidence_pct) + "% confianza)\n"
            "\U0001f4ca Playbook: " + playbook_text
        )

    reasoning_block = "\U0001f4ac <i>" + (reasoning or "")[:280] + "</i>" if reasoning else ""

    full_text = "\n".join(filter(None, [header, meta, outcome_block, reasoning_block])) + errors_section

    # Botones solo si hay discrepancia
    reply_markup = None
    if has_discrepancy and correction_id:
        reply_markup = {
            "inline_keyboard": [[
                {
                    "text": "\u2705 APROBAR correccion (" + (orig_label or "") + " \u2192 " + (aria_label or "") + ")",
                    "callback_data": "approve:" + correction_id
                }
            ], [
                {
                    "text": "\u274c RECHAZAR (mantener GHL)",
                    "callback_data": "reject:" + correction_id
                }
            ]]
        }

    result = telegram_send(full_text, reply_markup)
    return result is not None


# Alias para compatibilidad con llamadas existentes a telegram_notify_discrepancy
def telegram_notify_discrepancy(correction_id: str, call_id: str, phone: str,
                                original_outcome: str, aria_outcome: str,
                                confidence: float, reasoning: str,
                                errors: list, playbook_score: float,
                                contact_name: str = None,
                                call_ended_at: str = None) -> bool:
    return telegram_notify_call(
        call_id=call_id, phone=phone,
        original_outcome=original_outcome, aria_outcome=aria_outcome,
        confidence=confidence, reasoning=reasoning,
        errors=errors, playbook_score=playbook_score,
        contact_name=contact_name, call_ended_at=call_ended_at,
        has_discrepancy=True, correction_id=correction_id,
    )


def telegram_send_daily_report(metrics: dict, audit_date: str, top_errors: list,
                                aria_efficacy: dict = None) -> bool:
    """
    Enviar reporte diario completo por Telegram a las 8PM EDT.
    Incluye métricas del día + eficacia de ARIA + correcciones.
    """
    total = metrics.get("total_calls", 0)
    agendo = metrics.get("calls_agendo", 0)
    no_agendo = metrics.get("calls_no_agendo", 0)
    no_contesto = metrics.get("calls_no_contesto", 0)
    llamar_luego = metrics.get("calls_llamar_luego", 0)
    conversion = metrics.get("conversion_rate", 0) * 100
    contact_rate = metrics.get("contact_rate", 0) * 100
    playbook = metrics.get("avg_playbook_adherence")
    discrepancies = metrics.get("aria_discrepancies_found", 0)
    pb_str = f"{playbook*100:.0f}%" if playbook else "N/A"

    # Score Elena del día (0-100)
    elena_score = _calculate_elena_score(metrics)

    # Eficacia de ARIA
    efficacy_text = ""
    if aria_efficacy:
        approved = aria_efficacy.get("approved", 0)
        rejected = aria_efficacy.get("rejected", 0)
        total_fb = approved + rejected
        acc = f"{approved/total_fb*100:.0f}%" if total_fb > 0 else "N/A"
        efficacy_text = f"\n🎯 Eficacia ARIA: <b>{acc}</b> ({approved} aprobadas / {rejected} rechazadas)"

    # Top errores
    errors_text = ""
    if top_errors:
        errors_text = "\n\n⚠️ <b>Errores más frecuentes:</b>"
        for i, e in enumerate(top_errors[:3], 1):
            errors_text += f"\n  {i}. {e.get('type','?')} ×{e.get('count',0)}"

    text = (
        f"📊 <b>ARIA — Reporte Diario Elena</b>\n"
        f"📅 {audit_date}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 Llamadas: <b>{total}</b> | ✅ Citas: <b>{agendo}</b>\n"
        f"📈 Conversión: <b>{conversion:.1f}%</b> | Contacto: <b>{contact_rate:.1f}%</b>\n"
        f"📋 Playbook: <b>{pb_str}</b> | Score Elena: <b>{elena_score}/100</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 No agendó: {no_agendo} | 📵 No contestó: {no_contesto} | 📅 Llamar luego: {llamar_luego}\n"
        f"🔍 Discrepancias ARIA: <b>{discrepancies}</b>"
        f"{efficacy_text}"
        f"{errors_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 Reporte completo enviado por email."
    )
    result = telegram_send(text)
    return result is not None


def telegram_send_weekly_report(weekly_metrics: dict, start_date: str, end_date: str) -> bool:
    """
    Enviar reporte semanal por Telegram los domingos a las 8AM EDT.
    Incluye tendencias de 7 días + score Elena + eficacia ARIA.
    """
    total = weekly_metrics.get("total_calls", 0)
    agendo = weekly_metrics.get("calls_agendo", 0)
    conversion = weekly_metrics.get("avg_conversion_rate", 0) * 100
    playbook = weekly_metrics.get("avg_playbook_adherence")
    discrepancies = weekly_metrics.get("total_discrepancies", 0)
    approved = weekly_metrics.get("total_approved", 0)
    rejected = weekly_metrics.get("total_rejected", 0)
    elena_score = weekly_metrics.get("avg_elena_score", 0)
    score_trend = weekly_metrics.get("score_trend", "→")
    pb_str = f"{playbook*100:.0f}%" if playbook else "N/A"
    total_fb = approved + rejected
    acc = f"{approved/total_fb*100:.0f}%" if total_fb > 0 else "N/A"

    top_errors = weekly_metrics.get("top_errors", [])
    errors_text = ""
    if top_errors:
        errors_text = "\n\n⚠️ <b>Errores más frecuentes (semana):</b>"
        for i, e in enumerate(top_errors[:5], 1):
            errors_text += f"\n  {i}. {e.get('type','?')} ×{e.get('count',0)}"

    text = (
        f"📊 <b>ARIA — Reporte Semanal Elena</b>\n"
        f"📅 {start_date} → {end_date}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 Total llamadas: <b>{total}</b> | ✅ Citas: <b>{agendo}</b>\n"
        f"📈 Conversión promedio: <b>{conversion:.1f}%</b>\n"
        f"📋 Playbook promedio: <b>{pb_str}</b>\n"
        f"⭐ Score Elena: <b>{elena_score:.0f}/100</b> {score_trend}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Discrepancias ARIA: <b>{discrepancies}</b>\n"
        f"🎯 Eficacia ARIA: <b>{acc}</b> ({approved} aprobadas / {rejected} rechazadas)"
        f"{errors_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 Reporte completo enviado por email."
    )
    result = telegram_send(text)
    return result is not None


def telegram_handle_command(command: str, args: str, chat_id: str) -> bool:
    """
    Manejar comandos on-demand de Juan por Telegram.
    Comandos soportados:
      /audit 2d, /audit 30d — auditar N días
      /reporte hoy, /reporte semana — reportes
      /errores — top errores de la semana
      /eficacia — accuracy de ARIA
      /score — score actual de Elena
      /llamada [call_id] — detalle de una llamada
    """
    log.info(f"Telegram command: {command} {args}")

    try:
        if command == "/audit":
            # Parsear días: "2d" → 48h, "30d" → 720h
            days = 1
            if args:
                try:
                    days = int(args.replace("d", "").replace("h", "").strip())
                    if "h" in args:
                        hours = days
                    else:
                        hours = days * 24
                except ValueError:
                    hours = 25
            else:
                hours = 25

            telegram_send(
                f"🔄 <b>Iniciando audit de los últimos {days}d...</b>\n"
                f"Procesando llamadas desde Vapi. Esto puede tomar 2-5 minutos.",
                chat_id=chat_id
            )
            summary = run_audit(hours_back=hours)
            text = (
                f"✅ <b>Audit completado</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 Llamadas procesadas: <b>{summary.get('new_audited', 0)}</b>\n"
                f"🔍 Discrepancias encontradas: <b>{summary.get('discrepancies_found', 0)}</b>\n"
                f"📈 Conversión: <b>{summary.get('conversion_rate', 0)*100:.1f}%</b>\n"
                f"📋 Playbook promedio: <b>{str(round(summary.get('avg_playbook_score', 0)*100)) + '%' if summary.get('avg_playbook_score') else 'N/A'}</b>"
            )
            telegram_send(text, chat_id=chat_id)

        elif command == "/reporte":
            if args and "semana" in args.lower():
                _send_weekly_report_command(chat_id)
            else:
                _send_daily_report_command(chat_id)

        elif command == "/errores":
            _send_errors_report(chat_id, days=7)

        elif command == "/eficacia":
            _send_efficacy_report(chat_id)

        elif command == "/score":
            _send_score_report(chat_id)

        elif command == "/llamada":
            if args:
                _send_call_detail(chat_id, args.strip())
            else:
                telegram_send("⚠️ Uso: /llamada [call_id]", chat_id=chat_id)

        else:
            telegram_send(
                f"❓ Comando no reconocido: <code>{command}</code>\n\n"
                f"<b>Comandos disponibles:</b>\n"
                f"• /audit 2d — auditar últimos 2 días\n"
                f"• /audit 30d — auditar últimos 30 días\n"
                f"• /reporte hoy — resumen del día\n"
                f"• /reporte semana — últimos 7 días\n"
                f"• /errores — top errores de la semana\n"
                f"• /eficacia — accuracy de ARIA\n"
                f"• /score — score actual de Elena\n"
                f"• /llamada [id] — detalle de una llamada",
                chat_id=chat_id
            )

        return True

    except Exception as e:
        log.error(f"Error handling command {command}: {e}")
        telegram_send(f"⚠️ Error procesando comando: {str(e)[:100]}", chat_id=chat_id)
        return False


def _send_daily_report_command(chat_id: str):
    """Enviar reporte del día actual on-demand."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    records = supabase_query(
        "call_audits",
        f"created_at=gte.{today}T00:00:00Z&order=created_at.desc&limit=200"
    )
    if not records:
        telegram_send(f"📊 Sin llamadas auditadas hoy ({today}).", chat_id=chat_id)
        return

    results = _records_to_results(records)
    metrics = calculate_daily_metrics(results, today)
    top_errors = _get_top_errors(records)
    aria_efficacy = _get_aria_efficacy(days=1)

    telegram_send_daily_report(metrics, today, top_errors, aria_efficacy)


def _send_weekly_report_command(chat_id: str):
    """Enviar reporte semanal on-demand."""
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    weekly = _build_weekly_metrics(start_date, end_date)
    telegram_send_weekly_report(weekly, start_date, end_date)


def _send_errors_report(chat_id: str, days: int = 7):
    """Enviar reporte de errores más frecuentes."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    records = supabase_query(
        "call_audits",
        f"created_at=gte.{cutoff}&select=errors_detected&limit=500"
    )

    error_counts = {}
    for r in records:
        for err in (r.get("errors_detected") or []):
            t = err.get("type", "unknown")
            error_counts[t] = error_counts.get(t, 0) + 1

    if not error_counts:
        telegram_send(f"✅ Sin errores detectados en los últimos {days} días.", chat_id=chat_id)
        return

    sorted_errors = sorted(error_counts.items(), key=lambda x: x[1], reverse=True)
    text = f"⚠️ <b>Top errores de Elena (últimos {days}d)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for i, (err_type, count) in enumerate(sorted_errors[:10], 1):
        bar = "█" * min(count, 10)
        text += f"{i}. <b>{err_type}</b> ×{count} {bar}\n"
    telegram_send(text, chat_id=chat_id)


def _send_efficacy_report(chat_id: str):
    """Enviar reporte de eficacia de ARIA."""
    records = supabase_query(
        "feedback_log",
        "order=created_at.desc&limit=100"
    )

    if not records:
        telegram_send("📊 Sin feedback registrado aún.", chat_id=chat_id)
        return

    approved = sum(1 for r in records if r.get("feedback_type") == "approved")
    rejected = sum(1 for r in records if r.get("feedback_type") == "rejected")
    total = approved + rejected
    acc = f"{approved/total*100:.1f}%" if total > 0 else "N/A"

    # Últimos 7 días
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent = [r for r in records if r.get("created_at", "") >= cutoff]
    r_approved = sum(1 for r in recent if r.get("feedback_type") == "approved")
    r_rejected = sum(1 for r in recent if r.get("feedback_type") == "rejected")
    r_total = r_approved + r_rejected
    r_acc = f"{r_approved/r_total*100:.1f}%" if r_total > 0 else "N/A"

    text = (
        f"🎯 <b>Eficacia de ARIA</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Histórico:</b> {acc} ({approved} aprobadas / {rejected} rechazadas)\n"
        f"<b>Últimos 7d:</b> {r_acc} ({r_approved} aprobadas / {r_rejected} rechazadas)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ℹ️ Eficacia = % de correcciones de ARIA que Juan aprobó"
    )
    telegram_send(text, chat_id=chat_id)


def _send_score_report(chat_id: str):
    """Enviar score actual de Elena."""
    # Últimos 7 días
    records_7d = []
    for i in range(7):
        date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        day_records = supabase_query(
            "call_audits",
            f"created_at=gte.{date}T00:00:00Z&created_at=lt.{date}T23:59:59Z&limit=200"
        )
        if day_records:
            results = _records_to_results(day_records)
            metrics = calculate_daily_metrics(results, date)
            score = _calculate_elena_score(metrics)
            records_7d.append({"date": date, "score": score, "calls": metrics.get("total_calls", 0)})

    if not records_7d:
        telegram_send("📊 Sin datos suficientes para calcular el score.", chat_id=chat_id)
        return

    text = f"⭐ <b>Score Elena — Últimos 7 días</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for r in records_7d:
        score = r["score"]
        bar = "█" * (score // 10)
        emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
        text += f"{emoji} {r['date']}: <b>{score}/100</b> {bar} ({r['calls']} llamadas)\n"

    # Tendencia
    if len(records_7d) >= 2:
        trend = records_7d[0]["score"] - records_7d[-1]["score"]
        trend_str = f"↑ +{trend:.0f}" if trend > 0 else f"↓ {trend:.0f}" if trend < 0 else "→ estable"
        text += f"\n📈 Tendencia: <b>{trend_str}</b> vs hace 7 días"

    telegram_send(text, chat_id=chat_id)


def _send_call_detail(chat_id: str, call_id: str):
    """Enviar detalle de una llamada específica."""
    records = supabase_query(
        "call_audits",
        f"vapi_call_id=eq.{call_id}&select=*"
    )
    if not records:
        # Buscar por prefijo
        records = supabase_query(
            "call_audits",
            f"vapi_call_id=like.{call_id}%&select=*&limit=1"
        )

    if not records:
        telegram_send(f"⚠️ Llamada no encontrada: <code>{call_id}</code>", chat_id=chat_id)
        return

    r = records[0]
    errors = r.get("errors_detected") or []
    errors_text = ""
    if errors:
        errors_text = "\n\n⚠️ <b>Errores:</b>"
        for e in errors[:5]:
            errors_text += f"\n  • [{e.get('severity','?').upper()}] {e.get('type','?')}: {e.get('description','')[:60]}"

    playbook = r.get("playbook_adherence_score")
    pb_str = f"{playbook*100:.0f}%" if playbook else "N/A"

    text = (
        f"📞 <b>Detalle de Llamada</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ID: <code>{r.get('vapi_call_id', 'N/A')[:30]}</code>\n"
        f"📱 Teléfono: {r.get('phone_number', 'N/A')}\n"
        f"⏱ Duración: {r.get('call_duration_seconds', 'N/A')}s\n"
        f"📋 GHL dice: <b>{r.get('original_outcome', 'N/A')}</b>\n"
        f"🤖 ARIA dice: <b>{r.get('aria_outcome', 'N/A')}</b> ({int((r.get('aria_confidence') or 0)*100)}%)\n"
        f"📊 Playbook: <b>{pb_str}</b>\n"
        f"🔍 Estado: {r.get('audit_status', 'N/A')}"
        f"{errors_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 <i>{(r.get('aria_reasoning') or '')[:200]}</i>"
    )
    telegram_send(text, chat_id=chat_id)


# ============================================================
# HELPERS
# ============================================================

def _to_bool(value) -> Optional[bool]:
    """Convertir un valor a bool o None para columnas boolean de Supabase.
    GHL puede devolver 'pending', 'true', 'false', True, False, None, etc.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ('true', '1', 'yes', 'si', 'sí'):
            return True
        if v in ('false', '0', 'no'):
            return False
        # Valores no reconocidos (ej: 'pending') → None para no romper el insert
        return None
    # int, float, etc.
    return bool(value)


# ============================================================
# SUPABASE CLIENT (via REST API)
# ============================================================

def _get_supa_headers():
    """Obtener headers de Supabase leyendo env vars en tiempo de ejecución."""
    key = os.environ.get("SUPABASE_SERVICE_KEY") or SUPABASE_SERVICE_KEY
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }, key


def supabase_insert(table: str, data: dict) -> Optional[dict]:
    """Insertar un registro en Supabase via REST API."""
    headers, key = _get_supa_headers()
    if not key:
        log.warning(f"SUPABASE_SERVICE_KEY no configurado — saltando inserción en {table}")
        return None

    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**headers, "Prefer": "return=representation"},
        json=data,
        timeout=10
    )
    if r.status_code in (200, 201):
        result = r.json()
        return result[0] if isinstance(result, list) else result
    else:
        log.error(f"Supabase insert error [{table}]: {r.status_code} — {r.text[:200]}")
        return None


def supabase_upsert(table: str, data: dict, on_conflict: str = "vapi_call_id") -> Optional[dict]:
    """Upsert un registro en Supabase."""
    headers, key = _get_supa_headers()
    if not key:
        log.warning(f"SUPABASE_SERVICE_KEY no configurado — saltando upsert en {table}")
        return None

    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**headers, "Prefer": f"resolution=merge-duplicates,return=representation", "on_conflict": on_conflict},
        json=data,
        timeout=10
    )
    if r.status_code in (200, 201):
        result = r.json()
        return result[0] if isinstance(result, list) else result
    else:
        log.error(f"Supabase upsert error [{table}]: {r.status_code} — {r.text[:200]}")
        return None


def supabase_select(table: str, filters: dict = None, limit: int = 100) -> list:
    """Seleccionar registros de Supabase."""
    headers, key = _get_supa_headers()
    if not key:
        return []

    params = {"limit": limit}
    if filters:
        for k, v in filters.items():
            params[k] = f"eq.{v}"

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=headers,
        params=params,
        timeout=10
    )
    if r.status_code == 200:
        return r.json()
    else:
        log.error(f"Supabase select error [{table}]: {r.status_code} — {r.text[:200]}")
        return []


def supabase_query(table: str, query_string: str) -> list:
    """
    Query flexible de Supabase usando query string directo.
    Ejemplo: supabase_query("call_audits", "created_at=gte.2026-03-01&limit=100")
    """
    headers, key = _get_supa_headers()
    if not key:
        return []

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}?{query_string}",
        headers=headers,
        timeout=15
    )
    if r.status_code == 200:
        return r.json()
    else:
        log.error(f"Supabase query error [{table}]: {r.status_code} — {r.text[:200]}")
        return []


def supabase_update(table: str, filters: dict, data: dict) -> bool:
    """Actualizar registros en Supabase."""
    headers, key = _get_supa_headers()
    if not key:
        return False

    params = {}
    for k, v in filters.items():
        params[k] = f"eq.{v}"

    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=headers,
        params=params,
        json=data,
        timeout=10
    )
    return r.status_code in (200, 204)


# ============================================================
# VAPI API
# ============================================================

def fetch_vapi_calls(hours_back: int = 25, limit: int = 50) -> list:
    """
    Obtener llamadas de Vapi de las últimas N horas.
    """
    _vapi_key = os.environ.get("VAPI_API_KEY") or VAPI_API_KEY
    _assistant_id = os.environ.get("VAPI_ASSISTANT_ID") or VAPI_ASSISTANT_ID

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    log.info(f"Fetching Vapi calls since {cutoff_str} (last {hours_back}h)")

    r = requests.get(
        "https://api.vapi.ai/call",
        headers={"Authorization": f"Bearer {_vapi_key}"},
        params={
            "limit": limit,
            "assistantId": _assistant_id,
            "createdAtGt": cutoff_str
        },
        timeout=30
    )

    if r.status_code != 200:
        log.error(f"Vapi API error: {r.status_code} — {r.text[:200]}")
        return []

    calls = r.json()
    log.info(f"Fetched {len(calls)} calls from Vapi")
    return calls


def fetch_vapi_call_by_id(call_id: str) -> Optional[dict]:
    """Obtener una llamada específica de Vapi por ID."""
    _vapi_key = os.environ.get("VAPI_API_KEY") or VAPI_API_KEY
    r = requests.get(
        f"https://api.vapi.ai/call/{call_id}",
        headers={"Authorization": f"Bearer {_vapi_key}"},
        timeout=15
    )
    if r.status_code == 200:
        return r.json()
    log.error(f"Vapi call fetch error [{call_id}]: {r.status_code}")
    return None


def get_already_audited_ids() -> set:
    """Obtener IDs de llamadas ya auditadas en Supabase para evitar duplicados."""
    records = supabase_select("call_audits", limit=500)
    return {r["vapi_call_id"] for r in records if "vapi_call_id" in r}


# ============================================================
# GHL API
# ============================================================

def get_ghl_contact_id_by_phone(phone: str) -> Optional[str]:
    """
    Buscar un contacto en GHL por número de teléfono.
    Usado como fallback cuando Elena no llama a get_contact durante la llamada.
    """
    _ghl_pit = os.environ.get("GHL_PIT") or GHL_PIT
    _location_id = os.environ.get("GHL_LOCATION_ID") or GHL_LOCATION_ID

    if not phone:
        return None
    try:
        r = requests.post(
            "https://services.leadconnectorhq.com/contacts/search",
            headers={
                "Authorization": f"Bearer {_ghl_pit}",
                "Version": "2021-07-28",
                "Content-Type": "application/json"
            },
            json={
                "locationId": _location_id,
                "filters": [{"field": "phone", "operator": "eq", "value": phone}],
                "pageLimit": 1
            },
            timeout=10
        )
        if r.status_code == 200:
            contacts = r.json().get("contacts", [])
            if contacts:
                return contacts[0].get("id")
        else:
            log.warning(f"GHL phone search error [{phone}]: {r.status_code}")
    except Exception as e:
        log.warning(f"GHL phone search exception [{phone}]: {e}")
    return None


def get_ghl_contact_fields(contact_id: str) -> dict:
    """
    Obtener los campos de Elena del contacto en GHL.
    Retorna dict con los campos elena_*.
    """
    _ghl_pit = os.environ.get("GHL_PIT") or GHL_PIT

    r = requests.get(
        f"https://services.leadconnectorhq.com/contacts/{contact_id}",
        headers={
            "Authorization": f"Bearer {_ghl_pit}",
            "Version": "2021-07-28"
        },
        timeout=10
    )

    if r.status_code != 200:
        log.warning(f"GHL contact fetch error [{contact_id}]: {r.status_code}")
        return {}

    contact = r.json().get("contact", {})
    custom_fields = contact.get("customFields", [])

    # FIX #2: La API de GHL no devuelve fieldKey en customFields, solo id y value.
    # Usamos el ID del campo directamente (mapeado desde la location).
    elena_fields = {}
    ELENA_FIELD_IDS = {
        "ibrHOJBAON7gQpj9rT89": "elena_last_outcome",
        "oAs5Oga4qS7lGo0Kgt0S": "elena_call_duration",
        "z5E3DfytuVmJBy9QXCvD": "elena_ended_reason",
        "KbBNpjKFL3SErALyTFcM": "elena_success_eval",
        "cCd44bHm90pAn5q9fmux": "elena_summary",
        "Bb3FVz9jnWIbZkbjCDSw": "elena_vapi_call_id",
        "eQJVvxl128xm1P7LEo3v": "elena_outcome_display",
        "PudkAK9CqOKbDefRrCEF": "elena_stage",
        "s8beSvYXNMtzJRFENIUH": "elena_total_calls",
        "X0eYYBR1XN3r4Hhwa4aO": "elena_conversations",
    }

    for field in custom_fields:
        field_id = field.get("id", "")
        if field_id in ELENA_FIELD_IDS:
            aria_key = ELENA_FIELD_IDS[field_id]
            elena_fields[aria_key] = field.get("value")

    # Incluir nombre del contacto desde campos estándar de GHL
    first = (contact.get("firstName") or "").strip()
    last = (contact.get("lastName") or "").strip()
    full_name = f"{first} {last}".strip() or None
    elena_fields["contact_first_name"] = first or None
    elena_fields["contact_last_name"] = last or None
    elena_fields["contact_full_name"] = full_name

    return elena_fields


def update_ghl_contact_outcome(contact_id: str, new_outcome: str) -> bool:
    """
    Actualizar el outcome de Elena en GHL.
    Solo se llama cuando ARIA detecta una discrepancia y Juan aprueba la corrección.
    """
    _ghl_pit = os.environ.get("GHL_PIT") or GHL_PIT

    log.info(f"Updating GHL contact {contact_id} outcome to: {new_outcome}")

    r = requests.put(
        f"https://services.leadconnectorhq.com/contacts/{contact_id}",
        headers={
            "Authorization": f"Bearer {_ghl_pit}",
            "Version": "2021-07-28",
            "Content-Type": "application/json"
        },
        json={
            "customFields": [
                {"key": "elena_last_outcome", "field_value": new_outcome}
            ]
        },
        timeout=10
    )

    if r.status_code in (200, 201):
        log.info(f"GHL update successful for contact {contact_id}")
        return True
    else:
        log.error(f"GHL update failed [{contact_id}]: {r.status_code} — {r.text[:200]}")
        return False


# ============================================================
# ARIA AUDIT ENGINE — CLAUDE
# ============================================================

ARIA_SYSTEM_PROMPT = """Eres ARIA, el sistema de auditoría de llamadas para Elena, una agente de IA de ventas de Laser Place Miami que agenda citas de Botox.

Tu trabajo es analizar transcripts de llamadas telefónicas y determinar:
1. Si la clasificación del outcome es correcta
2. Si Elena siguió el playbook correctamente
3. Qué errores cometió Elena (si los hay)
4. La calidad general de la conversación

## OUTCOMES POSIBLES:
- **agendo**: Se creó una cita exitosamente (create_booking o reschedule_appointment exitoso)
- **no_agendo**: Hubo conversación real pero no se agendó cita
- **no_contesto**: El cliente no contestó, buzón de voz, IVR, o conversación <20s sin contenido real
- **llamar_luego**: El cliente pidió que lo llamen después (schedule_callback exitoso)
- **error_tecnico**: Error técnico que impidió la llamada
- **no_interesado**: El cliente explícitamente rechazó el servicio

## REGLAS DE CLASIFICACIÓN:
1. Si hay un create_booking o reschedule_appointment exitoso en los tool calls → SIEMPRE es "agendo"
2. Si la llamada duró <20s o el cliente no habló → "no_contesto"
3. Si el cliente dijo frases de buzón de voz (inglés o español) → "no_contesto"
4. Si schedule_callback fue exitoso → "llamar_luego"
5. Si el cliente dijo "no me interesa", "no quiero", "no me llames más" → "no_interesado"
6. Todo lo demás con conversación real → "no_agendo"

## ERRORES DE ELENA A DETECTAR:
- **missed_close**: Tenía oportunidad de agendar pero no la aprovechó
- **wrong_info**: Dio información incorrecta (precio, disponibilidad, etc.)
- **playbook_violation**: No siguió el playbook (ej: ofreció precio antes de la evaluación)
- **premature_endcall**: Terminó la llamada cuando el cliente aún quería hablar
- **repeated_availability_check**: Llamó a check_availability más de 2 veces innecesariamente
- **language_switch**: El cliente habló en inglés pero Elena respondió en español (o viceversa)
- **confusion_created**: Elena confundió al cliente con información contradictoria
- **premature_greeting**: Elena comenzó su pitch antes de confirmar que había una persona real

## PLAYBOOK DE ELENA (resumen):
1. Saludo → preguntar si tiene 2 minutos
2. Preguntar si los martes funcionan (día preferido de la clínica)
3. Si no → ofrecer otros días disponibles
4. check_availability → presentar 2 opciones máximo
5. Cuando el cliente elige → create_booking
6. Si el cliente pregunta precio → explicar que se personaliza, invitar a la evaluación gratuita
7. Despedida con confirmación de la cita

## FORMATO DE RESPUESTA:
Responde SIEMPRE en JSON válido con esta estructura exacta:
{
  "correct_outcome": "agendo|no_agendo|no_contesto|llamar_luego|error_tecnico|no_interesado",
  "confidence": 0.0-1.0,
  "reasoning": "Explicación breve de por qué este es el outcome correcto",
  "playbook_adherence_score": 0.0-1.0,
  "errors_detected": [
    {
      "type": "tipo_de_error",
      "description": "descripción específica",
      "severity": "low|medium|high",
      "timestamp_approx": "momento en la conversación"
    }
  ],
  "silence_detected": true|false,
  "language_switch_detected": true|false,
  "appointment_offered": true|false,
  "objection_handled": true|false,
  "quality_notes": "Notas adicionales sobre la calidad de la llamada"
}"""


def audit_call_with_claude(call_data: dict) -> dict:
    """
    Auditar una llamada usando Claude.
    Retorna el resultado del análisis.
    """
    call_id = call_data.get("id", "unknown")
    transcript = call_data.get("transcript", "") or ""
    summary = call_data.get("summary", "") or ""
    ended_reason = call_data.get("endedReason", "") or ""
    started_at = call_data.get("startedAt", "")
    ended_at = call_data.get("endedAt", "")

    # Calcular duración
    duration_seconds = None
    if started_at and ended_at:
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            duration_seconds = int((end - start).total_seconds())
        except Exception:
            pass

    # Extraer tool calls
    messages = call_data.get("messages", []) or []
    tool_calls_summary = []
    for msg in messages:
        if msg.get("role") == "tool_calls":
            for tc in msg.get("toolCalls", []):
                fn = tc.get("function", {})
                tool_calls_summary.append({
                    "name": fn.get("name"),
                    "args": fn.get("arguments", "{}")
                })
        elif msg.get("role") == "tool_call_result":
            if tool_calls_summary:
                result_str = str(msg.get("result", ""))[:300]
                tool_calls_summary[-1]["result"] = result_str

    user_prompt = f"""Analiza esta llamada de Elena y determina el outcome correcto.

## DATOS DE LA LLAMADA:
- ID: {call_id}
- Duración: {duration_seconds}s
- Razón de fin: {ended_reason}
- Inicio: {started_at}

## TRANSCRIPT:
{transcript[:3000] if transcript else "(sin transcript)"}

## RESUMEN GENERADO POR VAPI:
{summary[:500] if summary else "(sin resumen)"}

## TOOL CALLS EJECUTADOS:
{json.dumps(tool_calls_summary, ensure_ascii=False, indent=2)[:2000] if tool_calls_summary else "(ninguno)"}

Analiza todo lo anterior y responde en JSON con el formato especificado."""

    try:
        response = anthropic_client.messages.create(
            model=AUDIT_MODEL,
            max_tokens=1024,
            system=ARIA_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )

        response_text = response.content[0].text.strip()

        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()

        audit_result = json.loads(response_text)
        audit_result["duration_seconds"] = duration_seconds
        audit_result["call_id"] = call_id

        log.info(f"Audit complete [{call_id}]: outcome={audit_result.get('correct_outcome')} confidence={audit_result.get('confidence', 0):.2f}")
        return audit_result

    except json.JSONDecodeError as e:
        log.error(f"JSON parse error for call {call_id}: {e}")
        return {
            "correct_outcome": None, "confidence": 0.0,
            "reasoning": f"Error parsing Claude response: {str(e)}",
            "errors_detected": [], "playbook_adherence_score": None,
            "call_id": call_id, "duration_seconds": duration_seconds
        }
    except Exception as e:
        log.error(f"Claude audit error for call {call_id}: {e}")
        return {
            "correct_outcome": None, "confidence": 0.0,
            "reasoning": f"Error: {str(e)}",
            "errors_detected": [], "playbook_adherence_score": None,
            "call_id": call_id, "duration_seconds": duration_seconds
        }


# ============================================================
# PROCESO PRINCIPAL DE AUDITORÍA
# ============================================================

def process_call(call_data: dict, already_audited: set) -> Optional[dict]:
    """
    Procesar una llamada individual:
    1. Verificar si ya fue auditada
    2. Obtener datos del contacto en GHL
    3. Auditar con Claude
    4. Comparar con clasificación original
    5. Guardar en Supabase (FIX #3: has_discrepancy incluido en el record)
    6. Si hay discrepancia, registrar corrección pendiente y notificar Telegram
    """
    call_id = call_data.get("id")

    if call_id in already_audited:
        log.debug(f"Skipping already audited call: {call_id}")
        return None

    if call_data.get("status") != "ended":
        return None

    log.info(f"Processing call: {call_id}")

    # Obtener datos del contacto en GHL
    customer = call_data.get("customer", {}) or {}
    phone = customer.get("number", "")
    ghl_contact_id = None
    original_outcome = None

    # Intentar obtener el contactId del transcript/messages
    messages = call_data.get("messages", []) or []
    for msg in messages:
        if msg.get("role") == "tool_call_result" and msg.get("name") == "get_contact":
            result_str = msg.get("result", "{}")
            try:
                result = json.loads(result_str) if isinstance(result_str, str) else result_str
                if result.get("found"):
                    ghl_contact_id = result.get("contactId")
                    break
            except Exception:
                pass

    # FALLBACK: buscar por teléfono en GHL
    if not ghl_contact_id and phone:
        ghl_contact_id = get_ghl_contact_id_by_phone(phone)
        if ghl_contact_id:
            log.info(f"Contact found by phone fallback [{call_id}]: {ghl_contact_id}")

    # Obtener campos de Elena
    ghl_fields = {}
    if ghl_contact_id:
        ghl_fields = get_ghl_contact_fields(ghl_contact_id)
        original_outcome = ghl_fields.get("elena_last_outcome")

    # Auditar con Claude
    audit_result = audit_call_with_claude(call_data)

    aria_outcome = audit_result.get("correct_outcome")
    aria_confidence = audit_result.get("confidence", 0.0)

    # Determinar si hay discrepancia
    has_discrepancy = (
        original_outcome is not None and
        aria_outcome is not None and
        original_outcome != aria_outcome and
        aria_confidence >= CONFIDENCE_THRESHOLD_CORRECTION
    )

    audit_status = "discrepancy_found" if has_discrepancy else "audited"
    if has_discrepancy:
        log.warning(f"DISCREPANCY [{call_id}]: original={original_outcome} aria={aria_outcome} confidence={aria_confidence:.2f}")

    duration_seconds = audit_result.get("duration_seconds")
    started_at = call_data.get("startedAt")
    ended_at = call_data.get("endedAt")

    # FIX #3: has_discrepancy incluido en el audit_record
    audit_record = {
        "vapi_call_id": call_id,
        "ghl_contact_id": ghl_contact_id,
        "phone_number": phone,
        "agent_name": "elena",
        "call_started_at": started_at,
        "call_ended_at": ended_at,
        "call_duration_seconds": duration_seconds,
        "original_outcome": original_outcome,
        "original_ended_reason": call_data.get("endedReason"),
        "original_success_eval": _to_bool(ghl_fields.get("elena_success_eval")),
        "original_summary": call_data.get("summary"),
        "aria_outcome": aria_outcome,
        "aria_confidence": aria_confidence,
        "aria_reasoning": audit_result.get("reasoning"),
        "audit_status": audit_status,
        "playbook_adherence_score": audit_result.get("playbook_adherence_score"),
        "silence_detected": audit_result.get("silence_detected", False),
        "language_switch_detected": audit_result.get("language_switch_detected", False),
        "objection_handled": audit_result.get("objection_handled"),
        "appointment_offered": audit_result.get("appointment_offered"),
        "errors_detected": audit_result.get("errors_detected", []),
        "transcript_text": call_data.get("transcript", "")[:5000] if call_data.get("transcript") else None,
        "audio_url": call_data.get("recordingUrl"),
        "audit_model": AUDIT_MODEL,
        "audit_version": ARIA_VERSION,
        "raw_vapi_data": {
            "id": call_id,
            "endedReason": call_data.get("endedReason"),
            "status": call_data.get("status"),
            "cost": call_data.get("cost"),
        }
        # NOTE: has_discrepancy columna no existe en Supabase — se calcula en memoria como (aria_outcome != original_outcome)
    }

    # Guardar en Supabase
    saved = supabase_upsert("call_audits", audit_record)

    # ── Guardar corrección en Supabase si hay discrepancia ──────────────────
    _correction_id = None
    if saved and has_discrepancy and ghl_contact_id:
        correction_record = {
            "audit_id": saved.get("id"),
            "vapi_call_id": call_id,
            "ghl_contact_id": ghl_contact_id,
            "field_name": "elena_last_outcome",
            "old_value": original_outcome,
            "new_value": aria_outcome,
            "correction_status": "pending"
        }
        correction_saved = supabase_insert("aria_corrections", correction_record)
        if correction_saved:
            _correction_id = correction_saved.get("id", "")
            log.info(f"Correction record created: {_correction_id}")

    # ── Notificación Telegram SIEMPRE (monitoreo total) ──────────────────────
    if saved:
        try:
            telegram_notify_call(
                call_id=call_id,
                phone=phone,
                original_outcome=original_outcome,
                aria_outcome=aria_outcome,
                confidence=aria_confidence,
                reasoning=audit_result.get("reasoning", ""),
                errors=audit_result.get("errors_detected", []),
                playbook_score=audit_result.get("playbook_adherence_score"),
                contact_name=ghl_fields.get("contact_full_name"),
                call_ended_at=ended_at,
                duration_seconds=duration_seconds,
                has_discrepancy=has_discrepancy,
                correction_id=_correction_id,
            )
            log.info(f"Telegram notification sent — discrepancy={has_discrepancy} correction={_correction_id}")
        except Exception as _tg_err:
            log.error(f"Telegram notify error: {_tg_err}")

    return {
        "call_id": call_id,
        "original_outcome": original_outcome,
        "aria_outcome": aria_outcome,
        "aria_confidence": aria_confidence,
        "has_discrepancy": has_discrepancy,
        "audit_status": audit_status,
        "errors_count": len(audit_result.get("errors_detected", [])),
        "errors_detected_types": [e.get("type") for e in audit_result.get("errors_detected", [])],
        "playbook_score": audit_result.get("playbook_adherence_score"),
        "duration_seconds": duration_seconds,
        "ghl_contact_id": ghl_contact_id,
        "quality_notes": audit_result.get("quality_notes", "")
    }


def process_single_call_realtime(call_data: dict) -> Optional[dict]:
    """
    Procesar una llamada individual en tiempo real (disparado por webhook de Vapi).
    El webhook end-of-call-report garantiza que la llamada ya terminó, pero Vapi
    NO siempre incluye el campo 'status' en el payload del webhook.
    Inyectamos status='ended' si no viene para evitar el filtro en process_call.
    """
    call_id = call_data.get("id")
    status_in_payload = call_data.get("status")
    if not status_in_payload:
        # El webhook end-of-call-report garantiza que la llamada terminó
        # Vapi omite 'status' del payload del webhook — lo inyectamos
        call_data = dict(call_data)  # copia para no mutar el original
        call_data["status"] = "ended"
        log.info(f"process_single_call_realtime [{call_id}]: status no vino en webhook, inyectado como 'ended'")
    else:
        log.info(f"process_single_call_realtime [{call_id}]: status en webhook = '{status_in_payload}'")
    already_audited = get_already_audited_ids()
    return process_call(call_data, already_audited)


# ============================================================
# APPLY CORRECTION — Aplicar corrección aprobada en GHL
# ============================================================

def apply_correction(correction_id: str, approved: bool, feedback_notes: str = "") -> dict:
    """
    Aplicar o rechazar una corrección pendiente.
    Llamado desde el endpoint /aria/correction/<id>/approve o /reject en app.py.
    """
    _supabase_key = os.environ.get("SUPABASE_SERVICE_KEY") or SUPABASE_SERVICE_KEY
    _supabase_url = os.environ.get("SUPABASE_URL") or SUPABASE_URL
    _ghl_pit = os.environ.get("GHL_PIT") or GHL_PIT

    if not _supabase_key:
        return {"success": False, "error": "SUPABASE_SERVICE_KEY no configurado"}

    r = requests.get(
        f"{_supabase_url}/rest/v1/aria_corrections",
        headers={"apikey": _supabase_key, "Authorization": f"Bearer {_supabase_key}"},
        params={"id": f"eq.{correction_id}", "select": "*"},
        timeout=10
    )
    if r.status_code != 200 or not r.json():
        return {"success": False, "error": f"Corrección no encontrada: {correction_id}"}

    correction = r.json()[0]
    current_status = correction.get("correction_status")

    if current_status != "pending":
        return {
            "success": False,
            "error": f"Corrección ya procesada (status={current_status})",
            "correction_id": correction_id
        }

    ghl_contact_id = correction.get("ghl_contact_id")
    old_value = correction.get("old_value")
    new_value = correction.get("new_value")
    audit_id = correction.get("audit_id")
    vapi_call_id = correction.get("vapi_call_id")

    ghl_response_code = None
    ghl_response_body = None

    if approved:
        success = update_ghl_contact_outcome(ghl_contact_id, new_value)
        new_status = "applied" if success else "pending"
        ghl_response_code = 200 if success else 500
        ghl_response_body = "OK" if success else "GHL update failed"
        if success:
            supabase_update("call_audits", {"id": audit_id}, {"audit_status": "feedback_approved"})
    else:
        new_status = "reverted"
        success = True
        supabase_update("call_audits", {"id": audit_id}, {"audit_status": "feedback_rejected"})

    supabase_update(
        "aria_corrections",
        {"id": correction_id},
        {"correction_status": new_status, "ghl_response_code": ghl_response_code, "ghl_response_body": ghl_response_body}
    )

    feedback_record = {
        "audit_id": audit_id,
        "vapi_call_id": vapi_call_id,
        "feedback_type": "approved" if approved else "rejected",
        "feedback_source": "telegram",
        "original_outcome": old_value,
        "aria_outcome": new_value,
        "final_outcome": new_value if approved else old_value,
        "notes": feedback_notes or f"Telegram: {'aprobado' if approved else 'rechazado'} por Juan"
    }
    supabase_insert("feedback_log", feedback_record)

    if approved and success:
        msg = (f"✅ <b>Corrección aplicada en GHL</b>\n"
               f"<code>{vapi_call_id[:20] if vapi_call_id else 'N/A'}...</code>\n"
               f"Outcome actualizado: <b>{old_value}</b> → <b>{new_value}</b>")
    elif approved and not success:
        msg = (f"⚠️ <b>Error al aplicar corrección en GHL</b>\n"
               f"La corrección fue aprobada pero GHL devolvió un error.")
    else:
        msg = (f"❌ <b>Corrección rechazada</b>\n"
               f"<code>{vapi_call_id[:20] if vapi_call_id else 'N/A'}...</code>\n"
               f"Se mantiene la clasificación original: <b>{old_value}</b>")
    telegram_send(msg)

    log.info(f"Correction {correction_id}: approved={approved} status={new_status}")
    return {
        "success": success,
        "correction_id": correction_id,
        "approved": approved,
        "new_status": new_status,
        "old_value": old_value,
        "new_value": new_value
    }


# ============================================================
# MÉTRICAS Y SCORES
# ============================================================

def _calculate_elena_score(metrics: dict) -> int:
    """
    Calcular el score de Elena del día (0-100).
    Combina: conversión (40%) + playbook (35%) + tasa de error (25%)
    """
    total = metrics.get("total_calls", 0)
    if total == 0:
        return 0

    conversion = metrics.get("conversion_rate", 0)
    playbook = metrics.get("avg_playbook_adherence") or 0
    calls_with_errors = metrics.get("calls_with_errors", 0)
    error_rate = 1 - (calls_with_errors / total) if total > 0 else 1

    score = (
        conversion * 40 +
        playbook * 35 +
        error_rate * 25
    )
    return min(100, max(0, int(score)))


def _records_to_results(records: list) -> list:
    """Convertir registros de Supabase al formato de results para calculate_daily_metrics."""
    results = []
    for r in records:
        results.append({
            "call_id": r.get("vapi_call_id"),
            "original_outcome": r.get("original_outcome"),
            "aria_outcome": r.get("aria_outcome"),
            "aria_confidence": r.get("aria_confidence", 0),
            "has_discrepancy": r.get("audit_status") == "discrepancy_found",
            "audit_status": r.get("audit_status"),
            "errors_count": len(r.get("errors_detected") or []),
            "errors_detected_types": [e.get("type") for e in (r.get("errors_detected") or [])],
            "playbook_score": r.get("playbook_adherence_score"),
            "duration_seconds": r.get("call_duration_seconds"),
            "ghl_contact_id": r.get("ghl_contact_id"),
        })
    return results


def _get_top_errors(records: list) -> list:
    """Obtener los errores más frecuentes de una lista de registros."""
    error_counts = {}
    for r in records:
        for err in (r.get("errors_detected") or []):
            t = err.get("type", "unknown")
            error_counts[t] = error_counts.get(t, 0) + 1
    sorted_errors = sorted(error_counts.items(), key=lambda x: x[1], reverse=True)
    return [{"type": t, "count": c} for t, c in sorted_errors[:10]]


def _get_aria_efficacy(days: int = 1) -> dict:
    """Obtener métricas de eficacia de ARIA para los últimos N días."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    records = supabase_query("feedback_log", f"created_at=gte.{cutoff}&limit=200")
    approved = sum(1 for r in records if r.get("feedback_type") == "approved")
    rejected = sum(1 for r in records if r.get("feedback_type") == "rejected")
    return {"approved": approved, "rejected": rejected}


def _build_weekly_metrics(start_date: str, end_date: str) -> dict:
    """Construir métricas agregadas de la semana."""
    records = supabase_query(
        "call_audits",
        f"created_at=gte.{start_date}T00:00:00Z&created_at=lte.{end_date}T23:59:59Z&limit=1000"
    )

    if not records:
        return {"total_calls": 0}

    results = _records_to_results(records)
    top_errors = _get_top_errors(records)

    total = len(results)
    agendo = sum(1 for r in results if r.get("original_outcome") == "agendo")
    no_contesto = sum(1 for r in results if r.get("original_outcome") == "no_contesto")
    discrepancies = sum(1 for r in results if r.get("has_discrepancy"))

    playbook_scores = [r.get("playbook_score") for r in results if r.get("playbook_score") is not None]
    avg_playbook = sum(playbook_scores) / len(playbook_scores) if playbook_scores else None

    # Eficacia de ARIA en la semana
    cutoff = f"{start_date}T00:00:00Z"
    feedback = supabase_query("feedback_log", f"created_at=gte.{cutoff}&limit=200")
    approved = sum(1 for r in feedback if r.get("feedback_type") == "approved")
    rejected = sum(1 for r in feedback if r.get("feedback_type") == "rejected")

    # Score promedio Elena
    daily_scores = []
    for i in range(7):
        date = (datetime.fromisoformat(start_date) + timedelta(days=i)).strftime("%Y-%m-%d")
        day_records = [r for r in records if (r.get("created_at") or "").startswith(date)]
        if day_records:
            day_results = _records_to_results(day_records)
            day_metrics = calculate_daily_metrics(day_results, date)
            daily_scores.append(_calculate_elena_score(day_metrics))

    avg_score = sum(daily_scores) / len(daily_scores) if daily_scores else 0
    score_trend = "↑" if (daily_scores and daily_scores[0] > daily_scores[-1]) else "↓" if (daily_scores and daily_scores[0] < daily_scores[-1]) else "→"

    return {
        "total_calls": total,
        "calls_agendo": agendo,
        "calls_no_contesto": no_contesto,
        "avg_conversion_rate": agendo / total if total > 0 else 0,
        "avg_playbook_adherence": avg_playbook,
        "total_discrepancies": discrepancies,
        "total_approved": approved,
        "total_rejected": rejected,
        "avg_elena_score": avg_score,
        "score_trend": score_trend,
        "top_errors": top_errors,
    }


def calculate_daily_metrics(results: list, audit_date: str) -> dict:
    """Calcular métricas agregadas del día."""
    if not results:
        return {"total_calls": 0, "metric_date": audit_date, "agent_name": "elena"}

    total = len(results)
    outcomes = {}
    for r in results:
        o = r.get("original_outcome") or "unknown"
        outcomes[o] = outcomes.get(o, 0) + 1

    agendo = outcomes.get("agendo", 0)
    no_contesto = outcomes.get("no_contesto", 0)
    discrepancies = sum(1 for r in results if r.get("has_discrepancy"))
    errors_total = sum(r.get("errors_count", 0) for r in results)

    durations = [r.get("duration_seconds") for r in results if r.get("duration_seconds")]
    avg_duration = sum(durations) / len(durations) if durations else 0

    playbook_scores = [r.get("playbook_score") for r in results if r.get("playbook_score") is not None]
    avg_playbook = sum(playbook_scores) / len(playbook_scores) if playbook_scores else None

    conversion_rate = agendo / total if total > 0 else 0
    contact_rate = (total - no_contesto) / total if total > 0 else 0

    return {
        "metric_date": audit_date,
        "agent_name": "elena",
        "total_calls": total,
        "calls_agendo": agendo,
        "calls_no_agendo": outcomes.get("no_agendo", 0),
        "calls_no_contesto": no_contesto,
        "calls_llamar_luego": outcomes.get("llamar_luego", 0),
        "calls_error_tecnico": outcomes.get("error_tecnico", 0),
        "calls_no_interesado": outcomes.get("no_interesado", 0),
        "conversion_rate": round(conversion_rate, 4),
        "contact_rate": round(contact_rate, 4),
        "avg_call_duration_seconds": round(avg_duration, 1),
        "avg_playbook_adherence": round(avg_playbook, 3) if avg_playbook else None,
        "calls_with_errors": sum(1 for r in results if r.get("errors_count", 0) > 0),
        "aria_discrepancies_found": discrepancies,
        "report_generated_at": datetime.now(timezone.utc).isoformat()
    }


# ============================================================
# ALERTAS AUTOMÁTICAS
# ============================================================

def check_degradation_alert():
    """
    FIX #5 / NUEVA FUNCIÓN: Verificar si el score de Elena bajó >10 puntos en 3 días.
    Si sí, enviar alerta inmediata por Telegram.
    """
    scores = []
    for i in range(3):
        date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        records = supabase_query(
            "call_audits",
            f"created_at=gte.{date}T00:00:00Z&created_at=lt.{date}T23:59:59Z&limit=200"
        )
        if records:
            results = _records_to_results(records)
            metrics = calculate_daily_metrics(results, date)
            scores.append({"date": date, "score": _calculate_elena_score(metrics), "calls": metrics.get("total_calls", 0)})

    if len(scores) >= 2:
        drop = scores[-1]["score"] - scores[0]["score"]  # Más antiguo - más reciente
        if drop >= 10:
            telegram_send(
                f"🚨 <b>ALERTA: Degradación de Elena detectada</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Score bajó <b>{drop} puntos</b> en los últimos 3 días:\n"
                f"• {scores[-1]['date']}: {scores[-1]['score']}/100\n"
                f"• {scores[0]['date']}: {scores[0]['score']}/100\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Usa /errores para ver qué está fallando."
            )
            log.warning(f"DEGRADATION ALERT: score dropped {drop} points in 3 days")


def check_error_pattern_alert(results: list, audit_date: str):
    """
    Verificar si un tipo de error aparece >5 veces en el día.
    Si sí, enviar alerta inmediata por Telegram.
    """
    error_counts = {}
    for r in results:
        for err_type in r.get("errors_detected_types", []):
            error_counts[err_type] = error_counts.get(err_type, 0) + 1

    for err_type, count in error_counts.items():
        if count >= 5:
            telegram_send(
                f"⚠️ <b>Patrón de error detectado hoy</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Error: <b>{err_type}</b> ×{count} veces\n"
                f"Fecha: {audit_date}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Elena está repitiendo este error sistemáticamente hoy."
            )
            log.warning(f"ERROR PATTERN ALERT: {err_type} appeared {count} times today")


# ============================================================
# REPORTE DIARIO — EMAIL
# ============================================================

def build_report_text(results: list, metrics: dict, audit_date: str,
                       aria_efficacy: dict = None, report_type: str = "daily") -> str:
    """Construir el texto del reporte (diario o semanal)."""

    total = metrics.get("total_calls", 0)
    agendo = metrics.get("calls_agendo", 0)
    no_agendo = metrics.get("calls_no_agendo", 0)
    no_contesto = metrics.get("calls_no_contesto", 0)
    llamar_luego = metrics.get("calls_llamar_luego", 0)
    error_tecnico = metrics.get("calls_error_tecnico", 0)
    no_interesado = metrics.get("calls_no_interesado", 0)
    conversion = metrics.get("conversion_rate", 0) * 100
    contact_rate = metrics.get("contact_rate", 0) * 100
    discrepancies = metrics.get("aria_discrepancies_found", 0)
    avg_duration = metrics.get("avg_call_duration_seconds", 0)
    avg_playbook = metrics.get("avg_playbook_adherence")
    elena_score = _calculate_elena_score(metrics)

    discrepancy_details = [r for r in results if r.get("has_discrepancy")]
    all_errors = []
    for r in results:
        all_errors.extend(r.get("errors_detected_types", []))

    error_counts = {}
    for e in all_errors:
        error_counts[e] = error_counts.get(e, 0) + 1
    top_errors = sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # Eficacia de ARIA
    efficacy_section = ""
    if aria_efficacy:
        approved = aria_efficacy.get("approved", 0)
        rejected = aria_efficacy.get("rejected", 0)
        total_fb = approved + rejected
        acc = f"{approved/total_fb*100:.0f}%" if total_fb > 0 else "N/A"
        efficacy_section = f"""
🎯 EFICACIA DE ARIA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Correcciones propuestas:  {total_fb}
Aprobadas por Juan:       {approved} ({acc})
Rechazadas por Juan:      {rejected}
"""

    report = f"""
╔══════════════════════════════════════════════════════════╗
║         ARIA — REPORTE {'DIARIO' if report_type == 'daily' else 'SEMANAL'} DE ELENA                   ║
║         Fecha: {audit_date}                             
╚══════════════════════════════════════════════════════════╝

📊 RESUMEN EJECUTIVO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total llamadas auditadas:  {total}
Score Elena:               {elena_score}/100
Tasa de conversión:        {conversion:.1f}% ({agendo} citas agendadas)
Tasa de contacto:          {contact_rate:.1f}%
Duración promedio:         {avg_duration:.0f}s
Playbook adherence:        {f"{avg_playbook*100:.0f}%" if avg_playbook else "N/A"}

📋 DISTRIBUCIÓN DE OUTCOMES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Agendó:          {agendo:3d}  ({agendo/total*100:.0f}% del total)
💬 No agendó:       {no_agendo:3d}  ({no_agendo/total*100:.0f}% del total)
📵 No contestó:     {no_contesto:3d}  ({no_contesto/total*100:.0f}% del total)
📅 Llamar luego:    {llamar_luego:3d}  ({llamar_luego/total*100:.0f}% del total)
🚫 No interesado:   {no_interesado:3d}  ({no_interesado/total*100:.0f}% del total)
⚠️  Error técnico:   {error_tecnico:3d}  ({error_tecnico/total*100:.0f}% del total)
""" if total > 0 else f"\n📊 Sin llamadas para auditar en {audit_date}\n"

    if top_errors:
        report += f"""
⚠️ TOP ERRORES DE ELENA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, (err_type, count) in enumerate(top_errors, 1):
            report += f"  {i}. {err_type}: ×{count}\n"

    if discrepancies > 0:
        report += f"""
🔍 CORRECCIONES DE ARIA ({discrepancies} discrepancias detectadas)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(discrepancy_details[:10], 1):
            report += (
                f"{i}. Llamada: {d.get('call_id', 'N/A')[:20]}...\n"
                f"   GHL dice: {d.get('original_outcome', 'N/A')} → ARIA dice: {d.get('aria_outcome', 'N/A')} "
                f"(confianza: {d.get('aria_confidence', 0)*100:.0f}%)\n"
            )

    report += efficacy_section

    report += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generado por ARIA v{ARIA_VERSION} a las {datetime.now().strftime('%H:%M')} EST
"""
    return report


def send_email_report(report_text: str, audit_date: str, metrics: dict, subject_prefix: str = "Diario"):
    """Enviar reporte por email usando Gmail SMTP."""
    log.info("=" * 60)
    log.info(f"REPORTE {subject_prefix.upper()} ARIA")
    log.info("=" * 60)
    log.info(report_text)
    log.info("=" * 60)

    gmail_from = os.getenv("GMAIL_FROM", "")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", "")

    if not gmail_from or not gmail_app_password:
        log.warning("Email delivery skipped: GMAIL_FROM o GMAIL_APP_PASSWORD no configurados")
        return False

    try:
        total = metrics.get("total_calls", 0)
        agendo = metrics.get("calls_agendo", 0)
        conversion = metrics.get("conversion_rate", 0) * 100
        avg_playbook = metrics.get("avg_playbook_adherence")
        pb_str = f"{avg_playbook*100:.0f}%" if avg_playbook else "N/A"
        elena_score = _calculate_elena_score(metrics)

        subject = (
            f"ARIA | Elena {subject_prefix} {audit_date} — "
            f"{total} llamadas | {agendo} citas | "
            f"Conversión {conversion:.1f}% | Score {elena_score}/100"
        )

        html_body = f"""\
<html><body style="font-family:monospace;background:#0f0f0f;color:#e0e0e0;padding:24px">
<h2 style="color:#00d4aa">ARIA — Reporte {subject_prefix} de Elena</h2>
<p style="color:#888">Fecha: {audit_date}</p>
<pre style="background:#1a1a1a;padding:16px;border-radius:8px;font-size:13px;line-height:1.6">{report_text}</pre>
<hr style="border-color:#333">
<p style="color:#555;font-size:11px">Generado automáticamente por ARIA v{ARIA_VERSION}.</p>
</body></html>"""

        msg = MIMEMultipart("alternative")
        msg["From"] = f"ARIA — Elena Monitor <{gmail_from}>"
        msg["To"] = ADMIN_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(report_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_from, gmail_app_password)
            server.send_message(msg)

        log.info(f"Email report sent to {ADMIN_EMAIL}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        log.error(f"Gmail auth error: {e}")
        return False
    except Exception as e:
        log.error(f"Email send error: {e}")
        return False


# ============================================================
# PUNTO DE ENTRADA PRINCIPAL
# ============================================================

def run_audit(hours_back: int = None, dry_run: bool = False):
    """
    Ejecutar el ciclo completo de auditoría.
    Usado por el cron job y por comandos on-demand.
    """
    if hours_back is None:
        hours_back = AUDIT_LOOKBACK_HOURS

    audit_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info(f"Starting ARIA audit run — date={audit_date} hours_back={hours_back} dry_run={dry_run}")

    calls = fetch_vapi_calls(hours_back=hours_back, limit=AUDIT_BATCH_SIZE)

    if not calls:
        log.info("No calls to audit")
        return {"total": 0, "audited": 0, "discrepancies": 0, "conversion_rate": 0, "avg_playbook_score": None, "audit_date": audit_date}

    already_audited = get_already_audited_ids() if not dry_run else set()
    new_calls = [c for c in calls if c.get("id") not in already_audited]
    log.info(f"New calls to audit: {len(new_calls)} (skipping {len(calls) - len(new_calls)} already audited)")

    if not new_calls:
        log.info("All calls already audited")
        return {"total": len(calls), "audited": 0, "discrepancies": 0, "conversion_rate": 0, "avg_playbook_score": None, "audit_date": audit_date}

    results = []
    discrepancies = []

    for i, call in enumerate(new_calls):
        log.info(f"Auditing call {i+1}/{len(new_calls)}: {call.get('id')}")
        if dry_run:
            audit_result = audit_call_with_claude(call)
            result = {
                "call_id": call.get("id"),
                "original_outcome": None,
                "aria_outcome": audit_result.get("correct_outcome"),
                "aria_confidence": audit_result.get("confidence", 0),
                "has_discrepancy": False,
                "audit_status": "dry_run",
                "errors_count": len(audit_result.get("errors_detected", [])),
                "errors_detected_types": [e.get("type") for e in audit_result.get("errors_detected", [])],
                "playbook_score": audit_result.get("playbook_adherence_score"),
                "duration_seconds": audit_result.get("duration_seconds"),
                "quality_notes": audit_result.get("quality_notes", "")
            }
        else:
            result = process_call(call, already_audited)

        if result:
            results.append(result)
            if result.get("has_discrepancy"):
                discrepancies.append(result)

    metrics = calculate_daily_metrics(results, audit_date)

    if not dry_run and metrics:
        supabase_upsert("daily_metrics", metrics, on_conflict="metric_date,agent_name")

    # Verificar alertas de patrón de error
    if not dry_run and results:
        check_error_pattern_alert(results, audit_date)

    summary = {
        "total_fetched": len(calls),
        "new_audited": len(results),
        "discrepancies_found": len(discrepancies),
        "conversion_rate": metrics.get("conversion_rate", 0),
        "avg_playbook_score": metrics.get("avg_playbook_adherence"),
        "audit_date": audit_date
    }

    log.info(f"Audit complete: {summary}")
    return summary


def run_daily_report():
    """
    Generar y enviar el reporte diario a las 8PM EDT.
    Incluye métricas del día + eficacia de ARIA + correcciones.
    """
    audit_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info(f"Generating daily report for {audit_date}")

    # Obtener todos los audits del día
    records = supabase_query(
        "call_audits",
        f"created_at=gte.{audit_date}T00:00:00Z&order=created_at.desc&limit=500"
    )

    results = _records_to_results(records) if records else []
    metrics = calculate_daily_metrics(results, audit_date)
    top_errors = _get_top_errors(records) if records else []
    aria_efficacy = _get_aria_efficacy(days=1)

    # Guardar métricas del día
    if metrics.get("total_calls", 0) > 0:
        supabase_upsert("daily_metrics", metrics, on_conflict="metric_date,agent_name")

    # Enviar por Telegram
    telegram_send_daily_report(metrics, audit_date, top_errors, aria_efficacy)

    # Enviar por email
    report_text = build_report_text(results, metrics, audit_date, aria_efficacy, report_type="daily")
    send_email_report(report_text, audit_date, metrics, subject_prefix="Diario")

    # Verificar alerta de degradación
    check_degradation_alert()

    log.info(f"Daily report sent for {audit_date}")
    return {"date": audit_date, "total_calls": metrics.get("total_calls", 0)}


def run_weekly_report():
    """
    Generar y enviar el reporte semanal los domingos a las 8AM EDT.
    """
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    log.info(f"Generating weekly report: {start_date} → {end_date}")

    weekly_metrics = _build_weekly_metrics(start_date, end_date)

    # Enviar por Telegram
    telegram_send_weekly_report(weekly_metrics, start_date, end_date)

    # Construir métricas para email
    fake_metrics = {
        "total_calls": weekly_metrics.get("total_calls", 0),
        "calls_agendo": weekly_metrics.get("calls_agendo", 0),
        "calls_no_agendo": 0,
        "calls_no_contesto": weekly_metrics.get("calls_no_contesto", 0),
        "calls_llamar_luego": 0,
        "calls_error_tecnico": 0,
        "calls_no_interesado": 0,
        "conversion_rate": weekly_metrics.get("avg_conversion_rate", 0),
        "contact_rate": 0,
        "avg_call_duration_seconds": 0,
        "avg_playbook_adherence": weekly_metrics.get("avg_playbook_adherence"),
        "calls_with_errors": 0,
        "aria_discrepancies_found": weekly_metrics.get("total_discrepancies", 0),
    }
    aria_efficacy = {
        "approved": weekly_metrics.get("total_approved", 0),
        "rejected": weekly_metrics.get("total_rejected", 0)
    }

    report_text = build_report_text([], fake_metrics, f"{start_date} → {end_date}", aria_efficacy, report_type="weekly")
    send_email_report(report_text, f"{start_date}→{end_date}", fake_metrics, subject_prefix="Semanal")

    log.info(f"Weekly report sent: {start_date} → {end_date}")
    return {"start_date": start_date, "end_date": end_date, "total_calls": weekly_metrics.get("total_calls", 0)}


# ============================================================
# POLLING ACTIVO — Auditar llamadas sin depender del webhook
# ============================================================
import threading as _threading
import time as _time

_polling_started = False
_polling_lock = _threading.Lock()


def _aria_polling_loop(interval_seconds: int = 180):
    """
    Loop de polling activo: cada `interval_seconds` consulta Vapi por llamadas
    terminadas en la ultima hora y audita las que no estan en Supabase.
    Cubre inbound Twilio donde Vapi no envia el webhook end-of-call-report.
    """
    log.info(f"ARIA Polling iniciado — intervalo: {interval_seconds}s")
    while True:
        try:
            _time.sleep(interval_seconds)
            log.info("ARIA Polling: buscando llamadas no auditadas...")
            calls = fetch_vapi_calls(hours_back=1, limit=30)
            ended_calls = [c for c in calls if c.get("status") == "ended"]
            if not ended_calls:
                log.info("ARIA Polling: sin llamadas terminadas en la ultima hora")
                continue
            already_audited = get_already_audited_ids()
            pending = [c for c in ended_calls if c.get("id") not in already_audited]
            if not pending:
                log.info(f"ARIA Polling: {len(ended_calls)} llamadas — todas ya auditadas")
                continue
            log.info(f"ARIA Polling: {len(pending)} llamadas pendientes de auditar")
            for call_data in pending:
                call_id = call_data.get("id", "?")
                transcript = call_data.get("transcript", "") or ""
                if len(transcript) < 50:
                    log.info(f"ARIA Polling [{call_id}]: transcript muy corto ({len(transcript)} chars) — saltando")
                    continue
                try:
                    log.info(f"ARIA Polling [{call_id}]: auditando...")
                    result = process_call(call_data, already_audited)
                    if result:
                        log.info(f"ARIA Polling [{call_id}]: auditado — {result.get('audit_status')}")
                        already_audited.add(call_id)
                    else:
                        log.info(f"ARIA Polling [{call_id}]: saltado por process_call")
                except Exception as e:
                    log.error(f"ARIA Polling [{call_id}]: error — {e}")
        except Exception as e:
            log.error(f"ARIA Polling loop error: {e}")


def start_aria_polling(interval_seconds: int = 180):
    """
    Iniciar el loop de polling en un daemon thread.
    Idempotente: solo inicia una vez aunque se llame varias veces.
    """
    global _polling_started
    with _polling_lock:
        if _polling_started:
            log.info("ARIA Polling: ya iniciado, ignorando llamada duplicada")
            return
        _polling_started = True
    t = _threading.Thread(
        target=_aria_polling_loop,
        args=(interval_seconds,),
        daemon=True,
        name="aria-polling"
    )
    t.start()
    log.info(f"ARIA Polling thread iniciado (daemon) — intervalo {interval_seconds}s")


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "audit"

    if mode == "pilot":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        # Pilot mode: auditar N llamadas sin guardar
        calls = fetch_vapi_calls(hours_back=72, limit=50)
        calls = [c for c in calls if len(c.get("transcript", "") or "") > 200][:n]
        for call in calls:
            result = audit_call_with_claude(call)
            print(f"\nCall: {call.get('id')}")
            print(f"ARIA: {result.get('correct_outcome')} ({result.get('confidence', 0)*100:.0f}%)")
            print(f"Errors: {len(result.get('errors_detected', []))}")

    elif mode == "audit":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else AUDIT_LOOKBACK_HOURS
        summary = run_audit(hours_back=hours)
        print(f"\nAudit complete: {summary}")

    elif mode == "dry-run":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        summary = run_audit(hours_back=hours, dry_run=True)
        print(f"\nDry-run complete: {summary}")

    elif mode == "daily-report":
        result = run_daily_report()
        print(f"\nDaily report sent: {result}")

    elif mode == "weekly-report":
        result = run_weekly_report()
        print(f"\nWeekly report sent: {result}")

    elif mode == "check-alerts":
        check_degradation_alert()
        print("Alert check complete")

    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python3.11 aria_audit.py [pilot|audit|dry-run|daily-report|weekly-report|check-alerts] [args]")
        sys.exit(1)
