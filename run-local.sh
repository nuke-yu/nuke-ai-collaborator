#!/usr/bin/env bash
# Local DEV backend — fully separate from the Docker deploy stack.
#
#   ./run-local.sh
#
# Binds 127.0.0.1 ONLY (not 0.0.0.0): the backend is reachable just from this
# machine and never overlaps the Docker container's :8000 (which binds IPv6 ::1).
# Uses the local default DB (backend/db/chat.db) — no NUKE_DB_PATH override.
#
# Frontend (separate terminal):  cd frontend && npm run dev   → http://localhost:5173
# Docker deploy stack (separate): docker compose up -d         → http://localhost:8080
#
# No --reload on purpose: the AI loop / tools / workflow run in worker SUBPROCESSES
# this app spawns; --reload only reloads the main process, leaving workers on stale
# code. After changing backend code, Ctrl-C and re-run this script.
set -euo pipefail
cd "$(dirname "$0")/backend"
exec python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
