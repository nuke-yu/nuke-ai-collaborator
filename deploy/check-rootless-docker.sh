#!/bin/sh
set -eu

case "${NUKE_DOCKER_ISOLATION:-}" in
  rootless) ;;
  *) echo "NUKE_DOCKER_ISOLATION must be rootless" >&2; exit 2 ;;
esac

host=${DOCKER_HOST:-}
case "$host" in
  unix:///var/run/docker.sock|"" ) echo "refusing host root Docker socket" >&2; exit 2 ;;
esac

if ! docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
  echo "Docker daemon is not reporting rootless security options" >&2
  exit 1
fi

echo "rootless Docker daemon OK: $host"
