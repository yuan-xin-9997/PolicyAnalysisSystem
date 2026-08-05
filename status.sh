#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${ROOT_DIR}/src/logs/server.pid"
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

if [ ! -f "${PID_FILE}" ]; then
  echo "服务未运行：PID 文件不存在"
  exit 1
fi

PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
if [ -z "${PID}" ] || ! kill -0 "${PID}" 2>/dev/null; then
  rm -f "${PID_FILE}"
  echo "服务未运行：PID 无效"
  exit 1
fi

if command -v curl >/dev/null 2>&1 && curl -fsS "http://${HOST}:${PORT}/health/ready" >/dev/null; then
  echo "服务运行中，PID=${PID}，健康检查通过"
  exit 0
fi

echo "服务进程存在，PID=${PID}，但健康检查未通过"
exit 2
