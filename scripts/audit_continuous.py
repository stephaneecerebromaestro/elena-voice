#!/usr/bin/env python3
"""
scripts/audit_continuous.py
═══════════════════════════════════════════════════════════════════════════
Auditoría continua semanal de Elena Voice — Botox + LHR por separado.

Corre cada lunes 8am Miami. Para cada assistant de config.ASSISTANTS:
  1. Trae llamadas Vapi de los últimos 7 días
  2. Cruza con ARIA (Supabase call_audits) para outcomes clasificados
  3. Calcula métricas: volumen, % contesta, % agendadas, costo por booking
  4. Detecta patrones: top razones no_agendo, errores, loops check_availability
  5. Compara vs semana anterior (mejora/empeora)
  6. Si conversión cae > 20% vs semana anterior → alerta Telegram a Juan
  7. Escribe reporte markdown en audits/YYYY-MM-DD-weekly.md
  8. Envía resumen a Telegram

Uso:
    python3 scripts/audit_continuous.py              # modo normal
    python3 scripts/audit_continuous.py --dry-run    # no envía Telegram, no escribe archivo
    python3 scripts/audit_continuous.py --days 14    # ventana custom
    python3 scripts/audit_continuous.py --end 2026-04-14  # termina en fecha dada (Miami)

Reglas operativas:
    - NO llama a pacientes reales (solo lee data histórica)
    - NO escribe tags/fields en GHL (solo lee)
    - Si falla Telegram, el reporte markdown sigue escribiéndose
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytz
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import ASSISTANTS  # noqa: E402
from aria_audit import supabase_query, telegram_send  # noqa: E402

# ─── Config ──────────────────────────────────────────────────────────────────
EDT = pytz.timezone("America/New_York")
VAPI_API = "https://api.vapi.ai/call"
VAPI_KEY = os.environ.get("VAPI_API_KEY", "")
CONVERSION_DROP_ALERT = 0.20  # alerta si cae >= 20% vs semana anterior
AUDITS_DIR = REPO_ROOT / "audits"

logging.basicConfig(
    format="%(asctime)s [audit_continuous] %(levelname)s — %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── Utilidades de fechas ────────────────────────────────────────────────────
def iso_utc(dt: datetime) -> str:
    """ISO UTC con milisegundos (formato que Vapi acepta)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def miami_midnight(d: datetime) -> datetime:
    """Medianoche Miami para la fecha dada."""
    return EDT.localize(datetime(d.year, d.month, d.day, 0, 0, 0))


def week_window(end_dt_miami: datetime, days: int = 7) -> tuple[str, str]:
    """(utc_start, utc_end) — `days` días terminando a medianoche Miami."""
    end = miami_midnight(end_dt_miami)
    start = end - timedelta(days=days)
    return iso_utc(start), iso_utc(end)


# ─── Vapi ────────────────────────────────────────────────────────────────────
class VapiFetchError(RuntimeError):
    """Raised when Vapi is unreachable after all retries — aborts the audit
    instead of silently reporting zero calls (which would spam false alerts)."""


def fetch_vapi_calls(assistant_id: str, utc_start: str, utc_end: str,
                      limit: int = 1000, max_retries: int = 3) -> list[dict]:
    """
    Trae llamadas de Vapi filtradas por assistantId y rango UTC.
    Reintenta con backoff exponencial en 5xx/network errors.
    Lanza VapiFetchError si Vapi falla persistentemente — así el caller
    puede abortar en vez de reportar "0 llamadas" falsamente.
    """
    if not VAPI_KEY:
        raise VapiFetchError("VAPI_API_KEY no configurado")

    import time
    last_err = None
    for attempt in range(max_retries):
        try:
            r = requests.get(
                VAPI_API,
                headers={"Authorization": f"Bearer {VAPI_KEY}"},
                params={
                    "limit": limit,
                    "assistantId": assistant_id,
                    "createdAtGt": utc_start,
                    "createdAtLt": utc_end,
                },
                timeout=30,
            )
        except Exception as e:
            last_err = f"network: {e}"
            log.warning(f"Vapi fetch attempt {attempt + 1}/{max_retries} failed: {last_err}")
        else:
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            # 401/403 are permanent — no point retrying
            if r.status_code in (401, 403):
                raise VapiFetchError(last_err)
            log.warning(f"Vapi fetch attempt {attempt + 1}/{max_retries} failed: {last_err}")
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)  # 1s, 2s, 4s
    raise VapiFetchError(f"after {max_retries} retries: {last_err}")


# ─── ARIA (Supabase) ─────────────────────────────────────────────────────────
def fetch_audits(utc_start: str, utc_end: str) -> dict[str, dict]:
    """Trae auditorías ARIA del rango. Devuelve dict {vapi_call_id: audit_row}."""
    try:
        records = supabase_query(
            "call_audits",
            f"call_started_at=gte.{utc_start}&call_started_at=lt.{utc_end}&limit=2000",
        )
    except Exception as e:
        log.error(f"Supabase audits query exception: {e}")
        return {}
    return {r["vapi_call_id"]: r for r in records if r.get("vapi_call_id")}


# ─── Análisis de una llamada ─────────────────────────────────────────────────
def extract_outcome(call: dict, audit: dict | None) -> str:
    """
    Resuelve outcome de una llamada. Orden de precedencia:
      1. aria_outcome si hay auditoría (verdad clasificada por ARIA)
      2. original_outcome si hay auditoría pero no ARIA
      3. no_contesto si transcript < 50 chars (heurística estándar ARIA)
      4. unknown si Vapi tiene transcript pero ARIA no la auditó todavía
    """
    if audit:
        o = audit.get("aria_outcome") or audit.get("original_outcome")
        if o:
            return o
    transcript = (call.get("transcript") or "").strip()
    if len(transcript) < 50:
        return "no_contesto"
    return "unknown"


def count_check_availability_loops(call: dict) -> int:
    """Cuenta cuántas veces se invocó check_availability en la llamada."""
    msgs = ((call.get("artifact") or {}).get("messages") or [])
    count = 0
    for m in msgs:
        if m.get("role") != "tool_calls":
            continue
        for tc in m.get("toolCalls") or []:
            fn = (tc.get("function") or {}).get("name") or ""
            if fn == "check_availability":
                count += 1
    return count


# ─── Agregación por assistant ────────────────────────────────────────────────
def compute_stats(calls: list[dict], audits: dict[str, dict]) -> dict[str, Any]:
    """Calcula stats completos para un assistant en una ventana de tiempo."""
    ended = [c for c in calls if c.get("status") == "ended"]
    total = len(ended)

    outcomes: Counter[str] = Counter()
    error_types: Counter[str] = Counter()
    no_agendo_reasons: Counter[str] = Counter()
    loop_calls = 0  # llamadas con 3+ check_availability consecutivos
    audited_count = 0
    total_cost = 0.0
    durations = []

    for call in ended:
        cid = call.get("id")
        audit = audits.get(cid)
        if audit:
            audited_count += 1

        outcome = extract_outcome(call, audit)
        outcomes[outcome] += 1

        total_cost += float(call.get("cost") or 0)

        # Duración preferida: campo computado por ARIA, fallback a Vapi timestamps
        if audit and audit.get("call_duration_seconds"):
            durations.append(float(audit["call_duration_seconds"]))
        elif call.get("startedAt") and call.get("endedAt"):
            try:
                s = datetime.fromisoformat(call["startedAt"].replace("Z", "+00:00"))
                e = datetime.fromisoformat(call["endedAt"].replace("Z", "+00:00"))
                durations.append((e - s).total_seconds())
            except Exception:
                pass

        # Errores detectados por ARIA
        if audit:
            errs = audit.get("errors_detected") or []
            if isinstance(errs, str):
                try:
                    errs = json.loads(errs)
                except Exception:
                    errs = []
            for err in errs:
                t = err.get("type") if isinstance(err, dict) else str(err)
                if t:
                    error_types[t] += 1

            # Razones de no_agendo (resumen ARIA, primeras 140 chars)
            if outcome == "no_agendo":
                summary = (audit.get("aria_summary") or "").strip()
                if summary:
                    no_agendo_reasons[summary[:140]] += 1

        # Loops check_availability (≥3 invocaciones en la misma llamada)
        if count_check_availability_loops(call) >= 3:
            loop_calls += 1

    agendo = outcomes.get("agendo", 0)
    no_contesto = outcomes.get("no_contesto", 0)
    connected = total - no_contesto
    conversion = (agendo / connected) if connected > 0 else 0.0
    contact_rate = (connected / total) if total > 0 else 0.0
    avg_cost = (total_cost / total) if total > 0 else 0.0
    cost_per_booking = (total_cost / agendo) if agendo > 0 else None
    avg_dur = (sum(durations) / len(durations)) if durations else 0.0
    coverage = (audited_count / total) if total > 0 else 0.0

    return {
        "total_calls": total,
        "audited": audited_count,
        "audit_coverage": coverage,
        "outcomes": dict(outcomes),
        "agendo": agendo,
        "no_contesto": no_contesto,
        "connected": connected,
        "conversion_rate": conversion,
        "contact_rate": contact_rate,
        "total_cost": total_cost,
        "avg_cost": avg_cost,
        "cost_per_booking": cost_per_booking,
        "avg_duration_s": avg_dur,
        "top_errors": error_types.most_common(5),
        "top_no_agendo_reasons": no_agendo_reasons.most_common(3),
        "check_availability_loop_calls": loop_calls,
    }


def run_for_assistant(assistant_id: str, cfg: dict, utc_cur: tuple[str, str],
                      utc_prev: tuple[str, str]) -> dict[str, Any]:
    log.info(f"Auditando {cfg['name']} ({cfg['treatment']})")

    # VapiFetchError propaga hasta main() para abortar el envío de Telegram
    # cuando Vapi está caído — evita falsas alertas de "0 llamadas".
    calls_cur = fetch_vapi_calls(assistant_id, *utc_cur)
    calls_prev = fetch_vapi_calls(assistant_id, *utc_prev)
    audits_cur = fetch_audits(*utc_cur)
    audits_prev = fetch_audits(*utc_prev)

    log.info(f"  {cfg['treatment']}: {len(calls_cur)} calls actuales, "
             f"{len(calls_prev)} semana anterior, "
             f"{len(audits_cur)} audits actuales")

    cur = compute_stats(calls_cur, audits_cur)
    prev = compute_stats(calls_prev, audits_prev)

    delta_conversion = cur["conversion_rate"] - prev["conversion_rate"]
    alert = False
    if prev["conversion_rate"] > 0:
        rel = (prev["conversion_rate"] - cur["conversion_rate"]) / prev["conversion_rate"]
        if rel >= CONVERSION_DROP_ALERT and cur["connected"] >= 5:
            alert = True

    return {
        "assistant_id": assistant_id,
        "config": cfg,
        "current": cur,
        "previous": prev,
        "delta_conversion_abs": delta_conversion,
        "alert_drop": alert,
    }


# ─── Formatters: Markdown + Telegram ─────────────────────────────────────────
def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _usd(x: float | None) -> str:
    return f"${x:.2f}" if x is not None else "—"


def _delta_arrow(delta: float) -> str:
    if delta > 0.005:
        return "▲"
    if delta < -0.005:
        return "▼"
    return "≈"


def format_markdown(results: list[dict], utc_cur: tuple[str, str],
                     utc_prev: tuple[str, str], now_miami: datetime) -> str:
    cur_start = datetime.fromisoformat(utc_cur[0].replace("Z", "+00:00")).astimezone(EDT).date()
    cur_end = datetime.fromisoformat(utc_cur[1].replace("Z", "+00:00")).astimezone(EDT).date()
    prev_start = datetime.fromisoformat(utc_prev[0].replace("Z", "+00:00")).astimezone(EDT).date()

    lines = [
        f"# AUDITORÍA SEMANAL — Elena Voice",
        f"# Semana: {cur_start} al {cur_end} (vs semana anterior desde {prev_start})",
        f"# Generado: {now_miami.strftime('%Y-%m-%d %H:%M %Z')}",
        "# ════════════════════════════════════════════════════════════",
        "",
        "## RESUMEN EJECUTIVO",
        "",
        "| Tratamiento | Llamadas | Contesta | Agendó | Conversión | Δ vs semana ant. | Costo/booking |",
        "|-------------|---------:|---------:|-------:|-----------:|:----------------:|--------------:|",
    ]
    for r in results:
        cfg = r["config"]
        c = r["current"]
        arrow = _delta_arrow(r["delta_conversion_abs"])
        alert_flag = " 🚨" if r["alert_drop"] else ""
        lines.append(
            f"| {cfg['treatment'].upper()} | {c['total_calls']} | "
            f"{_pct(c['contact_rate'])} | {c['agendo']} | "
            f"{_pct(c['conversion_rate'])} | {arrow} "
            f"{c['conversion_rate'] - r['previous']['conversion_rate']:+.1%}{alert_flag} | "
            f"{_usd(c['cost_per_booking'])} |"
        )

    if any(r["alert_drop"] for r in results):
        lines += [
            "",
            "> 🚨 **ALERTA**: al menos un tratamiento cayó ≥20% en conversión vs semana anterior. "
            "Revisar secciones detalladas y transcripts recientes.",
        ]

    # Secciones detalladas por assistant
    for r in results:
        cfg = r["config"]
        c = r["current"]
        p = r["previous"]
        lines += [
            "",
            "---",
            "",
            f"## {cfg['name']} — {cfg['treatment'].upper()}",
            "",
            f"**assistant_id:** `{r['assistant_id']}`",
            f"**calendar_id:** `{cfg['calendar_id']}` · **pipeline_id:** `{cfg['pipeline_id']}`",
            "",
            "### Métricas",
            "",
            "| Métrica | Esta semana | Semana anterior |",
            "|---|---:|---:|",
            f"| Total llamadas | {c['total_calls']} | {p['total_calls']} |",
            f"| Contestadas (conectaron) | {c['connected']} ({_pct(c['contact_rate'])}) | {p['connected']} ({_pct(p['contact_rate'])}) |",
            f"| Agendaron | {c['agendo']} | {p['agendo']} |",
            f"| Conversión (agendó / contestó) | **{_pct(c['conversion_rate'])}** | {_pct(p['conversion_rate'])} |",
            f"| Duración promedio | {c['avg_duration_s']:.0f}s | {p['avg_duration_s']:.0f}s |",
            f"| Costo total | {_usd(c['total_cost'])} | {_usd(p['total_cost'])} |",
            f"| Costo por llamada | {_usd(c['avg_cost'])} | {_usd(p['avg_cost'])} |",
            f"| Costo por booking | {_usd(c['cost_per_booking'])} | {_usd(p['cost_per_booking'])} |",
            f"| Cobertura auditoría ARIA | {_pct(c['audit_coverage'])} ({c['audited']}/{c['total_calls']}) | {_pct(p['audit_coverage'])} |",
            "",
            "### Desglose de outcomes",
            "",
            "| Outcome | Cantidad | % |",
            "|---|---:|---:|",
        ]
        for oc in ["agendo", "no_contesto", "no_agendo", "llamar_luego",
                   "no_interesado", "error_tecnico", "unknown"]:
            n = c["outcomes"].get(oc, 0)
            pct = (n / c["total_calls"] * 100) if c["total_calls"] else 0
            lines.append(f"| {oc} | {n} | {pct:.1f}% |")

        if c["top_no_agendo_reasons"]:
            lines += ["", "### Top 3 razones de no_agendo", ""]
            for reason, n in c["top_no_agendo_reasons"]:
                lines.append(f"- **({n})** {reason}")
        else:
            lines += ["", "### Top 3 razones de no_agendo", "",
                      "_Sin llamadas no_agendo auditadas esta semana._"]

        if c["top_errors"]:
            lines += ["", "### Top errores detectados por ARIA", "",
                      "| Error | Ocurrencias |", "|---|---:|"]
            for err_type, n in c["top_errors"]:
                lines.append(f"| {err_type} | {n} |")

        lines += [
            "",
            "### Patrones de calidad",
            "",
            f"- Llamadas con **loop de check_availability** (≥3 invocaciones): "
            f"**{c['check_availability_loop_calls']}** "
            f"({(c['check_availability_loop_calls'] / c['total_calls'] * 100) if c['total_calls'] else 0:.1f}%)",
        ]

    lines += [
        "",
        "---",
        "",
        "## Metodología",
        "",
        "- **Fuente de verdad para totales:** Vapi API (status = `ended`)",
        "- **Clasificación de outcome:** `call_audits.aria_outcome` en Supabase; "
        "llamadas con transcript <50 chars se marcan como `no_contesto` sin necesidad de auditoría",
        "- **Top razones no_agendo:** `aria_summary` de auditorías con outcome `no_agendo`",
        "- **Loops check_availability:** llamadas con ≥3 invocaciones de la tool en `artifact.messages`",
        "- **Umbral de alerta:** caída relativa ≥20% en conversión vs semana anterior "
        "(requiere ≥5 llamadas contestadas para evitar falsos positivos en volumen bajo)",
        "",
        f"_Generado por `scripts/audit_continuous.py` el {now_miami.strftime('%Y-%m-%d %H:%M %Z')}_",
    ]
    return "\n".join(lines)


def format_telegram_summary(results: list[dict],
                             cur_start_date: str, cur_end_date: str) -> str:
    lines = [
        "📊 <b>Auditoría semanal Elena Voice</b>",
        f"<i>{cur_start_date} → {cur_end_date}</i>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for r in results:
        cfg = r["config"]
        c = r["current"]
        p = r["previous"]
        arrow = _delta_arrow(r["delta_conversion_abs"])
        delta_pp = (c["conversion_rate"] - p["conversion_rate"]) * 100
        flag = " 🚨" if r["alert_drop"] else ""
        lines += [
            "",
            f"<b>{cfg['treatment'].upper()}</b>{flag}",
            f"  📞 {c['total_calls']} llamadas · contesta {_pct(c['contact_rate'])}",
            f"  ✅ {c['agendo']} agendó · conversión <b>{_pct(c['conversion_rate'])}</b>",
            f"  {arrow} vs semana ant.: {delta_pp:+.1f}pp "
            f"(antes {_pct(p['conversion_rate'])})",
            f"  💰 costo/booking: {_usd(c['cost_per_booking'])}",
        ]
        if c["top_no_agendo_reasons"]:
            top = c["top_no_agendo_reasons"][0]
            lines.append(f"  🔍 Top no_agendo: <i>{top[0][:80]}</i> ({top[1]})")
        if c["check_availability_loop_calls"] > 0:
            lines.append(
                f"  ⚠️ {c['check_availability_loop_calls']} llamadas con loop check_availability"
            )

    if any(r["alert_drop"] for r in results):
        lines += [
            "",
            "🚨 <b>ALERTA:</b> conversión cayó ≥20% en algún tratamiento. "
            "Ver reporte completo en <code>audits/</code>.",
        ]

    return "\n".join(lines)


# ─── Entrada ─────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1])
    parser.add_argument("--dry-run", action="store_true",
                        help="No escribe archivo ni envía Telegram")
    parser.add_argument("--days", type=int, default=7, help="Tamaño de ventana (default 7)")
    parser.add_argument("--end", type=str,
                        help="Fecha final YYYY-MM-DD Miami (default: hoy)")
    args = parser.parse_args()

    now_miami = datetime.now(EDT)
    if args.end:
        end_dt = EDT.localize(datetime.strptime(args.end, "%Y-%m-%d"))
    else:
        end_dt = now_miami

    utc_cur = week_window(end_dt, days=args.days)
    utc_prev = week_window(end_dt - timedelta(days=args.days), days=args.days)

    log.info(f"Ventana actual: {utc_cur[0]} → {utc_cur[1]}")
    log.info(f"Ventana anterior: {utc_prev[0]} → {utc_prev[1]}")

    results = []
    vapi_failures = []
    for assistant_id, cfg in ASSISTANTS.items():
        try:
            results.append(run_for_assistant(assistant_id, cfg, utc_cur, utc_prev))
        except VapiFetchError as e:
            log.error(f"Vapi falló para {cfg.get('name', assistant_id)}: {e}")
            vapi_failures.append((cfg.get("name", assistant_id), str(e)))
        except Exception as e:
            log.exception(f"Error auditando {cfg.get('name', assistant_id)}: {e}")

    if vapi_failures:
        # Abortamos el reporte: enviar "0 llamadas" cuando Vapi está caído
        # dispararía una falsa alerta de caída total. Notificamos a Juan
        # en un canal distinto: log + Telegram corto pidiendo revisar.
        msg = "⚠️ <b>Auditoría semanal Elena Voice — abortada</b>\n"
        msg += "Vapi no respondió al fetch de llamadas. <b>No se generó reporte</b>.\n\n"
        for name, err in vapi_failures:
            msg += f"• {name}: <code>{err[:140]}</code>\n"
        msg += "\nReintentar manualmente: <code>scripts/run_weekly_audit.sh</code>"
        if not args.dry_run:
            telegram_send(msg)
        else:
            log.info("[dry-run] hubiera enviado alerta de fallo Vapi a Telegram")
        return 2

    if not results:
        log.error("Sin resultados — abortando.")
        return 1

    md = format_markdown(results, utc_cur, utc_prev, now_miami)

    cur_end_date = datetime.fromisoformat(utc_cur[1].replace("Z", "+00:00"))\
        .astimezone(EDT).strftime("%Y-%m-%d")
    cur_start_date = datetime.fromisoformat(utc_cur[0].replace("Z", "+00:00"))\
        .astimezone(EDT).strftime("%Y-%m-%d")

    out_path = AUDITS_DIR / f"{cur_end_date}-weekly.md"

    if args.dry_run:
        log.info("--dry-run: no se escribe archivo ni se envía Telegram")
        print(md)
        print("\n\n─── Telegram summary (dry-run) ───\n")
        print(format_telegram_summary(results, cur_start_date, cur_end_date))
        return 0

    AUDITS_DIR.mkdir(exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    log.info(f"Reporte escrito: {out_path}")

    tg = format_telegram_summary(results, cur_start_date, cur_end_date)
    sent = telegram_send(tg)
    if sent:
        log.info("Resumen enviado a Telegram")
    else:
        log.warning("Telegram no enviado (revisar TELEGRAM_BOT_TOKEN/CHAT_ID)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
