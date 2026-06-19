# Per-group execution sandbox image for Nuke AI Collaborator.
#
# One long-lived container per ACTIVE group runs `sleep infinity`; the worker
# `docker exec`s bot shell commands into it. Isolation is by MOUNT: only that
# group's workspace is bind-mounted in (at the same host path), so sibling
# groups, the central DB, and host secrets are simply absent from the container.
#
# The container is started with --user <host-uid>:<host-gid> so the sandboxed
# process shares the workspace files' owner uid (can read/write the group dir)
# while staying confined to the mounted dir. Memory/CPU come from `docker run`
# cgroup limits, not an in-image setting.
#
# Build:  docker build -t nuke-sandbox:latest deploy/sandbox
# Toolchain versions here are a sane default — adjust to match the projects the
# bots actually build.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Core toolchain the bots need to build/test/search inside the sandbox:
#   git           — version control (bots operate on real repos)
#   ripgrep       — the `search` tool runs inside the sandbox too
#   build-essential / curl — common build deps
#   nodejs + npm  — JS projects (also hosts typescript-language-server for Phase 2 LSP)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ripgrep \
        curl \
        ca-certificates \
        build-essential \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# No baked-in app user: the container is launched with --user <host-uid>:<host-gid>
# so file ownership on the bind-mounted workspace lines up. We only ensure a
# writable HOME for tools that need one regardless of uid.
ENV HOME=/tmp
WORKDIR /tmp

# Idle keepalive — the worker execs commands in via `docker exec`.
CMD ["sleep", "infinity"]
