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
