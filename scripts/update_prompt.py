#!/usr/bin/env python3
"""
scripts/update_prompt.py
═══════════════════════════════════════════════════════════════════════════
Publica un prompt nuevo a Vapi para el assistant indicado, preservando
tools y analysisPlan. Además actualiza el mirror local correspondiente
(system_prompt.txt / system_prompt_lhr.txt) con el header actualizado.

Reemplaza al viejo update_vapi_prompt.py (hardcoded a Botox, paths viejos).

Uso:
    # Publicar desde el mirror local (edits ya hechos en el archivo)
    python3 scripts/update_prompt.py --bot botox
    python3 scripts/update_prompt.py --bot lhr

    # Publicar desde un archivo específico
    python3 scripts/update_prompt.py --bot botox --from /tmp/new_prompt.txt

    # Dry-run: mostrar diff sin aplicar
    python3 scripts/update_prompt.py --bot botox --dry-run

El flujo canónico para editar un prompt:
    1. Editar system_prompt{,_lhr}.txt localmente (mantener el header intacto)
    2. python3 scripts/update_prompt.py --bot <botox|lhr> --dry-run   (revisar)
    3. python3 scripts/update_prompt.py --bot <botox|lhr>             (aplicar)
    4. Probar con una llamada real
    5. git add + commit + push
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import date
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

VAPI_API = "https://api.vapi.ai"

# Mapeo bot → assistant_id + mirror. Consistente con check_prompt_drift.py
# y con config.ASSISTANTS.
BOTS = {
    "botox": {
        "assistant_id": "1631c7cf-2914-45f9-bf82-6635cdf00aba",
        "mirror": REPO_ROOT / "system_prompt.txt",
        "label": "Botox",
    },
    "lhr": {
        "assistant_id": "3d5b77b5-f36c-4b95-88bc-4d6484277380",
        "mirror": REPO_ROOT / "system_prompt_lhr.txt",
        "label": "LHR",
    },
    "acne": {
        "assistant_id": "77392648-047e-4a40-9f8a-4f125f2ed6d6",
        "mirror": REPO_ROOT / "system_prompt_acne.txt",
        "label": "Acné",
    },
    "cicatrices": {
        "assistant_id": "b6b09524-06da-4bf7-b518-a71b6a1c7d8b",
        "mirror": REPO_ROOT / "system_prompt_cicatrices.txt",
        "label": "Cicatrices",
    },
    "fillers": {
        "assistant_id": "a9494200-af37-485c-b0fb-fb85479b17a7",
        "mirror": REPO_ROOT / "system_prompt_fillers.txt",
        "label": "Fillers",
    },
    "radiesse": {
        "assistant_id": "39bd6450-055e-4839-9c27-6522e08e8423",
        "mirror": REPO_ROOT / "system_prompt_radiesse.txt",
        "label": "Bioestimuladores",
    },
    "rejuvenecimiento": {
        "assistant_id": "65b3a4b0-2e08-471f-af56-e091e47f26bd",
        "mirror": REPO_ROOT / "system_prompt_rejuvenecimiento.txt",
        "label": "Rejuvenecimiento",
    },
}


def sha16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def strip_header(text: str) -> str:
    """Quita el bloque de comentarios de header (mismo criterio que drift check)."""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and (lines[i].lstrip().startswith("#") or lines[i].strip() == ""):
        i += 1
    return "".join(lines[i:]).strip("\n")


def build_header(bot: str, info: dict, content: str, first_message: str) -> str:
    sha = sha16(content)
    return (
        f"# {info['mirror'].name} — Elena Voice {info['label']} (LIVE mirror)\n"
        f"# ════════════════════════════════════════════════════════════\n"
        f"# FUENTE DE VERDAD: este archivo es un MIRROR del prompt live\n"
        f"# del assistant de {info['label']} en Vapi. Para cambios en producción\n"
        f"# usa scripts/update_prompt.py --bot {bot}. Este archivo se regenera\n"
        f"# con el header actualizado después de cada publicación.\n"
        f"#\n"
        f"# Pulled: {date.today().isoformat()}\n"
        f"# Assistant ID: {info['assistant_id']} ({info['label']})\n"
        f"# Content SHA (prompt body): {sha}\n"
        f"# First message: \"{first_message}\"\n"
        f"# ════════════════════════════════════════════════════════════\n\n"
    )


def get_assistant(api_key: str, assistant_id: str) -> dict:
    r = requests.get(f"{VAPI_API}/assistant/{assistant_id}",
                     headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def summarize_diff(old: str, new: str) -> str:
    if old == new:
        return "  (idénticos — nada que publicar)"
    lines_old = old.splitlines()
    lines_new = new.splitlines()
    added = len(lines_new) - len(lines_old)
    # Primera línea diferente
    first_diff = None
    for i, (a, b) in enumerate(zip(lines_old, lines_new)):
        if a != b:
            first_diff = i
            break
    out = [
        f"  old: {len(old)} chars, {len(lines_old)} líneas, SHA {sha16(old)}",
        f"  new: {len(new)} chars, {len(lines_new)} líneas, SHA {sha16(new)}",
        f"  Δ líneas: {added:+d}, Δ chars: {len(new) - len(old):+d}",
    ]
    if first_diff is not None:
        out.append(f"  Primera línea diferente (línea {first_diff + 1}):")
        out.append(f"    -  {lines_old[first_diff][:140]!r}")
        out.append(f"    +  {lines_new[first_diff][:140]!r}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--bot", required=True, choices=sorted(BOTS.keys()),
                        help="Bot objetivo (botox | lhr)")
    parser.add_argument("--from", dest="source_file", default=None,
                        help="Archivo fuente del prompt (default: el mirror del bot)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Muestra diff sin publicar")
    args = parser.parse_args()

    api_key = os.environ.get("VAPI_API_KEY", "")
    if not api_key:
        print("ERROR: VAPI_API_KEY no configurado (source /etc/elena-voice/env)", file=sys.stderr)
        return 2

    info = BOTS[args.bot]
    source = Path(args.source_file) if args.source_file else info["mirror"]
    if not source.exists():
        print(f"ERROR: archivo fuente no existe: {source}", file=sys.stderr)
        return 2

    new_prompt = strip_header(source.read_text(encoding="utf-8"))
    if not new_prompt.strip():
        print(f"ERROR: prompt vacío tras strip_header en {source}", file=sys.stderr)
        return 2

    # Fetch current assistant (necesitamos tools, analysisPlan, etc.)
    try:
        assistant = get_assistant(api_key, info["assistant_id"])
    except requests.RequestException as e:
        print(f"ERROR: no pude traer assistant de Vapi: {e}", file=sys.stderr)
        return 2

    model_cfg = assistant.get("model", {}) or {}
    current_prompt = (model_cfg.get("messages") or [{}])[0].get("content", "")
    current_tools = model_cfg.get("tools", [])
    current_analysis_plan = assistant.get("analysisPlan")
    first_message = assistant.get("firstMessage", "")

    # AUDIT: advertir si alguien agregó toolIds manualmente (OAuth GHL está roto)
    tool_ids = model_cfg.get("toolIds") or []
    if tool_ids:
        print(f"⚠️  WARNING: {len(tool_ids)} toolIds encontrados. Los vamos a vaciar.")
        print("    GHL-native toolIds están intencionalmente desactivados desde v17.46.")
        print("    Si alguien los restauró, revisar historia antes de re-aplicar.")

    print(f"─── Publicación de prompt: {info['label']} ({info['assistant_id'][:8]}...) ───")
    print(f"  Fuente: {source.relative_to(REPO_ROOT)}")
    print(f"  Tools preservados: {len(current_tools)}")
    print(f"  analysisPlan: {'sí' if current_analysis_plan else 'no'}")
    print("")
    print(summarize_diff(current_prompt, new_prompt))

    if current_prompt == new_prompt:
        print("\n✓ Nada que publicar — el prompt del repo ya coincide con Vapi live.")
        return 0

    if args.dry_run:
        print("\n--dry-run: no se publica. Repetir sin --dry-run para aplicar.")
        return 0

    # PATCH a Vapi — mandamos solo model (preserva analysisPlan implícitamente si
    # no lo tocamos; aun así lo incluimos explícitamente por seguridad)
    new_messages = list(model_cfg.get("messages") or [{}])
    if not new_messages:
        new_messages = [{"role": "system", "content": new_prompt}]
    else:
        new_messages[0] = {**new_messages[0], "role": new_messages[0].get("role", "system"),
                           "content": new_prompt}
    patch_body = {
        "model": {
            **model_cfg,
            "messages": new_messages,
            "toolIds": [],  # fuerza vacío por la razón del warning arriba
            "tools": current_tools,
        }
    }
    if current_analysis_plan is not None:
        patch_body["analysisPlan"] = current_analysis_plan

    r = requests.patch(
        f"{VAPI_API}/assistant/{info['assistant_id']}",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=patch_body, timeout=30,
    )
    if r.status_code >= 300:
        print(f"ERROR PATCH {r.status_code}: {r.text[:400]}", file=sys.stderr)
        return 2

    # Verificar
    a2 = get_assistant(api_key, info["assistant_id"])
    live_after = (a2.get("model", {}).get("messages") or [{}])[0].get("content", "")
    tools_after = len(a2.get("model", {}).get("tools", []))
    if live_after != new_prompt:
        print("ERROR: PATCH devolvió 200 pero el prompt live no coincide con lo enviado", file=sys.stderr)
        return 2
    print(f"\n✓ PATCH aplicado. Prompt live SHA {sha16(live_after)}, {len(live_after)} chars")
    print(f"  Tools live: {tools_after}")
    if tools_after != len(current_tools):
        print(f"  ⚠️  Cantidad de tools cambió ({len(current_tools)} → {tools_after}) — revisar")

    # Regenerar header del mirror
    fm = a2.get("firstMessage", first_message)
    header = build_header(args.bot, info, live_after, fm)
    info["mirror"].write_text(header + live_after + "\n", encoding="utf-8")
    print(f"  Mirror regenerado: {info['mirror'].relative_to(REPO_ROOT)}")

    print("\nPróximos pasos sugeridos:")
    print(f"  1. Test con una llamada al número del bot ({info['label']})")
    print(f"  2. git add {info['mirror'].name} && git commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
