# Single-container image: backend (FastAPI + supervisor/worker/collector tree)
# that also serves the built frontend SPA. The per-group execution sandboxes are
# SEPARATE containers this app spawns via the mounted docker socket — build that
# image separately:  docker build -t nuke-sandbox:latest deploy/sandbox
#
# Build:  docker build -t nuke-ai-collaborator:latest .
# Run:    see docker-compose.yml (needs docker.sock + a host workspace volume).

# --- stage 1: build the frontend → /app/frontend/dist ------------------------
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- stage 2: backend + the served frontend ----------------------------------
FROM python:3.11-slim
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

# System deps:
#   ripgrep    — the `search` tool (runs in the worker)
#   git        — repo operations
#   nodejs/npm — runtime for typescript-language-server (JS/TS code_intel)
#   docker.io  — docker CLI to dispatch per-group sandbox containers (talks to
#                the mounted /var/run/docker.sock; daemon itself is NOT run here)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ripgrep git nodejs npm docker.io ca-certificates \
    && npm install -g typescript-language-server typescript \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt   # includes jedi

COPY backend/ /app/backend/
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# NUKE_FRONTEND_DIST makes the backend serve the SPA (main.py); same-origin, so
# the frontend's relative API/WS calls just work.
ENV NUKE_FRONTEND_DIST=/app/frontend/dist \
    HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
