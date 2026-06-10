"""Centralized system configuration and magic numbers (DFT-084)."""
import os

# --- WebSocket & IPC ---
WS_SEND_TIMEOUT = 10.0
SUPERVISOR_SEND_TIMEOUT = 5.0
IPC_MAX_FRAME_SIZE = 64 * 1024 * 1024  # 64 MiB

# --- R&D & Automation ---
ASK_TIMEOUT_SECONDS = 300
SPAWN_MAX_DEPTH = 3
DOOM_LOOP_THRESHOLD = 3
SUMMARY_THRESHOLD = 15

# --- AI & Resource Limits ---
AI_RETRY_MAX = 3
TOOL_RESULT_MAX_CHARS = 20_000
SHELL_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024  # 512 MB

# --- Context Management (AutoCompact) ---
AUTOCOMPACT_BUFFER_TOKENS = 13_000
PRE_RUN_TOKEN_THRESHOLD = 20_000
DB_COMPACTION_TOKEN_THRESHOLD = 30_000

# --- Observability (DFT-032) ---
# /metrics is enabled by default for internal Prometheus scraping. Set
# NUKE_METRICS_TOKEN to require `Authorization: Bearer <token>` (use when the
# endpoint is reachable beyond a trusted network); leave unset for internal-only.
METRICS_ENABLED = os.environ.get("NUKE_METRICS_ENABLED", "1") != "0"
METRICS_TOKEN = os.environ.get("NUKE_METRICS_TOKEN") or None

# --- Environment overrides ---
if os.environ.get("NUKE_DEBUG"):
    DOOM_LOOP_THRESHOLD = 100
