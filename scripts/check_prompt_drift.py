#!/usr/bin/env python3
"""
scripts/check_prompt_drift.py
═══════════════════════════════════════════════════════════════════════════
Compara los mirrors `system_prompt*.txt` del repo con lo que está live en
Vapi para cada assistant configurado. Alerta si divergen (drift).

¿Por qué existe este script?
El 2026-04-14 descubrimos que `system_prompt.txt` del repo decía una cosa
(firstMessage largo) y Vapi live decía otra (firstMessage corto aplicado
días antes). El drift silencioso hace que futuras ediciones partan de una
versión obsoleta del prompt y re-introduzcan regresiones ya corregidas.

Este script corre como parte del `run_weekly_audit.sh` del lunes, y también
se puede ejecutar manual para verificar antes de cambios.

Uso:
    python3 scripts/check_prompt_drift.py              # normal, exit 0 si OK
    python3 scripts/check_prompt_drift.py --fix        # pull del live a mirror
    python3 scripts/check_prompt_drift.py --json       # output JSON

Exit codes:
    0  mirrors coinciden con live
    1  drift detectado
    2  error (no se pudo alcanzar Vapi, creds faltantes, etc.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
VAPI_API = "https://api.vapi.ai/assistant"

# Mapeo de assistant_id → archivo mirror en el repo.
# Si se agrega un tratamiento, agregar su entry aquí también.
MIRRORS = {
    "1631c7cf-2914-45f9-bf82-6635cdf00aba": {
        "label": "Botox",
        "path": REPO_ROOT / "system_prompt.txt",
    },
    "3d5b77b5-f36c-4b95-88bc-4d6484277380": {
        "label": "LHR",
        "path": REPO_ROOT / "system_prompt_lhr.txt",
    },
    "77392648-047e-4a40-9f8a-4f125f2ed6d6": {
        "label": "Acné",
        "path": REPO_ROOT / "system_prompt_acne.txt",
    },
    "b6b09524-06da-4bf7-b518-a71b6a1c7d8b": {
        "label": "Cicatrices",
        "path": REPO_ROOT / "system_prompt_cicatrices.txt",
    },
    "a9494200-af37-485c-b0fb-fb85479b17a7": {
        "label": "Fillers",
        "path": REPO_ROOT / "system_prompt_fillers.txt",
    },
    "39bd6450-055e-4839-9c27-6522e08e8423": {
        "label": "Bioestimuladores",
        "path": REPO_ROOT / "system_prompt_radiesse.txt",
    },
    "65b3a4b0-2e08-471f-af56-e091e47f26bd": {
        "label": "Rejuvenecimiento",
        "path": REPO_ROOT / "system_prompt_rejuvenecimiento.txt",
    },
}


def strip_header(text: str) -> str:
    """
    Los mirrors empiezan con un header de comentarios `# ...` que NO es
    parte del prompt live — documenta fuente, fecha del pull, SHA, etc.
    Quitamos ese bloque (primeras líneas que empiezan con `# ` hasta la
    primera línea en blanco o no-comentario) antes de comparar.
    """
    lines = text.splitlines(keepends=True)
    i = 0
    # Avanza mientras las líneas empiecen con '#' o estén vacías
    while i < len(lines):
        s = lines[i].lstrip()
        if s.startswith("#") or s.strip() == "":
            i += 1
            continue
        break
    return "".join(lines[i:])


def sha16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def fetch_live_prompt(assistant_id: str, api_key: str) -> str:
    r = requests.get(f"{VAPI_API}/{assistant_id}",
                     headers={"Authorization": f"Bearer {api_key}"},
                     timeout=30)
    r.raise_for_status()
    msgs = (r.json().get("model") or {}).get("messages") or [{}]
    return msgs[0].get("content", "")


def check_one(assistant_id: str, info: dict, api_key: str) -> dict:
    result = {"assistant_id": assistant_id, "label": info["label"],
              "path": str(info["path"].relative_to(REPO_ROOT)),
              "status": "unknown", "details": ""}

    if not info["path"].exists():
        result["status"] = "missing_mirror"
        result["details"] = f"Mirror file {info['path']} does not exist"
        return result

    try:
        live = fetch_live_prompt(assistant_id, api_key)
    except requests.RequestException as e:
        result["status"] = "fetch_error"
        result["details"] = str(e)[:200]
        return result

    mirror = strip_header(info["path"].read_text(encoding="utf-8"))
    # Normalizar whitespace de bordes — el mirror suele acabar con \n extra
    # por el escritor, mientras que Vapi guarda el prompt sin trailing \n.
    # La diferencia no es semántica, no debe disparar falsa alarma.
    live = live.strip("\n")
    mirror = mirror.strip("\n")

    live_sha = sha16(live)
    mirror_sha = sha16(mirror)
    result["live_sha"] = live_sha
    result["mirror_sha"] = mirror_sha
    result["live_len"] = len(live)
    result["mirror_len"] = len(mirror)

    if live_sha == mirror_sha:
        result["status"] = "ok"
        return result

    # Drift: dar un hint sobre qué parte difiere
    if abs(len(live) - len(mirror)) > 2000:
        hint = f"Difieren en tamaño: mirror {len(mirror)}, live {len(live)}"
    else:
        # Encontrar la primera línea diferente
        live_lines = live.splitlines()
        mirror_lines = mirror.splitlines()
        diff_at = None
        for i, (lv, mr) in enumerate(zip(live_lines, mirror_lines)):
            if lv != mr:
                diff_at = (i, lv, mr)
                break
        if diff_at:
            i, lv, mr = diff_at
            hint = f"Difieren desde la línea {i + 1}:\n  mirror: {mr[:120]!r}\n  live:   {lv[:120]!r}"
        else:
            hint = "Difieren pero el prefix común es idéntico; uno es más largo"
    result["status"] = "drift"
    result["details"] = hint
    return result


def update_mirror(assistant_id: str, info: dict, api_key: str) -> None:
    """--fix: sobrescribe el mirror del repo con el contenido live actual."""
    live = fetch_live_prompt(assistant_id, api_key)
    a = requests.get(f"{VAPI_API}/{assistant_id}",
                     headers={"Authorization": f"Bearer {api_key}"}, timeout=30).json()
    fm = a.get("firstMessage", "")
    sha = sha16(live)
    from datetime import date
    header = (
        f"# {info['path'].name} — Elena Voice {info['label']} (LIVE mirror)\n"
        f"# ════════════════════════════════════════════════════════════\n"
        f"# FUENTE DE VERDAD: este archivo es un MIRROR del prompt live\n"
        f"# del assistant de {info['label']} en Vapi. Cualquier edición debe\n"
        f"# aplicarse vía API de Vapi (scripts/update_prompt.py), no editando\n"
        f"# este archivo directamente. Sincronizado vía check_prompt_drift.py --fix.\n"
        f"#\n"
        f"# Pulled: {date.today().isoformat()}\n"
        f"# Assistant ID: {assistant_id} ({info['label']})\n"
        f"# Content SHA (prompt body): {sha}\n"
        f"# First message: \"{fm}\"\n"
        f"# ════════════════════════════════════════════════════════════\n\n"
    )
    info["path"].write_text(header + live, encoding="utf-8")
    print(f"  ✓ {info['label']}: mirror actualizado ({len(live)} chars, SHA {sha})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true",
                        help="Sobrescribe los mirrors con el contenido live (pull)")
    parser.add_argument("--json", action="store_true", help="Salida en JSON")
    args = parser.parse_args()

    api_key = os.environ.get("VAPI_API_KEY", "")
    if not api_key or api_key == "test":
        # En CI (env de test) no hay creds reales — skip con exit 0 para no bloquear
        msg = "VAPI_API_KEY no configurado (env de CI/test) — skipping drift check"
        if args.json:
            print(json.dumps({"status": "skipped", "reason": msg}))
        else:
            print(msg)
        return 0

    results = []
    for aid, info in MIRRORS.items():
        if args.fix:
            try:
                update_mirror(aid, info, api_key)
                results.append({"assistant_id": aid, "label": info["label"], "status": "fixed"})
            except Exception as e:
                results.append({"assistant_id": aid, "label": info["label"],
                                "status": "error", "details": str(e)[:200]})
        else:
            results.append(check_one(aid, info, api_key))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("─── Prompt drift check ───")
        for r in results:
            icon = {"ok": "✅", "drift": "⚠️ ", "fetch_error": "❌",
                    "missing_mirror": "❌", "fixed": "🔧", "error": "❌"}.get(r["status"], "? ")
            print(f"{icon} {r['label']:10} [{r['status']}]")
            if r.get("details"):
                for line in r["details"].splitlines():
                    print(f"     {line}")
            if r["status"] == "ok":
                print(f"     SHA {r['mirror_sha']}, {r['mirror_len']} chars")

    # Exit code: 1 si hay drift real, 2 si hay error, 0 si todo OK
    if any(r.get("status") in ("fetch_error", "missing_mirror", "error") for r in results):
        return 2
    if any(r.get("status") == "drift" for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
