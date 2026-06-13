from core import config
import asyncio
import logging
import math
import time
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

CONFLICT_DETECTION_PROMPT = """你是一个事实冲突检测助手。
新事实：'{new_fact}'
旧事实：'{old_fact}'
它们是否描述了同一个属性、配置、路径、偏好或状态，但新事实更新或覆盖了旧事实的值（即两者存在排他性的冲突，新事实使旧事实失效）？
例如：“将服务端口修改为8080” 与 “将服务端口修改为3000” 存在排他性冲突（返回 YES）。
而 “项目已切换到React 19版本” 与 “用户偏好使用Python” 不存在冲突（返回 NO）。
请直接回答 YES 或 NO，不要有任何解释。"""

QUERY_REWRITE_PROMPT = """你是一个搜索查询重写助手。给出一个对话历史和当前指令，请重写为一个简明扼要的搜索关键词查询（不超过15个字），以用于从长短期记忆库中检索出最相关的上下文（技术决策、代码片段或项目细节）。
直接输出重写后的检索关键词，不要有任何前导字、解释或标点符号。"""


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


# ── 3. FactExtractor ── 关键事实与噪音快筛
class FactExtractor:
    """提取发言中的高价值事实，过滤 conversational noise (如“好的”)。"""
    
    @staticmethod
    async def extract(content: str) -> list[str]:
        if not content or not content.strip() or len(content.strip()) < 15:
            return []
            
        from ai.client import call_ai
        try:
            res = await call_ai(FACT_EXTRACTION_PROMPT, [], content)
            res = res.strip()
            if not res or "NO_SALIENT_INFO" in res:
                return []
            return [
                line.strip() 
                for line in res.split("\n") 
                if line.strip() and not line.strip().startswith("#")
            ]
        except Exception:
            log.exception("FactExtractor: failed to extract facts")
            return []


# ── 4. ConflictResolver ── 语义冲突消解
class ConflictResolver:
    """检测新事实与既有向量库的排他性冲突（如端口改变），并返回应失效的旧ID。"""

    @staticmethod
    async def resolve(fact: str, bot_id: int, group_id: int | None) -> str | None:
        from ai.client import call_ai
        loop = asyncio.get_running_loop()
        
        try:
            if group_id is not None:
                where = {"$and": [{"bot_id": {"$eq": bot_id}}, {"group_id": {"$eq": group_id}}]}
            else:
                where = {"bot_id": {"$eq": bot_id}}
                
            results = await loop.run_in_executor(
                None, 
                partial(ChromaStore.query_similar_sync, fact, where, 1)
            )
            
            if not results or not results.get("documents") or not results["documents"][0]:
                return None
                
            old_fact = results["documents"][0][0]
            old_id = results["ids"][0][0]
            dist = results["distances"][0][0] if results.get("distances") else 1.0
            
            # 语义距离在 0.25 (即相似度 > 0.75) 以内，检查新老属性冲突
            if dist < 0.25:
                user_input = f"新事实：'{fact}'\n旧事实：'{old_fact}'"
                conflict_res = await call_ai(CONFLICT_DETECTION_PROMPT, [], user_input)
                if "YES" in conflict_res.upper():
                    return old_id
        except Exception:
            log.exception("ConflictResolver: error during conflict check")
            
        return None


# ── 5. TimeDecayRanker ── 绝对指数衰减排序
class TimeDecayRanker:
    """基于物理时间差应用绝对指数衰减的排序器（半衰期默认7天）。"""

    def __init__(self, half_life_days: float = 7.0):
        # 衰减常数 lambda = ln(2) / 半衰期秒数
        self.decay_const = 0.693147 / (half_life_days * 86400.0)

    def rank(self, docs: list[str], metas: list[dict], dists: list[float], ids: list[str], top_k: int) -> list[str]:
        t_now = time.time()
        scored = []
        
        for idx in range(len(docs)):
            dist = dists[idx] if idx < len(dists) else 1.0
            # 相似度：cosine similarity = 1.0 - distance
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
    """对于 workflow bot generic trigger，根据上下文历史改写为明确主题的搜索词。"""

    @staticmethod
    async def rewrite(query: str, history: list[dict] | None) -> str:
        if not history:
            return query
            
        is_generic = False
        if len(query) < 50:
            generic_keywords = ["开始", "观点", "讨论", "继续", "完成", "下一步", "发表", "请根据", "本轮", "以上", "总结"]
            if any(kw in query for kw in generic_keywords):
                is_generic = True
                
        if not is_generic:
            return query
            
        from ai.client import call_ai
        try:
            recent = history[-3:]
            history_text = "\n".join(
                f"[{m.get('sender_name', 'User')}]: {str(m.get('content', ''))[:200]}" 
                for m in recent
            )
            user_input = f"对话历史：\n{history_text}\n\n当前指令：{query}"
            rewritten = await call_ai(QUERY_REWRITE_PROMPT, [], user_input)
            rewritten = rewritten.strip().strip('"').strip("'")
            if rewritten and len(rewritten) < 50:
                log.info("QueryRewriter: rewrote generic query '%s' -> '%s'", query, rewritten)
                return rewritten
        except Exception:
            log.exception("QueryRewriter: rewrite failed")
            
        return query


# ── 7. Public APIs ── 导出给外部的公共函数接口 (向下兼容)

async def add_to_chroma(message_id: int, content: str, role: str, bot_id: int, group_id: int | None = None):
    # 1. 噪声快筛与事实提取
    facts = await FactExtractor.extract(content)
    if not facts:
        return

    loop = asyncio.get_running_loop()
    
    # 2. 对每个事实执行冲突检测，写入时传入当前物理时间戳
    for idx, fact in enumerate(facts):
        del_id = await ConflictResolver.resolve(fact, bot_id, group_id)
        
        metadata = {
            "bot_id": bot_id,
            "role": role or "",
            "timestamp": time.time()
        }
        if group_id is not None:
            metadata["group_id"] = group_id
            
        fact_id = f"{message_id}_{idx}"
        await loop.run_in_executor(
            None,
            partial(ChromaStore.write_fact_sync, fact_id, fact, metadata, del_id)
        )


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
            return []
            
        docs = results["documents"][0]
        metas = results["metadatas"][0] if results.get("metadatas") else []
        dists = results["distances"][0] if results.get("distances") else []
        ids = results["ids"][0] if results.get("ids") else []
        
        # 绝对指数衰减打分与精排
        ranker = TimeDecayRanker()
        return ranker.rank(docs, metas, dists, ids, top_k)
    except Exception:
        log.exception("retrieve_relevant: failed to fetch similar memories")
        return []


async def delete_bot_memory(bot_id: int, group_id: int | None = None):
    loop = asyncio.get_running_loop()
    if group_id is not None:
        where = {"$and": [{"bot_id": {"$eq": bot_id}}, {"group_id": {"$eq": group_id}}]}
    else:
        where = {"bot_id": {"$eq": bot_id}}
    await loop.run_in_executor(None, partial(ChromaStore.delete_sync, where))


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

    # 2. 检索前重写通用/模板化的 trigger 消息，使得检索具备明确主题 (Problem 4)
    search_query = await QueryRewriter.rewrite(query, history)

    # 3. 语义检索（Chroma，Bot 个人知识库）
    relevant = await retrieve_relevant(bot_id, group_id, search_query)
    if relevant:
        parts.append("【相关历史记录】\n" + "\n---\n".join(relevant))

    return "\n\n".join(parts)
