#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

HOST="${SCHOLARSPROUT_HOST:-0.0.0.0}"
PORT="${SCHOLARSPROUT_PORT:-8000}"
PYTHON_BIN="${SCHOLARSPROUT_PYTHON:-python3}"

echo "Starting ScholarSprout for campus-network access on ${HOST}:${PORT}"
echo "Python: ${PYTHON_BIN}"
echo "Access URL: http://<server-campus-ip>:${PORT}/"

exec "${PYTHON_BIN}" -m cli.main gateway --host "${HOST}" --port "${PORT}"
