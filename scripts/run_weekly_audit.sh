#!/bin/bash
# Wrapper para cron: corre la auditoría semanal cargando env seguro desde
# /etc/elena-voice/env (permisos 600, credenciales mirror de Render).
#
# Instalación crontab (root):
#   CRON_TZ=America/New_York
#   0 8 * * 1 /root/agents/elena-voice/scripts/run_weekly_audit.sh
#
# Logs: /root/.claude/logs/elena-voice-audit.log (rotar manualmente si crece)

set -euo pipefail

REPO=/root/agents/elena-voice
ENV_FILE=/etc/elena-voice/env
LOG=/root/.claude/logs/elena-voice-audit.log
PY=/tmp/elena_venv/bin/python

mkdir -p "$(dirname "$LOG")"

{
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Weekly audit run"
  echo "═══════════════════════════════════════════════════════════"

  if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE no existe — no puedo cargar credenciales"
    exit 1
  fi

  # shellcheck disable=SC1090
  source "$ENV_FILE"

  cd "$REPO"

  # Drift check antes del audit — si los mirrors del repo divergen del
  # prompt live en Vapi, alerta pero no bloquea el audit.
  echo ""
  echo "--- Prompt drift check ---"
  if ! "$PY" scripts/check_prompt_drift.py; then
    echo "⚠️  Drift detectado. El audit sigue pero conviene revisar."
  fi

  "$PY" scripts/audit_continuous.py
} >> "$LOG" 2>&1
