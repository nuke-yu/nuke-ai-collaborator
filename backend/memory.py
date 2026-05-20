import asyncio
from functools import partial
import chromadb
from chromadb.utils import embedding_functions
from database import get_db

SUMMARY_THRESHOLD = 15

# ── Chroma 初始化（懒加载，首次使用时自动下载 ~80MB embedding 模型）──
_chroma_client = None
_chroma_collection = None

def _get_collection():
    global _chroma_client, _chroma_collection
    if _chroma_collection is None:
        _chroma_client = chromadb.PersistentClient(path="./chroma_db")
        _chroma_collection = _chroma_client.get_or_create_collection(
            name="messages",
            embedding_function=embedding_functions.DefaultEmbeddingFunction(),
            metadata={"hnsw:space": "cosine"},
        )
    return _chroma_collection


# ── 写入 Chroma（在线程池里跑，避免阻塞事件循环）──────────────────────
def _add_sync(message_id: int, content: str, role: str, group_id: int):
    col = _get_collection()
    col.upsert(
        ids=[str(message_id)],
        documents=[content],
        metadatas=[{"group_id": group_id, "role": role or ""}],
    )

async def add_to_chroma(message_id: int, content: str, role: str, group_id: int):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_add_sync, message_id, content, role, group_id))


# ── RAG 检索（语义向量，支持同义词/跨语言）────────────────────────────
def _query_sync(query: str, role: str, group_id: int, top_k: int) -> list:
    col = _get_collection()
    try:
        results = col.query(
            query_texts=[query],
            n_results=top_k,
            where={"$and": [
                {"group_id": {"$eq": group_id}},
                {"role": {"$eq": role}},
            ]},
            include=["documents"],
        )
        return results["documents"][0] if results["documents"] else []
    except Exception:
        return []

async def retrieve_relevant(group_id: int, role: str, query: str, top_k: int = 3) -> list:
    if not role:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_query_sync, query, role, group_id, top_k))


# ── 摘要压缩（超过阈值时把老消息压成要点，存 SQLite）─────────────────
async def maybe_summarize(group_id: int, role: str, member_ids: list):
    from ai_client import call_ai
    if not member_ids:
        return
    try:
        ph = ",".join("?" * len(member_ids))
        async with get_db() as db:
            async with db.execute(
                f"SELECT id, content FROM messages WHERE group_id=? AND member_id IN ({ph}) ORDER BY id",
                [group_id] + member_ids
            ) as cur:
                role_msgs = await cur.fetchall()

            async with db.execute(
                "SELECT covered_through_id FROM role_summaries WHERE group_id=? AND role=? ORDER BY id DESC LIMIT 1",
                (group_id, role)
            ) as cur:
                last = await cur.fetchone()

        last_id = last[0] if last else 0
        new_msgs = [(r[0], r[1]) for r in role_msgs if r[0] > last_id]

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
                "INSERT INTO role_summaries (group_id, role, summary, covered_through_id) VALUES (?, ?, ?, ?)",
                (group_id, role, summary, batch[-1][0])
            )
            await db.commit()
    except Exception:
        pass


# ── 组合记忆上下文（注入 system prompt）──────────────────────────────
async def get_memory_context(group_id: int, role: str, query: str) -> str:
    if not role:
        return ""

    parts = []

    # 1. 历史摘要（SQLite）
    try:
        async with get_db() as db:
            async with db.execute(
                "SELECT summary FROM role_summaries WHERE group_id=? AND role=? ORDER BY id DESC LIMIT 3",
                (group_id, role)
            ) as cur:
                summaries = await cur.fetchall()
        if summaries:
            combined = "\n".join(r[0] for r in reversed(summaries))
            parts.append(f"【历史工作摘要】\n{combined}")
    except Exception:
        pass

    # 2. 语义检索（Chroma）
    relevant = await retrieve_relevant(group_id, role, query)
    if relevant:
        parts.append("【相关历史记录】\n" + "\n---\n".join(relevant))

    return "\n\n".join(parts)
