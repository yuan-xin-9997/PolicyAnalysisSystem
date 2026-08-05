#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${ROOT_DIR}/src/logs/server.pid"
LOG_FILE="${ROOT_DIR}/src/logs/app.log"
APP_DIR="${ROOT_DIR}/src/app/backend"
CONFIG_FILE="${ROOT_DIR}/src/config/app.json"
HOST="${POLICY_ANALYSIS_SERVER__HOST:-127.0.0.1}"
PORT="${POLICY_ANALYSIS_SERVER__PORT:-}"

if [ -z "${PORT}" ] && [ -f "${CONFIG_FILE}" ]; then
  PORT="$("${PYTHON:-python3}" - <<'PY' "${CONFIG_FILE}"
import json
import sys
from pathlib import Path

try:
    print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("server", {}).get("port", 30080))
except Exception:
    print(30080)
PY
)"
fi
PORT="${PORT:-30080}"

mkdir -p "${ROOT_DIR}/src/logs" "${ROOT_DIR}/src/data"

if [ -f "${PID_FILE}" ]; then
  PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
    echo "服务已在运行，PID=${PID}"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

if [ -d "${ROOT_DIR}/.venv" ]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

cd "${ROOT_DIR}"
nohup "${PYTHON_BIN}" -m uvicorn policy_analysis.main:app \
  --app-dir "${APP_DIR}" \
  --host "${HOST}" \
  --port "${PORT}" \
  >> "${LOG_FILE}" 2>&1 &

echo "$!" > "${PID_FILE}"
echo "服务已启动，PID=$(cat "${PID_FILE}")，地址=http://${HOST}:${PORT}"
