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
否则，请提取核心事实并附带重要性打分（Salience Score，介于 0.0 到 1.0 之间，值越高代表信息对后续开发和项目决策越重要，例如技术决策为 0.9，配置修改为 0.8，用户偏好为 0.7）。
每行一条事实，格式为：事实内容|分数（不要带序号、前缀或任何标点符号），例如：
将服务端口修改为8080|0.9
用户偏好使用Python进行脚本编写|0.7
项目已切换到React 19版本|0.9

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

REFLECTION_PROMPT = """你是一个项目记忆巩固助手。下面是某个智能体近期积累的若干零散事实/经历。
请跨这些事实归纳出更高层、可泛化的洞察、规律或项目级结论（例如反复出现的问题、用户的稳定偏好、技术决策背后的模式），而不是简单复述单条事实。
只输出真正有概括价值的洞察；若这些事实之间没有可归纳的规律，请直接返回 NO_INSIGHT。
每行一条洞察，格式为：洞察内容|重要性分数（0.0-1.0，越高代表对后续决策越关键），不要带序号、前缀或其它标点，例如：
项目多次因环境配置不一致导致部署失败，配置管理是薄弱环节|0.9
用户稳定偏好简洁、可维护的方案而非堆砌特性|0.8

事实列表：
"""


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
    def delete_ids_sync(cls, ids: list[str]):
        """按 ID 批量删除（用于一次性清除被新事实覆盖失效的旧记忆）。"""
        if not ids:
            return
        col = cls.get_collection()
        try:
            col.delete(ids=ids)
        except Exception:
            log.exception("ChromaStore: failed to delete conflicting IDs %s", ids)

    @classmethod
    def prune_expired_memories_sync(cls, fact_max_age_seconds: float | None = None,
                                    reflection_max_age_seconds: float | None = None):
        """物理清除过期记忆 (遗忘机制：TTL)，反思洞察用更长 TTL (P2)。

        分两次删，按 mem_type 区隔：
          - 事实/遗留无类型：超过 fact TTL 删；用 $ne reflection 确保**绝不误删反思**；
          - 反思：仅超过更长的 reflection TTL 才删。
        """
        col = cls.get_collection()
        now = time.time()
        fact_age = config.MEMORY_TTL_DAYS * 86400 if fact_max_age_seconds is None else fact_max_age_seconds
        refl_age = config.REFLECT_TTL_DAYS * 86400 if reflection_max_age_seconds is None else reflection_max_age_seconds
        try:
            col.delete(where={"$and": [
                {"timestamp": {"$lt": now - fact_age}},
                {"mem_type": {"$ne": "reflection"}},
            ]})
            col.delete(where={"$and": [
                {"timestamp": {"$lt": now - refl_age}},
                {"mem_type": {"$eq": "reflection"}},
            ]})
            log.info("ChromaStore: pruned facts>%ds, reflections>%ds", fact_age, refl_age)
        except Exception:
            log.exception("ChromaStore: failed to prune expired memories")

    @classmethod
    def get_memories_since_sync(cls, bot_id: int, group_id: int | None, since_ts: float) -> dict:
        """取某 bot 在某群、timestamp 晚于水位线的全部记忆（用 get 而非相似度 query）。
        反思巩固用：拿增量事实做归纳，不需要语义相似度。"""
        col = cls.get_collection()
        conds = [{"bot_id": {"$eq": bot_id}}, {"timestamp": {"$gt": since_ts}}]
        if group_id is not None:
            conds.append({"group_id": {"$eq": group_id}})
        where = conds[0] if len(conds) == 1 else {"$and": conds}
        return col.get(where=where, include=["documents", "metadatas"])

    @classmethod
    def get_by_ids_sync(cls, ids: list[str]) -> dict:
        """按 id 取记忆（A-MEM provenance 链接遍历用）。"""
        if not ids:
            return {}
        col = cls.get_collection()
        return col.get(ids=ids, include=["documents", "metadatas"])


# ── 3. FactExtractor ── 关键事实与噪音快筛
class FactExtractor:
    """提取发言中的高价值事实，过滤 conversational noise (如“好的”)。"""
    
    @staticmethod
    async def extract(content: str, provider: str = "deepseek", model: str = "deepseek-chat") -> list[tuple[str, float]]:
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
            
            extracted = []
            for line in text.split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    parts = line.split("|")
                    fact_text = parts[0].strip()
                    try:
                        score = float(parts[1].strip())
                        score = max(0.0, min(1.0, score))
                    except ValueError:
                        score = 0.5
                    extracted.append((fact_text, score))
                else:
                    extracted.append((line, 0.5))
            return extracted
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
    """基于物理时间差应用绝对指数衰减的排序器（半衰期默认7天），融合重要性和关键字增强并设绝对相似度下限。"""

    def __init__(self, half_life_days: float = 7.0, similarity_floor: float | None = None,
                 reflection_bonus: float | None = None):
        self.decay_const = 0.693147 / (half_life_days * 86400.0)
        # None → 用配置项（可经 NUKE_MEMORY_SIMILARITY_FLOOR 覆盖）；显式传值用于测试。
        self.similarity_floor = config.MEMORY_SIMILARITY_FLOOR if similarity_floor is None else similarity_floor
        # 反思洞察的加性检索 bonus (P2)，让沉淀的高层语义知识更易浮现。
        self.reflection_bonus = config.REFLECT_RETRIEVAL_BONUS if reflection_bonus is None else reflection_bonus

    def rank(self, docs: list[str], metas: list[dict], dists: list[float], top_k: int, query: str = "") -> list[str]:
        t_now = time.time()
        scored = []
        
        # 提取查询语句中的核心英文/数字关键字，用于精确名词/配置的检索加权增强
        keywords = []
        if query:
            # \u53ea\u53d6\u82f1\u6570\u5173\u952e\u5b57\uff088080/React/CDG\uff09\uff1a\u4e2d\u6587\u65e0\u7a7a\u683c\u5206\u8bcd\uff0c\u6574\u6bb5\u6210\u4e00\u4e2a token\u3001
            # \u51e0\u4e4e\u4e0d\u53ef\u80fd\u5b50\u4e32\u547d\u4e2d\u6587\u6863\u800c\u5f92\u589e\u566a\u58f0\uff1b\u4e2d\u6587\u8bed\u4e49\u5339\u914d\u7531\u5411\u91cf\u76f8\u4f3c\u5ea6\u627f\u62c5\u3002
            keywords = [w.lower() for w in re.findall(r'[a-zA-Z0-9]+', query) if len(w) > 1]
            
        for idx in range(len(docs)):
            dist = dists[idx] if idx < len(dists) else 1.0
            sim = max(0.0, min(1.0, 1.0 - dist))
            
            # 🔴 检索加绝对相似度下限（防检索幻觉，文档头号坑）
            if sim < self.similarity_floor:
                continue
                
            meta = metas[idx] if idx < len(metas) else {}
            importance = meta.get("importance", 0.5)  # 获取写入时存进 metadata 的重要性分值
            
            t_val = meta.get("timestamp")
            if t_val is None:
                t_val = t_now - 30.0 * 86400.0
                
            delta_t = max(0.0, t_now - t_val)
            recency = math.exp(-self.decay_const * delta_t)
            
            # 🟡 混合检索关键字增强：如果文档中匹配到了特定的精确名词/配置，则提升其打分
            keyword_boost = 0.0
            if keywords:
                doc_lower = docs[idx].lower()
                matches = sum(1 for kw in keywords if kw in doc_lower)
                if matches > 0:
                    keyword_boost = min(0.2, 0.08 * matches)
            
            # 🟡 完整三因子加权公式：0.5 * 语义相似度 + 0.3 * 时间衰减 + 0.2 * 重要性 + keyword_boost
            score = 0.5 * sim + 0.3 * recency + 0.2 * importance + keyword_boost
            # 🟢 反思洞察加性 bonus（沉淀的语义知识，P2）
            if meta.get("mem_type") == "reflection":
                score += self.reflection_bonus
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

    # 2. 批量消解冲突
    del_ids = await ConflictResolver.resolve_batch([f[0] for f in facts], bot_id, group_id, provider, model)

    loop = asyncio.get_running_loop()

    # 3. 写入全部新事实并保存重要性评分与访问频次统计
    for idx, (fact, score) in enumerate(facts):
        metadata = {
            "bot_id": bot_id,
            "role": role or "",
            "timestamp": time.time(),
            "importance": score,       # 存入重要性分数，供检索三因子加权 (Problem 2)
            "mem_type": "fact",        # 区分情景/原子事实 vs 反思洞察 (P1 巩固层)
        }
        if group_id is not None:
            metadata["group_id"] = group_id

        fact_id = f"{message_id}_{idx}"
        await loop.run_in_executor(
            None,
            partial(ChromaStore.write_fact_sync, fact_id, fact, metadata)
        )

    # 4. 一次性删除被新事实整体覆盖而失效的旧记忆。del_ids 是一组陈旧记忆 ID，
    #    与具体哪条新事实没有位置对应关系，故必须批量删除（不能按 facts 下标配对，
    #    否则 len(del_ids) > len(facts) 时多出的冲突 ID 永不删除、陈旧事实残留）。
    if del_ids:
        await loop.run_in_executor(
            None,
            partial(ChromaStore.delete_ids_sync, del_ids)
        )

    # 5. 遗忘机制：写入时有 10% 的概率被动在后台运行过期记忆的 TTL 清理，防爆库
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
        
        # 绝对指数衰减打分与精排，包含重要性与关键字增强
        ranker = TimeDecayRanker()
        relevant = ranker.rank(docs, metas, dists, top_k, query)
        
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


# 分库探测缓存：(默认库路径, 表名) → 该表是否存在于默认连接所指向的库。
# 表在某个物理库文件中的存在性在迁移后是静态的，故可永久缓存，避免热路径上
# （get_memory_context 每个 bot 轮次都跑）每次多开一个连接查 sqlite_master。
_table_presence_cache: dict[tuple[str, str], bool] = {}


def _default_db_path() -> str:
    """读端 get_db()/connect() 实际解析到的库路径：当前 bind_db 绑定的群库，否则 db.DB_PATH。
    写端用它显式传给 write_connect，以对齐读端（db/writer.py 有独立的 DB_PATH，无参时会分叉）。"""
    from db.context import current_db_path
    from db import DB_PATH
    return current_db_path.get() or DB_PATH


async def _resolve_memory_db_path(table_name: str, group_id: int | None) -> str | None:
    """决定某群表实际落在哪个库文件。

    返回 None  → 用默认库（读 get_db()、写 write_connect(默认路径)）：覆盖单库/测试模式，
                 以及群已被 bind_db 绑定的情形——此时默认连接本就指向对的库。
    返回 gpath → 分库模式且默认（中央）库没有该群表 → 显式路由到该群私有库。

    读写两端都经此解析同一路径，保证落到**同一个库**（消除读写不对称）。
    """
    default_path = _default_db_path()
    key = (default_path, table_name)
    exists = _table_presence_cache.get(key)
    if exists is None:
        try:
            async with get_db() as db:
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,)
                ) as cur:
                    exists = (await cur.fetchone()) is not None
        except Exception:
            # 探测失败：保守按"默认库可用"，走默认连接而非误连群库
            return None
        _table_presence_cache[key] = exists
    if exists:
        return None  # 默认连接已指向正确的库
    from runtime.dbpaths import group_db_path
    return group_db_path(group_id) if group_id is not None else None


async def _memory_db(table_name: str, group_id: int | None, *, write: bool):
    """返回针对 table_name 的（未进入的）异步连接上下文。读写共用同一路径解析。

    读默认走 get_db()（可被测试 patch、跟随 contextvar）；写默认显式 write_connect(默认路径)，
    与读端解析到同一库（不能用无参 write_connect —— writer 自带 DB_PATH，会与 db.DB_PATH 分叉）。
    """
    from db import connect
    from db.writer import write_connect
    path = await _resolve_memory_db_path(table_name, group_id)
    if write:
        return write_connect(path if path else _default_db_path())
    return connect(path) if path else get_db()


async def maybe_summarize(group_id: int, bot_id: int, role: str, member_ids: list):
    from ai.client import call_ai
    if not member_ids:
        return
    try:
        async with await _memory_db("role_summaries", group_id, write=False) as db:
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

        async with await _memory_db("role_summaries", group_id, write=True) as db:
            await db.execute(
                "INSERT INTO role_summaries (bot_id, group_id, role, summary, covered_through_id) VALUES (?, ?, ?, ?, ?)",
                (bot_id, group_id, role, summary, batch[-1][0])
            )
            await db.commit()
    except Exception:
        log.exception("maybe_summarize failed (bot_id=%s, group_id=%s)", bot_id, group_id)


async def _get_reflection_watermark(bot_id: int, group_id: int | None) -> float:
    """读取 (bot, group) 的反思水位线（已巩固到的最新事实 timestamp）；无记录返回 0。"""
    try:
        async with await _memory_db("reflection_state", group_id, write=False) as db:
            async with db.execute(
                "SELECT covered_through_ts FROM reflection_state WHERE bot_id=? AND group_id=?",
                (bot_id, group_id)
            ) as cur:
                row = await cur.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
    except Exception:
        # 表缺失（旧库未迁移）或读取失败：当作 0，让本次反思跑全量，不阻断
        log.warning("reflection: watermark read failed (bot_id=%s, group_id=%s)", bot_id, group_id)
        return 0.0


async def _set_reflection_watermark(bot_id: int, group_id: int | None, ts: float) -> None:
    """推进 (bot, group) 的反思水位线（upsert）。"""
    sql = (
        "INSERT INTO reflection_state (bot_id, group_id, covered_through_ts, updated_at) "
        "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(bot_id, group_id) DO UPDATE SET "
        "covered_through_ts=excluded.covered_through_ts, updated_at=CURRENT_TIMESTAMP"
    )
    async with await _memory_db("reflection_state", group_id, write=True) as db:
        await db.execute(sql, (bot_id, group_id, ts))
        await db.commit()



async def maybe_reflect(group_id: int, bot_id: int, role: str,
                        provider: str = "deepseek", model: str = "deepseek-chat") -> None:
    """巩固层：把自上次反思以来积累的零散事实周期性归纳为高层语义洞察。

    重要性累积触发；产出的洞察作为 mem_type=reflection 写回 Chroma，靠高 importance +
    bonus 在三因子检索中浮起。后台运行，任何失败只记日志、绝不冒泡。

    单层(默认)：只反思 mem_type=fact，排除既有反思，防误差放大。
    多层(P3, REFLECT_MULTILEVEL=1)：允许把未到顶(level < REFLECT_MAX_LEVEL)的反思一并纳入
    再归纳，形成反思树；新反思 level = 已消费记忆的最大 level + 1（封顶）。
    """
    try:
        loop = asyncio.get_running_loop()
        watermark = await _get_reflection_watermark(bot_id, group_id)

        # 1. 取水位线之后的增量记忆
        res = await loop.run_in_executor(
            None, partial(ChromaStore.get_memories_since_sync, bot_id, group_id, watermark)
        )
        ids = (res or {}).get("ids") or []
        docs = (res or {}).get("documents") or []
        metas = (res or {}).get("metadatas") or []
        if not ids:
            return

        # 候选筛选：单层排除所有反思；多层纳入 level<MAX 的反思（顶层不再被归纳）。
        facts = []           # (doc, importance, ts)
        consumed_ids = []    # 实际进入归纳的记忆 id —— 作为新反思的 provenance 边
        max_ts = watermark
        max_level = 0        # 已消费记忆的最大 level（fact 视作 0）
        for i, item_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            lvl = int(meta.get("level", 0))
            if meta.get("mem_type") == "reflection":
                if not config.REFLECT_MULTILEVEL or lvl >= config.REFLECT_MAX_LEVEL:
                    continue
            doc = docs[i] if i < len(docs) else ""
            if not doc:
                continue
            facts.append((doc, float(meta.get("importance", 0.5)), float(meta.get("timestamp", 0.0))))
            consumed_ids.append(item_id)
            max_ts = max(max_ts, float(meta.get("timestamp", 0.0)))
            max_level = max(max_level, lvl)

        # 2. 触发闸门：条数 + 重要性累积都要够
        if len(facts) < config.REFLECT_MIN_FACTS:
            return
        if sum(f[1] for f in facts) < config.REFLECT_IMPORTANCE_THRESHOLD:
            # 重要性不足：正常下几条就越过阈值；但若长期只积累低价值事实始终不触发，
            # 水位线永不推进、fetch 无界增长。超过积压上限时强制推进水位线丢弃这批
            # 低价值事实（avg importance 极低，本就该被遗忘），消除增长。
            if len(facts) > config.REFLECT_MAX_BACKLOG:
                await _set_reflection_watermark(bot_id, group_id, max_ts)
            return

        new_level = min(max_level + 1, config.REFLECT_MAX_LEVEL)

        # 3. 一次 LLM 归纳（用群组配置的 provider/model）
        from ai.client import call_ai_once
        fact_lines = "\n".join(f"- {doc}" for doc, _, _ in facts)
        res_llm = await call_ai_once(
            REFLECTION_PROMPT + fact_lines,
            [{"role": "user", "content": "请归纳洞察。"}],
            provider, model, temperature=0.3, max_tokens=512,
        )
        text = ((res_llm.get("content") if isinstance(res_llm, dict) and res_llm.get("type") == "text" else "") or "").strip()

        insights: list[tuple[str, float]] = []
        if text and "NO_INSIGHT" not in text:
            for line in text.split("\n"):
                line = line.strip().lstrip("-").strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    txt, _, sc = line.rpartition("|")
                    txt = txt.strip()
                    try:
                        score = max(0.0, min(1.0, float(sc.strip())))
                    except ValueError:
                        txt, score = line, 0.7
                else:
                    txt, score = line, 0.7
                if txt:
                    insights.append((txt[:500], score))
            insights = insights[: config.REFLECT_MAX_INSIGHTS]

        # 4. 写回洞察（即使本轮无洞察，也照样推进水位线，避免反复空跑同一批事实）
        if insights:
            del_ids = await ConflictResolver.resolve_batch(
                [t for t, _ in insights], bot_id, group_id, provider, model
            )
            # provenance 边：只记实际被归纳的记忆 id（多层时即指向下层反思，构成 A-MEM 链）
            source_ids = ",".join(str(i) for i in consumed_ids)
            now = time.time()
            for idx, (insight, score) in enumerate(insights):
                metadata = {
                    "bot_id": bot_id,
                    "role": role or "",
                    "timestamp": now,
                    "importance": score,
                    "mem_type": "reflection",
                    "level": new_level,         # 反思层级 (P3)，封顶后不再被归纳
                    "source_ids": source_ids,   # A-MEM 链接：本反思由哪些记忆提炼而来
                }
                if group_id is not None:
                    metadata["group_id"] = group_id
                refl_id = f"refl_{bot_id}_{group_id}_{int(now * 1000)}_{idx}"
                await loop.run_in_executor(
                    None, partial(ChromaStore.write_fact_sync, refl_id, insight, metadata)
                )
            if del_ids:
                await loop.run_in_executor(None, partial(ChromaStore.delete_ids_sync, del_ids))

        await _set_reflection_watermark(bot_id, group_id, max_ts)
        log.info(
            "Memory Reflection: bot_id=%s, group_id=%s, facts_consumed=%d, insights=%d",
            bot_id, group_id, len(facts), len(insights),
        )
    except Exception:
        log.exception("maybe_reflect failed (bot_id=%s, group_id=%s)", bot_id, group_id)


async def get_memory_links(memory_id: str) -> list[dict]:
    """A-MEM provenance 遍历 (P3)：返回某条记忆（通常是反思）由哪些下层记忆提炼而来。

    沿 `source_ids` 走一跳，返回 [{id, document, mem_type, level}]，用于可解释性
    （“这条洞察基于哪些事实”）与反思树导航。多层时一条 level-2 反思的来源即 level-1
    反思，逐跳调用即可向下展开整条链。
    """
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, partial(ChromaStore.get_by_ids_sync, [memory_id]))
        metas = (res or {}).get("metadatas") or []
        if not metas or not metas[0]:
            return []
        src_ids = [s for s in (metas[0].get("source_ids") or "").split(",") if s]
        if not src_ids:
            return []
        srcs = await loop.run_in_executor(None, partial(ChromaStore.get_by_ids_sync, src_ids))
        s_ids = (srcs or {}).get("ids") or []
        s_docs = (srcs or {}).get("documents") or []
        s_metas = (srcs or {}).get("metadatas") or []
        out = []
        for i, sid in enumerate(s_ids):
            m = s_metas[i] if i < len(s_metas) and s_metas[i] else {}
            out.append({
                "id": sid,
                "document": s_docs[i] if i < len(s_docs) else "",
                "mem_type": m.get("mem_type", "fact"),
                "level": int(m.get("level", 0)),
            })
        return out
    except Exception:
        log.exception("get_memory_links failed for %s", memory_id)
        return []


async def get_memory_context(bot_id: int, role: str, query: str, group_id: int | None = None, history: list | None = None) -> str:
    parts = []

    # 1. 历史摘要（SQLite，按 Bot 个人）
    try:
        async with await _memory_db("role_summaries", group_id, write=False) as db:
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

def _parse_created_at(created_str: str) -> float | None:
    """把 SQLite CURRENT_TIMESTAMP（UTC，'%Y-%m-%d %H:%M:%S'）解析为 UTC epoch 秒，
    与 add_to_chroma 写入的 time.time() 同基准。naive datetime 必须显式按 UTC 处理，
    否则 .timestamp() 会按本地时区换算导致整体偏移。"""
    from datetime import datetime, timezone
    s = (created_str or "").strip().replace("T", " ")
    for cut in ("Z", "+", "."):
        if cut in s:
            s = s.split(cut)[0].strip()
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


async def backfill_chroma_timestamps(dry_run: bool = False) -> dict:
    """回填 Chroma 中缺失 timestamp 的旧记忆，从各群组的 messages 表读取消息创建时间。

    Chroma 是全局单库，但 messages 表分散在各 group DB 中（CELL-04/05），裸 get_db()
    只会落到中央库、查不到群消息。因此按记忆的 group_id 元数据分桶，逐组 bind_db 到对应
    group DB 后批量查询、回填。缺少 group_id 的遗留条目无法定位所属库，跳过并计数。

    返回统计：{"scanned","need","updated","no_group","skipped_no_msg"}。
    dry_run=True 时只统计与日志、不写回 Chroma。
    """
    loop = asyncio.get_running_loop()
    from contextlib import nullcontext
    from collections import defaultdict
    from db import bind_db
    from runtime.dbpaths import group_db_path

    stats = {"scanned": 0, "need": 0, "updated": 0, "no_group": 0, "skipped_no_msg": 0}

    def _get_all_memories_sync():
        col = ChromaStore.get_collection()
        return col.get(include=["metadatas"])

    try:
        memories = await loop.run_in_executor(None, _get_all_memories_sync)
    except Exception:
        log.exception("backfill_chroma_timestamps: failed to read collection")
        return stats

    if not memories or not memories.get("ids"):
        return stats

    ids = memories["ids"]
    metas = memories.get("metadatas") or []
    stats["scanned"] = len(ids)

    # 仅保留缺 timestamp 的条目，按 group_id 分桶（gid=None：无群标记的遗留条目，
    # 走当前绑定/中央库做 best-effort 回退查询）
    by_group: dict[int | None, list[tuple[str, dict]]] = defaultdict(list)
    for idx, item_id in enumerate(ids):
        meta = dict(metas[idx]) if idx < len(metas) and metas[idx] else {}
        if meta.get("timestamp") is not None:
            continue
        stats["need"] += 1
        gid = meta.get("group_id")
        if gid is None:
            stats["no_group"] += 1
            by_group[None].append((item_id, meta))
        else:
            by_group[int(gid)].append((item_id, meta))

    for gid, entries in by_group.items():
        # chroma id 形如 "12" 或 "12_0"；同一 message_id 可对应多条事实
        by_msg: dict[int, list[tuple[str, dict]]] = defaultdict(list)
        for item_id, meta in entries:
            try:
                msg_id = int(item_id.split("_")[0])
            except ValueError:
                continue
            by_msg[msg_id].append((item_id, meta))
        if not by_msg:
            continue

        created: dict[int, str] = {}
        ctx = bind_db(group_db_path(gid)) if gid is not None else nullcontext()
        try:
            with ctx:
                async with get_db() as db:
                    # 分批查询，避免单条 IN(...) 超过 SQLite 占位符上限（旧版 999）
                    msg_ids = list(by_msg.keys())
                    for off in range(0, len(msg_ids), 500):
                        chunk = msg_ids[off:off + 500]
                        ph = ",".join("?" * len(chunk))
                        async with db.execute(
                            f"SELECT id, created_at FROM messages WHERE id IN ({ph})",
                            chunk,
                        ) as cur:
                            for row in await cur.fetchall():
                                created[row[0]] = row[1]
        except Exception:
            log.exception("backfill_chroma_timestamps: DB read failed for group %s", gid)
            continue

        upd_ids: list[str] = []
        upd_metas: list[dict] = []
        for msg_id, items in by_msg.items():
            epoch = _parse_created_at(created.get(msg_id))
            if epoch is None:
                stats["skipped_no_msg"] += len(items)
                continue
            for item_id, meta in items:
                meta["timestamp"] = epoch
                upd_ids.append(item_id)
                upd_metas.append(meta)

        if not upd_ids:
            continue
        stats["updated"] += len(upd_ids)
        if dry_run:
            log.info("backfill_chroma_timestamps (dry-run): group %s would update %d record(s)", gid, len(upd_ids))
            continue

        def _update_batch_sync(uids: list[str], umetas: list[dict]):
            col = ChromaStore.get_collection()
            col.update(ids=uids, metadatas=umetas)

        try:
            await loop.run_in_executor(None, partial(_update_batch_sync, upd_ids, upd_metas))
            log.info("backfill_chroma_timestamps: group %s updated %d record(s)", gid, len(upd_ids))
        except Exception:
            log.exception("backfill_chroma_timestamps: update failed for group %s", gid)

    return stats
