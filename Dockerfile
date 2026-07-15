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

# Harden npm against flaky networks (ERR_SOCKET_TIMEOUT etc.): more retries with
# longer backoff and a generous socket timeout. Set a registry env override knob
# too — pass --build-arg NPM_REGISTRY=<mirror> on slow/proxied hosts.
ARG NPM_REGISTRY=https://registry.npmjs.org/
RUN npm config set registry "$NPM_REGISTRY" \
    && npm config set fetch-retries 5 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm config set fetch-retry-maxtimeout 120000 \
    && npm config set fetch-timeout 600000

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

# Install the JS/TS language server here, into a self-contained prefix, so the
# backend stage can reuse node + this without pulling Debian's npm package
# (which drags in webpack + hundreds of node-* deps and dominates the build).
RUN --mount=type=cache,target=/root/.npm \
    npm install -g --prefix /opt/node-tools typescript-language-server typescript

# --- stage 2: backend + the served frontend ----------------------------------
# Must match the dev Python (3.13): the codebase uses PEP 701 f-strings
# (backslashes inside f-string expressions) which are a SyntaxError on <3.12.
FROM python:3.13-slim
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
        ripgrep git gh libstdc++6 ca-certificates

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

# Seed the default group on first boot (idempotent: skips if any group exists),
# then hand off to uvicorn as PID 1. Seed is best-effort so a seed hiccup can't
# brick startup. Without this a fresh DB has no group and login 500s on the
# hardcoded POST /api/groups/1/members.
CMD ["sh", "-c", "python -m seed || echo '[entrypoint] seed skipped/failed'; exec python -m uvicorn main:app --host 0.0.0.0 --port 8000"]
