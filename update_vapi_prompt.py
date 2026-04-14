#!/usr/bin/env python3
"""
update_vapi_prompt.py — DEPRECATED

Este script tenía hardcoded el assistant_id de Botox y la VAPI_KEY. Fue
reemplazado el 2026-04-14 por scripts/update_prompt.py que:
  - Soporta --bot botox|lhr (y cualquier otro que se agregue en el futuro)
  - Lee VAPI_API_KEY del entorno (no más secrets commiteados)
  - Preserva model.tools y analysisPlan igual que antes
  - Regenera el mirror del repo con header actualizado tras publicar
  - Tiene --dry-run para ver el diff sin aplicar

Uso del nuevo script:
    source /etc/elena-voice/env
    python3 scripts/update_prompt.py --bot botox --dry-run
    python3 scripts/update_prompt.py --bot botox
"""
import sys

print(__doc__, file=sys.stderr)
sys.exit(2)
