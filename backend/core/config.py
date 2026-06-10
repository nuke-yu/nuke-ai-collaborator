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

# --- Embeddings (DFT-035) ---
# Pluggable embedding backend. "local" uses chromadb's bundled MiniLM model
# (offline, no API key, 384-dim); "openai"/"deepseek" call an OpenAI-compatible
# /embeddings endpoint. Switching provider/model changes the vector dimension and
# invalidates the stored index — run `python3 -m scripts.reindex_embeddings`
# after changing these.
EMBEDDING_PROVIDER = (os.environ.get("NUKE_EMBEDDING_PROVIDER") or "local").lower()
EMBEDDING_MODEL = os.environ.get("NUKE_EMBEDDING_MODEL") or None      # None → provider default
EMBEDDING_ENDPOINT = os.environ.get("NUKE_EMBEDDING_ENDPOINT") or None  # override base URL

# --- Environment overrides ---
if os.environ.get("NUKE_DEBUG"):
    DOOM_LOOP_THRESHOLD = 100
