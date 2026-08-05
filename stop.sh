#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${ROOT_DIR}/src/logs/server.pid"
TIMEOUT="${POLICY_ANALYSIS_STOP_TIMEOUT_SECONDS:-20}"

if [ ! -f "${PID_FILE}" ]; then
  echo "服务未运行：PID 文件不存在"
  exit 0
fi

PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
if [ -z "${PID}" ] || ! kill -0 "${PID}" 2>/dev/null; then
  rm -f "${PID_FILE}"
  echo "服务未运行：已清理失效 PID 文件"
  exit 0
fi

kill "${PID}" 2>/dev/null || true
for _ in $(seq 1 "${TIMEOUT}"); do
  if kill -0 "${PID}" 2>/dev/null; then
    sleep 1
  else
    rm -f "${PID_FILE}"
    echo "服务已停止"
    exit 0
  fi
done

kill -9 "${PID}" 2>/dev/null || true
rm -f "${PID_FILE}"
echo "服务已强制停止"
