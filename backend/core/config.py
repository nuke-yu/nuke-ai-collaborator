"""Centralized system configuration and magic numbers (DFT-084)."""
import os

# --- WebSocket & IPC ---
WS_SEND_TIMEOUT = 10.0
SUPERVISOR_SEND_TIMEOUT = 5.0
IPC_MAX_FRAME_SIZE = 64 * 1024 * 1024  # 64 MiB

# Per-call wall clock for a single MCP tool execution in the collector. A hung or
# slow MCP server otherwise holds a collector concurrency slot indefinitely;
# bounding each call stops one bad server from starving the shared pool.
MCP_CALL_TIMEOUT_SECONDS = float(os.environ.get("NUKE_MCP_CALL_TIMEOUT_SECONDS") or 120)

# --- R&D & Automation ---
ASK_TIMEOUT_SECONDS = 300
SPAWN_MAX_DEPTH = 3
DOOM_LOOP_THRESHOLD = 3
SUMMARY_THRESHOLD = 15

# --- AI & Resource Limits ---
AI_RETRY_MAX = 3
TOOL_RESULT_MAX_CHARS = 20_000
SHELL_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024  # 512 MB

# run_shell execution backend (group isolation is enforced by the backend):
#   "local"     — host subprocess, NO cross-group isolation (dev / Docker not ready)
#   "container" — per-group sandbox container; unhealthy backend fails closed
#   "auto"      — container if healthy, else fall back to local (best-effort, dev)
# Production should set NUKE_SHELL_EXEC_BACKEND=container so isolation is mandatory.
SHELL_EXEC_BACKEND = (os.environ.get("NUKE_SHELL_EXEC_BACKEND") or "local").lower()


def validate_runtime_security() -> None:
    """Reject production configurations that permit host-local shell execution."""
    if os.environ.get("NUKE_ENV", "").lower() != "production":
        return
    if SHELL_EXEC_BACKEND != "container":
        raise RuntimeError(
            "NUKE_ENV=production requires NUKE_SHELL_EXEC_BACKEND=container; "
            f"got {SHELL_EXEC_BACKEND!r}"
        )

# Per-group execution sandbox (container backend). One long-lived container per
# active group; only that group's workspace is bind-mounted in → group isolation
# is a mount fact. See deploy/sandbox/Dockerfile.
SANDBOX_IMAGE = os.environ.get("NUKE_SANDBOX_IMAGE") or "nuke-sandbox:latest"
SANDBOX_MEMORY = os.environ.get("NUKE_SANDBOX_MEMORY") or "512m"
SANDBOX_CPUS = os.environ.get("NUKE_SANDBOX_CPUS") or "2"
SANDBOX_NETWORK = os.environ.get("NUKE_SANDBOX_NETWORK") or "bridge"  # "none" to cut egress
SANDBOX_IDLE_TIMEOUT_S = int(os.environ.get("NUKE_SANDBOX_IDLE_TIMEOUT_S") or 1800)

# JS/TS code intelligence (code_intel) — typescript-language-server over LSP.
# Static analysis (does not execute project code), so it runs in the worker like
# jedi. Absent binary → engine reports unavailable → tool falls back to `search`.
TS_LANGUAGE_SERVER = os.environ.get("NUKE_TS_LANGUAGE_SERVER") or "typescript-language-server"
# Keep a warm LSP server per project root; evict after this much idle.
LSP_IDLE_TIMEOUT_S = int(os.environ.get("NUKE_LSP_IDLE_TIMEOUT_S") or 600)

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

# --- Memory Retrieval (RAG ranking) ---
# Absolute cosine-similarity floor for injecting a memory into context: candidates
# scoring below this are dropped BEFORE ranking, to curb retrieval-hallucination
# (a semantically-far memory being fed to the LLM as if relevant). Tune per
# embedding model — too high → "amnesia" (nothing retrieved), too low → noise.
# Watch the `returned=N` count in the "Memory RAG Retrieval" log under real
# traffic. Default suits a higher-similarity model; the bundled local MiniLM may
# need a lower value.
MEMORY_SIMILARITY_FLOOR = float(os.environ.get("NUKE_MEMORY_SIMILARITY_FLOOR") or 0.65)

# --- Memory Consolidation / Reflection (P1, 巩固层) ---
# 反思把零散事实周期性提炼成高层语义洞察。重要性累积触发：自上次反思以来新事实的
# salience 之和 ≥ REFLECT_IMPORTANCE_THRESHOLD 且条数 ≥ REFLECT_MIN_FACTS 才触发，
# 单次最多产出 REFLECT_MAX_INSIGHTS 条洞察。仿 Generative Agents（其阈值 150 基于
# 1-10 评分；本系统 salience 为 0-1，故阈值相应缩小）。
REFLECT_IMPORTANCE_THRESHOLD = float(os.environ.get("NUKE_REFLECT_IMPORTANCE_THRESHOLD") or 3.0)
REFLECT_MIN_FACTS = int(os.environ.get("NUKE_REFLECT_MIN_FACTS") or 5)
REFLECT_MAX_INSIGHTS = int(os.environ.get("NUKE_REFLECT_MAX_INSIGHTS") or 5)
# 积压上限：自上次反思以来未触发的事实超过此数，强制推进水位线丢弃这批（importance 极低、
# 本就该遗忘），避免重要性始终不达阈值时水位线永不推进、每条消息 fetch 无界增长。
REFLECT_MAX_BACKLOG = int(os.environ.get("NUKE_REFLECT_MAX_BACKLOG") or 50)

# 冲突消解：仅当新事实与旧事实的向量距离小于此值（cosine 空间下 0.25 ≈ 相似度 >0.75）
# 才纳入排他性冲突审查。换 embedding 模型或度量空间（如 L2）时需相应调整，故可配。
MEMORY_CONFLICT_MAX_DISTANCE = float(os.environ.get("NUKE_MEMORY_CONFLICT_MAX_DISTANCE") or 0.25)

# 遗忘 TTL（天）：原子事实较短，反思洞察作为沉淀的语义知识保留更久 (P2)。
MEMORY_TTL_DAYS = float(os.environ.get("NUKE_MEMORY_TTL_DAYS") or 180.0)
REFLECT_TTL_DAYS = float(os.environ.get("NUKE_REFLECT_TTL_DAYS") or 540.0)
# 检索时给反思洞察的加性 bonus，使沉淀的高层知识更易浮现 (P2)。
REFLECT_RETRIEVAL_BONUS = float(os.environ.get("NUKE_REFLECT_RETRIEVAL_BONUS") or 0.1)

# L4 工具事件压缩：某 bot 在某群累计未压缩的 tool_events（L1 事件日志）达到
# TOOL_EVENT_COMPRESS_THRESHOLD 条时，turn 后用一次 call_ai 把这批（最多 MAX_BATCH 条）
# 总结成持久记忆写入 Chroma（供 recall / session-init 语义注入），并标记 compressed=1。
# 仿 maybe_reflect，但触发改为纯条数门控、成本上限 1 次模型调用/触发。
TOOL_EVENT_COMPRESS_THRESHOLD = int(os.environ.get("NUKE_TOOL_EVENT_COMPRESS_THRESHOLD") or 20)
TOOL_EVENT_COMPRESS_MAX_BATCH = int(os.environ.get("NUKE_TOOL_EVENT_COMPRESS_MAX_BATCH") or 40)
TOOL_EVENT_COMPRESS_MAX_INSIGHTS = int(os.environ.get("NUKE_TOOL_EVENT_COMPRESS_MAX_INSIGHTS") or 3)
# 已压缩原始事件行的保留天数：压缩后这些行只是审计冗余，超期低概率后台清理防表无限增长。
TOOL_EVENT_RETENTION_DAYS = float(os.environ.get("NUKE_TOOL_EVENT_RETENTION_DAYS") or 30.0)
# 检索时给「与当前讨论 topic 同 thread」的记忆的加性 bonus：让本话题记忆上浮，
# 但**不硬过滤**跨话题记忆（软作用域）——避免按话题孤岛化、保住跨话题的长期知识召回。
MEMORY_THREAD_AFFINITY_BONUS = float(os.environ.get("NUKE_MEMORY_THREAD_AFFINITY_BONUS") or 0.15)

# 多层反思 (P3)：默认关闭（单层，只反思原子事实，防误差放大）。开启后允许对既有反思
# 再归纳，形成反思树；REFLECT_MAX_LEVEL 封顶层数，到顶的反思不再被纳入下一层归纳。
REFLECT_MULTILEVEL = (os.environ.get("NUKE_REFLECT_MULTILEVEL") or "0") not in ("0", "", "false", "False")
REFLECT_MAX_LEVEL = int(os.environ.get("NUKE_REFLECT_MAX_LEVEL") or 2)

# --- Environment overrides ---
if os.environ.get("NUKE_DEBUG"):
    DOOM_LOOP_THRESHOLD = 100
