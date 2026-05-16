#!/usr/bin/env python3
"""
apply_critical_fixes.py — Aplica los fixes críticos v2.3 a uno o todos los bots.

Los fixes se detectan por keywords. Si el prompt ya los tiene, se salta.
Idempotente: se puede correr N veces sin duplicar texto.

Uso:
    python3 scripts/apply_critical_fixes.py --bot botox
    python3 scripts/apply_critical_fixes.py --all
    python3 scripts/apply_critical_fixes.py --all --dry-run

Fixes incluidos (v2.3 — 2026-05-16):
  Fix1: Anti-loop herramientas en silencio/buzón (get_current_time loop)
  Fix2: Bloqueo create_booking si no hubo humano real (ghost booking)
  Fix3: Reconocimiento de recepcionista → esperar 15s
  Fix4: check_availability por DÍA solicitado (no por llamada completa)
  Fix5: Rechazo en inglés → cerrar en inglés (no pre-reserva forzada)
  Fix6: Definiciones mejoradas no_contesto vs no_agendo en analysisPlan
"""

from __future__ import annotations
import argparse, os, sys, json, requests
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
VAPI_API = "https://api.vapi.ai"

from update_prompt import BOTS


# ────────────────────────────────────────────────
# DEFINICIÓN DE CADA FIX
# ────────────────────────────────────────────────

def fix1_antiloop(prompt: str) -> tuple[str, bool]:
    """Prohibición de herramientas durante silencio y buzón"""
    keyword = "PROHIBICIÓN EN BUZÓN"
    if keyword in prompt:
        return prompt, False
    variants = [
        "4. ANTI-LOOP DE HERRAMIENTAS: NUNCA ejecutes la misma herramienta dos veces en el mismo turno. Si una herramienta falla o no devuelve lo esperado, usa el lenguaje natural para manejarlo, no la vuelvas a llamar inmediatamente.",
        "4. ANTI-LOOP DE HERRAMIENTAS: NUNCA ejecutes la misma herramienta dos veces en el mismo turno.",
    ]
    addition = (
        "\n   PROHIBICIÓN EN SILENCIO: Durante el protocolo de 3 intentos (\"¿Me escuchas bien?\"), "
        "PROHIBIDO ejecutar herramientas de ningún tipo. Solo habla."
        "\n   PROHIBICIÓN EN BUZÓN: Una vez detectada señal de buzón de voz (regla 6), la ÚNICA acción permitida "
        "es endCall. Ninguna herramienta — incluyendo get_current_time — es ejecutable después de detectar un buzón."
    )
    for v in variants:
        if v in prompt:
            return prompt.replace(v, v + addition), True
    return prompt, False


def fix2_ghost_booking(prompt: str) -> tuple[str, bool]:
    """Bloquear create_booking si no hubo respuesta humana"""
    keyword = "PROHIBICIÓN ABSOLUTA BUZÓN"
    if keyword in prompt:
        return prompt, False
    target = "- PROHIBIDO: NUNCA ejecutes create_booking si el cliente solo dijo \"quiero agendar\" pero aún no ha elegido la hora."
    addition = (
        "\n- PROHIBICIÓN ABSOLUTA BUZÓN: NUNCA ejecutes create_booking si el transcript contiene señales "
        "de buzón (\"deje su mensaje\", \"at the tone\", \"leave a message\", beep) o si no hubo respuesta "
        "humana real. Si no estás segura de si había un humano, NO ejecutes create_booking."
    )
    if target in prompt:
        return prompt.replace(target, target + addition), True
    return prompt, False


def fix3_receptionist(prompt: str) -> tuple[str, bool]:
    """Reconocer recepcionista → esperar 15s"""
    keyword = "EXCEPCIÓN RECEPCIONISTA"
    if keyword in prompt:
        return prompt, False
    target = "   REGLA: Si no hay señal clara de persona humana en el primer turno, cuelga."
    addition = (
        "   EXCEPCIÓN RECEPCIONISTA: Si escuchas frases como \"I'll see if this person is available\", "
        "\"Please stay on the line\", \"Let me check if they're available\", \"Voy a ver si está disponible\", "
        "\"Un momento, se lo comunico\" → NO cuelgues. Espera hasta 15 segundos. Si el cliente real contesta, "
        "continúa con STATE 1. Si no hay respuesta en 15 segundos: "
        "\"¿Me puede decir que soy Elena de Laser Place y que me llame al 786-743-0129?\" → endCall. "
        "Estas frases son recepcionistas humanos, NO buzones.\n"
    )
    if target in prompt:
        return prompt.replace(target, addition + target), True
    return prompt, False


def fix4_checkavail_day(prompt: str) -> tuple[str, bool]:
    """check_availability por día solicitado, no por llamada completa"""
    if "por DÍA solicitado" in prompt or "por cada DÍA distinto" in prompt:
        return prompt, False
    variants = [
        ("- FIX D: NO llamar check_availability más de una vez por llamada, a menos que el cliente pida un día y los slots que ya tienes sean de una semana anterior.",
         "- FIX D: NO llamar check_availability más de una vez por DÍA solicitado. Si el cliente pide martes y luego pide jueves, puedes llamar check_availability para jueves. No la llames dos veces para el mismo día dentro de la misma llamada, a menos que los slots sean de una semana anterior."),
        ("- Máximo 2 llamadas a check_availability por conversación.",
         "- Máximo 2 llamadas a check_availability por conversación, pero puedes hacer una por cada DÍA distinto que el cliente solicite."),
    ]
    for old, new in variants:
        if old in prompt:
            return prompt.replace(old, new), True
    return prompt, False


def fix5_english_rejection(prompt: str) -> tuple[str, bool]:
    """Rechazo en inglés → cerrar en inglés"""
    keyword = "EXCEPCIÓN RECHAZO EN INGLÉS"
    if keyword in prompt:
        return prompt, False
    target = "- Si insiste en que la llames luego -> Di \"Perfecto, te llamo [mañana/en unas horas/la próxima semana]. ¡Que tengas un excelente día!\" y ejecuta endCall. GHL programa el callback automáticamente."
    addition = (
        "\nEXCEPCIÓN RECHAZO EN INGLÉS: Si el cliente rechazó con una frase completa en inglés "
        "(\"Sorry, I can't talk right now\", \"I'm not interested\", \"Please don't call me\"), "
        "NO uses el script de pre-reserva en español. Cierra directamente en inglés: "
        "\"I understand, sorry for the interruption. Have a great day!\" → endCall."
    )
    if target in prompt:
        return prompt.replace(target, target + addition), True
    return prompt, False


def fix6_outcome_schema(agent: dict) -> tuple[dict, bool]:
    """Mejorar definiciones no_contesto / no_agendo"""
    keyword = "USAR SI no hubo respuesta humana"
    try:
        props = agent['analysisPlan']['structuredDataPlan']['schema']['properties']
        if keyword in props.get('outcome', {}).get('description', ''):
            return agent, False
        props['outcome']['description'] = (
            "Resultado final de la llamada. "
            "no_contesto=USAR SI no hubo respuesta humana real: buzón de voz, IVR automático, silencio sin respuesta, "
            "o el 'usuario' solo generó mensajes de sistema (beep, 'deje su mensaje', 'at the tone', 'leave a message'). "
            "Si Elena habló pero nadie respondió como humano REAL → no_contesto. "
            "no_agendo=USAR SOLO SI hubo conversación real con un humano (el cliente respondió preguntas, expresó opinión, "
            "dio información) Y la llamada terminó sin cita creada. Si no hubo respuesta humana clara → no_contesto, NO no_agendo. "
            "agendo=cita creada y confirmada verbalmente por el cliente. "
            "llamar_luego=cliente pidió callback explícitamente. "
            "error_tecnico=error del sistema que impidió la conversación."
        )
        return agent, True
    except (KeyError, TypeError):
        return agent, False


ALL_PROMPT_FIXES = [fix1_antiloop, fix2_ghost_booking, fix3_receptionist, fix4_checkavail_day, fix5_english_rejection]


# ────────────────────────────────────────────────
# APLICAR A UN BOT
# ────────────────────────────────────────────────

def apply_to_bot(bot_name: str, api_key: str, dry_run: bool = False) -> bool:
    info = BOTS.get(bot_name)
    if not info:
        print(f"❌ Bot desconocido: {bot_name}")
        return False

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    r = requests.get(f"{VAPI_API}/assistant/{info['assistant_id']}", headers=headers, timeout=30)
    if r.status_code != 200:
        print(f"❌ Error leyendo {bot_name}: {r.status_code}")
        return False

    agent = r.json()
    msgs = agent.get('model', {}).get('messages', [])
    sys_idx = next((i for i, m in enumerate(msgs) if m.get('role') == 'system'), None)
    if sys_idx is None:
        print(f"❌ {bot_name}: no system message")
        return False

    prompt = msgs[sys_idx]['content']
    applied = []

    for fix_fn in ALL_PROMPT_FIXES:
        prompt, changed = fix_fn(prompt)
        if changed:
            applied.append(fix_fn.__name__)

    agent, changed = fix6_outcome_schema(agent)
    if changed:
        applied.append("fix6_outcome_schema")

    if not applied:
        print(f"  ⏭️  {bot_name}: todos los fixes ya aplicados")
        return True

    for fn_name in applied:
        print(f"  ✅ {bot_name}: {fn_name}")

    if dry_run:
        print(f"  (dry-run — no se publicó)")
        return True

    msgs[sys_idx]['content'] = prompt
    agent['model']['messages'] = msgs

    payload = {"model": agent["model"], "analysisPlan": agent["analysisPlan"]}
    r2 = requests.patch(f"{VAPI_API}/assistant/{info['assistant_id']}", headers=headers, json=payload, timeout=30)
    if r2.status_code >= 300:
        print(f"  ❌ PATCH falló: {r2.status_code}")
        return False

    # Actualizar mirror local
    mirror = info.get('mirror')
    if mirror and Path(mirror).exists():
        Path(mirror).write_text(prompt, encoding='utf-8')
        print(f"  📄 Mirror actualizado: {Path(mirror).name}")

    print(f"  ✅ {bot_name}: PATCH exitoso — updatedAt: {r2.json().get('updatedAt')}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--bot", choices=sorted(BOTS.keys()), help="Bot específico")
    parser.add_argument("--all", action="store_true", help="Todos los bots")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.bot and not args.all:
        parser.print_help()
        return 1

    api_key = os.environ.get("VAPI_API_KEY", "")
    if not api_key:
        try:
            api_key = open('/root/.secrets/vapi_key').read().strip()
        except FileNotFoundError:
            print("ERROR: VAPI_API_KEY no configurado", file=sys.stderr)
            return 2

    bots = list(BOTS.keys()) if args.all else [args.bot]

    for bot in bots:
        apply_to_bot(bot, api_key, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
