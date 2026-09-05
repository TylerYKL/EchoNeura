#!/usr/bin/env bash
#
# Start EchoNeura for local development: FastAPI (with an in-process queue
# worker) on :8000 and Next.js on :3000.
#
# Usage:  make dev          (or: bash scripts/dev.sh)
# Ctrl-C stops both. No Docker, no Redis, no GPU required.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

# --- prerequisites ---------------------------------------------------------
if [[ ! -x backend/.venv/bin/uvicorn ]]; then
  echo "→ creating the Python virtualenv and installing backend dependencies…"
  (cd backend && python3 -m venv .venv && ./.venv/bin/pip install -q --disable-pip-version-check -U pip)
  backend/.venv/bin/pip install -q --disable-pip-version-check -r backend/requirements.txt
fi

if [[ ! -d frontend/node_modules ]]; then
  echo "→ installing frontend dependencies…"
  (cd frontend && npm install --no-audit --no-fund)
fi

if [[ ! -f .env ]]; then
  echo "→ creating .env from .env.example (mock providers, no keys needed)"
  cp .env.example .env
fi

mkdir -p data/uploads

# --- run both, tear down cleanly ------------------------------------------
pids=()
cleanup() {
  echo
  echo "→ stopping…"
  for pid in "${pids[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "→ API      http://localhost:${BACKEND_PORT}/api/docs"
echo "→ Web UI   http://localhost:${FRONTEND_PORT}"
echo

(
  cd backend
  PYTHONPATH=. ../backend/.venv/bin/uvicorn app.main:app \
    --host 0.0.0.0 --port "$BACKEND_PORT" --reload --log-level info
) &
pids+=($!)

# Give the API a moment so the frontend's server-side health probe succeeds on
# first paint instead of showing "cannot reach the API".
sleep 2

(
  cd frontend
  BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}" PORT="$FRONTEND_PORT" npm run dev
) &
pids+=($!)

wait -n
