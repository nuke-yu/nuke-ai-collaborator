#!/usr/bin/env bash
# Validate the per-group execution sandbox end-to-end, independent of the app.
#
# Builds the image, starts a sandbox that bind-mounts ONLY a fake group_1, then
# proves: (a) commands run inside the container, (b) a sibling group's data is
# INVISIBLE (ENOENT) — i.e. group isolation holds at the mount layer — and
# (c) the toolchain (rg/git/node) is present.
#
# Run this on a host with a working Docker daemon:
#   bash deploy/sandbox/validate.sh
set -euo pipefail

IMAGE="${NUKE_SANDBOX_IMAGE:-nuke-sandbox:latest}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> 0. docker daemon reachable?"
docker info >/dev/null 2>&1 || { echo "[FAIL] docker daemon not reachable"; exit 1; }
echo "    ok"

echo "==> 1. build $IMAGE"
docker build -t "$IMAGE" "$HERE"

# fake workspace root with two groups; only group_1 will be mounted
TMP="$(mktemp -d)"
mkdir -p "$TMP/group_1/shared" "$TMP/group_2/shared"
echo "group-2-secret"     > "$TMP/group_2/shared/chat.db"
echo "hello-from-group-1" > "$TMP/group_1/shared/readme.txt"
G1="$TMP/group_1"
NAME="nuke-sbx-validate-$$"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "==> 2. start sandbox (mounts ONLY group_1, runs as host uid)"
docker run -d --rm --name "$NAME" \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$G1,dst=$G1" \
  --workdir "$G1" \
  "$IMAGE" sleep infinity >/dev/null

echo "==> 3. commands run inside + own group's files readable"
docker exec "$NAME" sh -c 'printf "    hostname=%s\n    " "$(hostname)"; cat shared/readme.txt'

echo "==> 4. sibling group_2 must be INVISIBLE"
if docker exec "$NAME" sh -c "cat '$TMP/group_2/shared/chat.db'" >/dev/null 2>&1; then
  echo "[FAIL] group_2 data was READABLE inside the sandbox — isolation BROKEN"
  exit 1
fi
echo "    [OK] group_2 path is ENOENT inside the sandbox — isolation holds"

echo "==> 5. toolchain present"
docker exec "$NAME" sh -c 'rg --version | head -1; git --version; node --version'

echo ""
echo "==> PASS: sandbox builds, executes, isolates group_1 from group_2."
