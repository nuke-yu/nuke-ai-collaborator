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

# Install the JS/TS language server here, into a self-contained prefix, so the
# backend stage can reuse node + this without pulling Debian's npm package
# (which drags in webpack + hundreds of node-* deps and dominates the build).
RUN npm install -g --prefix /opt/node-tools typescript-language-server typescript

# --- stage 2: backend + the served frontend ----------------------------------
FROM python:3.11-slim
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

# System deps (kept minimal — node + the docker CLI are COPYed from images below
# instead of apt-installed, which is what used to make this layer take ~6min):
#   ripgrep      — the `search` tool (runs in the worker)
#   git          — repo operations
#   libstdc++6   — required by the copied node binary
#   ca-certificates — TLS roots
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ripgrep git libstdc++6 ca-certificates

# node runtime + the JS/TS language server, reused from the frontend stage.
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /opt/node-tools /opt/node-tools
ENV PATH="/opt/node-tools/bin:${PATH}"

# docker CLI only (no daemon) — to dispatch per-group sandbox containers via the
# mounted /var/run/docker.sock.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app/backend
# Install from the fully-pinned lock (reproducible + no index resolution at build
# time). requirements.txt stays the human-edited source; regen lock per its header.
COPY backend/requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps -r requirements.lock   # jedi, chromadb, etc.

COPY backend/ /app/backend/
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# NUKE_FRONTEND_DIST makes the backend serve the SPA (main.py); same-origin, so
# the frontend's relative API/WS calls just work.
ENV NUKE_FRONTEND_DIST=/app/frontend/dist \
    HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
