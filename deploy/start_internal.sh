#!/usr/bin/env bash
set -euo pipefail

# Run from any working directory while keeping imports relative to the repo root.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

HOST="${NOVICESYNAPSE_HOST:-0.0.0.0}"
PORT="${NOVICESYNAPSE_PORT:-8000}"

echo "Starting NoviceSynapse on ${HOST}:${PORT}"
exec python3 -m cli.main gateway --host "${HOST}" --port "${PORT}"
