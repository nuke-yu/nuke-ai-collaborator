from core import config
import asyncio
import logging
import math
import time
import re
from functools import partial
import chromadb
from db import get_db
from ai import embeddings

log = logging.getLogger(__name__)

# Constants
SUMMARY_THRESHOLD = config.SUMMARY_THRESHOLD

# ── 1. 事实过滤与冲突检测提示词 ───────────────────────────────────────
FACT_EXTRACTION_PROMPT = """你是一个记忆过滤与事实提取助手。你的任务是从以下智能体的发言中，提取出值得长期记住的关键技术决策、用户偏好、重要配置修改或项目结论。
如果是无意义的客套话（如'好的'、'没问题'）、中间调试过程、过渡性废话或临时性的工具报错，请直接返回'NO_SALIENT_INFO'。
否则，请以极其简练的中文陈述句提取出核心事实，每条一行（不要带序号、前缀或标点符号），例如：
将服务端口修改为8080
用户偏好使用Python进行脚本编写
项目已切换到React 19版本

发言内容：
"""

BATCH_CONFLICT_PROMPT = """你是一个事实冲突检测助手。
下面是新提取的若干事实（New Facts）：
{new_facts_text}

下面是从既有记忆库中检索出来的可能存在冲突的已有事实（Existing Memories）：
{existing_memories_text}

请逐一比对新事实与已有事实。如果发现某个新事实与某个已有事实描述了相同的属性、配置、路径、偏好或状态，但新事实更新或覆盖了旧事实的值（即存在排他性冲突，新事实使旧事实失效），请将发生冲突的已有事实 ID 记录下来。

请直接返回一个 JSON 数组，包含所有存在冲突且被覆盖的已有事实 ID，格式如下：
["id1", "id2", ...]
如果没有检测到任何冲突，请直接返回：[]
请勿输出任何其他解释文字。"""


# ── Chroma 初始化（与测试 Mock 兼容，首次使用时自动加载 ~80MB embedding 模型）──
_chroma_client = None
_chroma_collection = None

def _get_collection():
    global _chroma_client, _chroma_collection
    if _chroma_collection is None:
        from ai import embeddings  # DFT-035: config-driven embedding backend
        _chroma_client = chromadb.PersistentClient(path="./chroma_db")
        sig = embeddings.embedding_signature()
        col = _chroma_client.get_or_create_collection(
            name="messages",
            embedding_function=embeddings.get_embedding_function(),
            metadata={"hnsw:space": "cosine", "emb_sig": sig},
        )
        embeddings.verify_signature((col.metadata or {}).get("emb_sig"), sig)
        _chroma_collection = col
    return _chroma_collection


# ── 2. ChromaStore ── 向量数据库连接管理
class ChromaStore:
    """负责与 ChromaDB 进行所有底层的物理读写与删除交互。"""

    @classmethod
    def get_collection(cls):
        # 委托给模块级 _get_collection 函数，以兼容已有的单元测试 Mock 机制
        return _get_collection()

    @classmethod
    def write_fact_sync(cls, f_id: str, f_content: str, metadata: dict, del_id: str | None = None):
        col = cls.get_collection()
        if del_id:
            try:
                col.delete(ids=[del_id])
            except Exception:
                log.warning("ChromaStore: failed to delete conflicting ID %s", del_id)
                
        # 隐私与治理：敏感信息脱敏 (PII & Secret Redaction)
        try:
            from executors.redaction import redact_secrets
            f_content, _ = redact_secrets(f_content)
        except Exception:
            log.exception("ChromaStore: secret redaction failed")
            
        col.upsert(
            ids=[f_id],
            documents=[f_content],
            metadatas=[metadata]
        )

    @classmethod
    def query_similar_sync(cls, query: str, where: dict, limit: int = 1) -> dict:
        col = cls.get_collection()
        return col.query(
            query_texts=[query],
            n_results=limit,
            where=where,
            include=["documents", "distances", "metadatas"]  # 排除 "ids"，ChromaDB 默认包含 "ids"
        )

    @classmethod
    def delete_sync(cls, where: dict):
        col = cls.get_collection()
        col.delete(where=where)

    @classmethod
    def prune_expired_memories_sync(cls, max_age_seconds: float = 180 * 86400):
        """物理清除过期的旧记忆 (遗忘机制：TTL)"""
        col = cls.get_collection()
        t_threshold = time.time() - max_age_seconds
        try:
            col.delete(where={"timestamp": {"$lt": t_threshold}})
            log.info("ChromaStore: pruned memories older than %d seconds", max_age_seconds)
        except Exception:
            log.exception("ChromaStore: failed to prune expired memories")


# ── 3. FactExtractor ── 关键事实与噪音快筛
class FactExtractor:
    """提取发言中的高价值事实，过滤 conversational noise (如“好的”)。"""
    
    @staticmethod
    async def extract(content: str, provider: str = "deepseek", model: str = "deepseek-chat") -> list[str]:
        if not content or not content.strip() or len(content.strip()) < 15:
            return []

        # #1: 用群组实际配置的 provider/model（而非写死 DeepSeek），低温保证抽取确定性。
        from ai.client import call_ai_once
        try:
            res = await call_ai_once(
                FACT_EXTRACTION_PROMPT,
                [{"role": "user", "content": content}],
                provider, model, temperature=0.2, max_tokens=512,
            )
            text = (res.get("content") if isinstance(res, dict) and res.get("type") == "text" else "") or ""
            text = text.strip()
            if not text or "NO_SALIENT_INFO" in text:
                return []
            return [
                line.strip()
                for line in text.split("\n")
                if line.strip() and not line.strip().startswith("#")
            ]
        except Exception:
            log.exception("FactExtractor: failed to extract facts")
            return []


# ── 4. ConflictResolver ── 批量语义冲突消解 (优化 LLM 调用扇出)
class ConflictResolver:
    """批量比对新事实与相似的历史事实，检测排他性冲突并返回被覆盖/应删除的旧记忆 ID 列表。"""

    @staticmethod
    async def resolve_batch(facts: list[str], bot_id: int, group_id: int | None,
                            provider: str = "deepseek", model: str = "deepseek-chat") -> list[str]:
        if not facts:
            return []

        loop = asyncio.get_running_loop()
        from ai.client import call_ai_once

        # 1. 召回每个新事实的 Top-3 相似历史记录作为候选（解决只看最近邻 1 条的漏检问题）
        candidates = {}  # id -> document
        try:
            if group_id is not None:
                where = {"$and": [{"bot_id": {"$eq": bot_id}}, {"group_id": {"$eq": group_id}}]}
            else:
                where = {"bot_id": {"$eq": bot_id}}

            for fact in facts:
                results = await loop.run_in_executor(
                    None, 
                    partial(ChromaStore.query_similar_sync, fact, where, 3)
                )
                if results and results.get("documents") and results["documents"][0]:
                    docs = results["documents"][0]
                    ids = results["ids"][0] if results.get("ids") else []
                    dists = results["distances"][0] if results.get("distances") else []
                    
                    for idx, doc in enumerate(docs):
                        item_id = ids[idx]
                        dist = dists[idx] if idx < len(dists) else 1.0
                        # 仅将语义距离在 0.25 (即相似度 > 0.75) 以内的记录纳入审查
                        if dist < 0.25:
                            candidates[item_id] = doc
        except Exception:
            log.exception("ConflictResolver: failed to fetch candidates for conflict check")
            return []

        if not candidates:
            return []

        # 2. 将所有新事实与召回的冲突候选合并，打包执行 1 次 LLM 进行批量判断（大幅缩减 LLM 调用成本）
        new_facts_text = "\n".join(f"- {fact}" for fact in facts)
        existing_memories_text = "\n".join(f"- [ID: {item_id}] {doc}" for item_id, doc in candidates.items())

        try:
            prompt = BATCH_CONFLICT_PROMPT.format(
                new_facts_text=new_facts_text,
                existing_memories_text=existing_memories_text
            )
            # #1: 用群组配置的 provider/model；零温确保 JSON 判定稳定。
            res = await call_ai_once(
                prompt,
                [{"role": "user", "content": "请检测冲突。"}],
                provider, model, temperature=0.0, max_tokens=256,
            )
            response = ((res.get("content") if isinstance(res, dict) and res.get("type") == "text" else "") or "").strip()
            
            # 解析 JSON 数组并严格过滤非法 ID
            import json
            json_match = re.search(r"\[.*\]", response, re.DOTALL)
            if json_match:
                response = json_match.group(0)
                
            ids_to_delete = json.loads(response)
            if isinstance(ids_to_delete, list):
                # 校验 ID 是否确实在召回的候选池中，防止 LLM 幻觉产生误删
                return [str(item_id) for item_id in ids_to_delete if str(item_id) in candidates]
        except Exception:
            log.exception("ConflictResolver: failed to resolve conflicts via batch LLM")
            
        return []


# ── 5. TimeDecayRanker ── 绝对指数衰减排序
class TimeDecayRanker:
    """基于物理时间差应用绝对指数衰减的排序器（半衰期默认7天）。"""

    def __init__(self, half_life_days: float = 7.0):
        self.decay_const = 0.693147 / (half_life_days * 86400.0)

    def rank(self, docs: list[str], metas: list[dict], dists: list[float], top_k: int) -> list[str]:
        t_now = time.time()
        scored = []
        
        for idx in range(len(docs)):
            dist = dists[idx] if idx < len(dists) else 1.0
            sim = max(0.0, min(1.0, 1.0 - dist))
            
            meta = metas[idx] if idx < len(metas) else {}
            t_val = meta.get("timestamp")
            if t_val is None:
                # 兼容旧数据：若没有 timestamp，假定为 30 天前的旧数据以产生充分衰减
                t_val = t_now - 30.0 * 86400.0
                
            delta_t = max(0.0, t_now - t_val)
            recency = math.exp(-self.decay_const * delta_t)
            
            # 绝对衰减加权：0.7 * 语义相似度 + 0.3 * 时间衰减指数
            score = 0.7 * sim + 0.3 * recency
            scored.append((score, docs[idx]))
            
        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]


# ── 6. QueryRewriter ── 对话检索意图改写
class QueryRewriter:
    """workflow bot 的 trigger 是模板话术（"请发表你在本轮的观点"），本身没有可检索的主题。

    #2: 改写在本地完成 —— 从最近的历史消息里取出真实话题作为检索词，
    不再在热路径上 await 一次 LLM 往返（旧实现每个 generic 轮次都阻塞一次 call_ai）。
    纯同步、无网络。
    """

    # 模板化 trigger：完全匹配的短指令
    _GENERIC_EXACT = {"继续", "下一步", "开始", "发表观点", "开始工作", "请继续"}
    # 模板化 trigger：workflow 注入句的子串特征
    _GENERIC_SUBSTR = ("请根据以上讨论内容", "发表你在本轮的观点")

    @staticmethod
    def _is_generic(query: str) -> bool:
        if any(s in query for s in QueryRewriter._GENERIC_SUBSTR):
            return True
        if len(query) < 25:
            trimmed = query.strip().strip("。").strip("！")
            if trimmed in QueryRewriter._GENERIC_EXACT:
                return True
        return False

    @staticmethod
    def rewrite(query: str, history: list[dict] | None) -> str:
        if not history or not QueryRewriter._is_generic(query):
            return query

        # 模板化 trigger：优先用最近一条实质性的真人消息（话题来源）作检索词，
        # 否则退回最近一条实质性消息；都没有则保留原 query。
        def _substantive(predicate):
            for m in reversed(history):
                if predicate(m):
                    content = str(m.get("content") or "").strip()
                    if len(content) >= 10:
                        return content[:200]
            return None

        topic = _substantive(lambda m: m.get("sender_type") in ("human", "user"))
        if topic is None:
            topic = _substantive(lambda m: True)
        if topic is not None:
            log.info("QueryRewriter: generic trigger '%s' -> topic '%s'", query, topic)
            return topic
        return query


# ── 7. Public APIs ── 导出给外部的公共函数接口 (向下兼容)

async def add_to_chroma(message_id: int, content: str, role: str, bot_id: int, group_id: int | None = None,
                        provider: str = "deepseek", model: str = "deepseek-chat"):
    # 1. 噪声快筛与事实提取
    facts = await FactExtractor.extract(content, provider, model)
    if not facts:
        return

    # 2. 批量消解冲突（将 N 次冲突检测合并为 1 次 LLM 调用，防限流）
    del_ids = await ConflictResolver.resolve_batch(facts, bot_id, group_id, provider, model)

    loop = asyncio.get_running_loop()
    
    # 3. 对每个事实执行写入，并清除对应冲突 ID
    for idx, fact in enumerate(facts):
        metadata = {
            "bot_id": bot_id,
            "role": role or "",
            "timestamp": time.time()
        }
        if group_id is not None:
            metadata["group_id"] = group_id
            
        fact_id = f"{message_id}_{idx}"
        
        # 如果有该新事实对应的失效旧记忆，将其删除
        del_id = del_ids[idx] if idx < len(del_ids) else None
        await loop.run_in_executor(
            None,
            partial(ChromaStore.write_fact_sync, fact_id, fact, metadata, del_id)
        )

    # 4. 遗忘机制：写入时有 10% 的概率被动在后台运行过期记忆的 TTL 清理，防爆库
    import random
    if random.random() < 0.1:
        loop.run_in_executor(None, ChromaStore.prune_expired_memories_sync)


async def retrieve_relevant(bot_id: int, group_id: int | None, query: str, top_k: int = 3) -> list:
    loop = asyncio.get_running_loop()
    try:
        if group_id is not None:
            where = {"$and": [{"bot_id": {"$eq": bot_id}}, {"group_id": {"$eq": group_id}}]}
        else:
            where = {"bot_id": {"$eq": bot_id}}
            
        candidates_k = max(top_k * 3, 10)
        results = await loop.run_in_executor(
            None,
            partial(ChromaStore.query_similar_sync, query, where, candidates_k)
        )
        
        if not results or not results.get("documents") or not results["documents"][0]:
            log.info("Memory RAG: query='%s', bot_id=%s, group_id=%s, candidates_fetched=0", query, bot_id, group_id)
            return []
            
        docs = results["documents"][0]
        metas = results["metadatas"][0] if results.get("metadatas") else []
        dists = results["distances"][0] if results.get("distances") else []
        
        # 绝对指数衰减打分与精排
        ranker = TimeDecayRanker()
        relevant = ranker.rank(docs, metas, dists, top_k)
        
        # 可观测性评估：记录命中与排序指标
        log.info(
            "Memory RAG Retrieval: query='%s', bot_id=%s, group_id=%s, fetched=%d, returned=%d",
            query, bot_id, group_id, len(docs), len(relevant)
        )
        return relevant
    except Exception:
        log.exception("retrieve_relevant: failed to fetch similar memories")
        return []


async def delete_bot_memory(bot_id: int, group_id: int | None = None):
    """清除 Bot 的向量记忆。异常时优雅降级并记录日志，防止阻断 SQLite 的清除事务。"""
    loop = asyncio.get_running_loop()
    if group_id is not None:
        where = {"$and": [{"bot_id": {"$eq": bot_id}}, {"group_id": {"$eq": group_id}}]}
    else:
        where = {"bot_id": {"$eq": bot_id}}
    try:
        await loop.run_in_executor(None, partial(ChromaStore.delete_sync, where))
    except Exception:
        log.exception("delete_bot_memory: failed to clear Chroma memory for bot_id=%s, group_id=%s", bot_id, group_id)


async def maybe_summarize(group_id: int, bot_id: int, role: str, member_ids: list):
    from ai.client import call_ai
    if not member_ids:
        return
    try:
        async with get_db() as db:
            # Enforce group_id filter to avoid cross-group summary pollution
            async with db.execute(
                "SELECT covered_through_id FROM role_summaries WHERE bot_id=? AND group_id=? ORDER BY id DESC LIMIT 1",
                (bot_id, group_id)
            ) as cur:
                last = await cur.fetchone()

            last_id = last[0] if last else 0

            ph = ",".join("?" * len(member_ids))
            # Enforce group_id filter on messages to prevent cross-group messages mixing (DFT-008 correction)
            async with db.execute(
                f"SELECT id, content FROM messages WHERE group_id=? AND member_id IN ({ph}) AND id > ? ORDER BY id",
                [group_id] + member_ids + [last_id]
            ) as cur:
                new_msgs = await cur.fetchall()

        if len(new_msgs) < SUMMARY_THRESHOLD:
            return

        batch = new_msgs[:SUMMARY_THRESHOLD]
        text = "\n".join(f"[{mid}] {content}" for mid, content in batch)
        summary = await call_ai(
            "你是会话摘要助手，请用中文将以下内容提炼为5个以内的核心要点，每点一行。",
            [],
            text
        )

        async with get_db() as db:
            await db.execute(
                "INSERT INTO role_summaries (bot_id, group_id, role, summary, covered_through_id) VALUES (?, ?, ?, ?, ?)",
                (bot_id, group_id, role, summary, batch[-1][0])
            )
            await db.commit()
    except Exception:
        log.exception("maybe_summarize failed (bot_id=%s, group_id=%s)", bot_id, group_id)


async def get_memory_context(bot_id: int, role: str, query: str, group_id: int | None = None, history: list | None = None) -> str:
    parts = []

    # 1. 历史摘要（SQLite，按 Bot 个人）
    try:
        async with get_db() as db:
            if group_id is not None:
                async with db.execute(
                    "SELECT summary FROM role_summaries WHERE bot_id=? AND group_id=? ORDER BY id DESC LIMIT 3",
                    (bot_id, group_id)
                ) as cur:
                    summaries = await cur.fetchall()
            else:
                async with db.execute(
                    "SELECT summary FROM role_summaries WHERE bot_id=? ORDER BY id DESC LIMIT 3",
                    (bot_id,)
                ) as cur:
                    summaries = await cur.fetchall()
        if summaries:
            combined = "\n".join(r[0] for r in reversed(summaries))
            parts.append(f"【我的历史经验摘要】\n{combined}")
    except Exception:
        log.exception("get_memory_context: loading role summaries failed (bot_id=%s)", bot_id)

    # 2. 检索前重写通用/模板化的 trigger 消息，使得检索具备明确主题 (#2: 本地启发式，无 LLM)
    search_query = QueryRewriter.rewrite(query, history)

    # 3. 语义检索（Chroma，Bot 个人知识库）
    relevant = await retrieve_relevant(bot_id, group_id, search_query)
    if relevant:
        parts.append("【相关历史记录】\n" + "\n---\n".join(relevant))

    return "\n\n".join(parts)


# ── 8. Migration Utilities ── 历史遗留数据升级迁移脚本 (向后兼容)

async def backfill_chroma_timestamps():
    """回填 Chroma 中旧记忆的 timestamp。从 SQLite messages 表读取相应消息的创建时间。"""
    loop = asyncio.get_running_loop()
    from datetime import datetime
    
    try:
        def _get_all_memories_sync():
            col = ChromaStore.get_collection()
            return col.get(include=["metadatas"])
            
        memories = await loop.run_in_executor(None, _get_all_memories_sync)
        if not memories or not memories.get("ids"):
            return
            
        ids = memories["ids"]
        metas = memories["metadatas"]
        
        async with get_db() as db:
            for idx, item_id in enumerate(ids):
                meta = metas[idx] if idx < len(metas) else {}
                if meta and "timestamp" in meta:
                    continue  # 已有时间戳，无需回填
                    
                # 尝试从 ID 解析 message_id (形如 "12" 或 "12_0")
                msg_id_str = item_id.split("_")[0]
                try:
                    msg_id = int(msg_id_str)
                except ValueError:
                    continue
                    
                # 从 SQLite 获取原消息创建时间
                async with db.execute("SELECT created_at FROM messages WHERE id=?", (msg_id,)) as cur:
                    row = await cur.fetchone()
                if not row or not row[0]:
                    continue
                    
                created_str = row[0]
                try:
                    dt = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S")
                    epoch_time = dt.timestamp()
                except Exception:
                    continue
                    
                # 更新 Chroma 对应记录的元数据
                meta["timestamp"] = epoch_time
                def _update_meta_sync(mid: str, m: dict):
                    col = ChromaStore.get_collection()
                    col.update(ids=[mid], metadatas=[m])
                    
                await loop.run_in_executor(None, partial(_update_meta_sync, item_id, meta))
                log.info("backfill_chroma_timestamps: updated ID %s with timestamp %s", item_id, epoch_time)
    except Exception:
        log.exception("backfill_chroma_timestamps failed")
