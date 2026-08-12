"""Physical Chroma client for canonical derived Memory projections."""
from __future__ import annotations

import logging
import time
from typing import Any

import chromadb

from ai import embeddings
from core import config

log = logging.getLogger(__name__)
_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        try:
            _client = chromadb.PersistentClient(path="./chroma_db")
            signature = embeddings.embedding_signature()
            collection = _client.get_or_create_collection(
                name="messages",
                embedding_function=embeddings.get_embedding_function(),
                metadata={"hnsw:space": "cosine", "emb_sig": signature},
            )
            embeddings.verify_signature((collection.metadata or {}).get("emb_sig"), signature)
            _collection = collection
        except BaseException as exc:
            _client = None
            _collection = None
            if isinstance(exc, (KeyboardInterrupt, SystemExit)) or type(exc).__name__ == "CancelledError":
                raise
            raise RuntimeError(
                f"chroma collection unavailable: {type(exc).__module__}.{type(exc).__name__}: {exc}"
            ) from exc
    return _collection


class ChromaProjectionClient:
    @staticmethod
    def write_sync(record_id: str, content: str, metadata: dict[str, Any]) -> None:
        safe_content = content
        try:
            from executors.redaction import redact_secrets
            safe_content, _ = redact_secrets(safe_content)
        except Exception:
            log.exception("Chroma projection redaction failed")
        _get_collection().upsert(ids=[record_id], documents=[safe_content], metadatas=[metadata])

    @staticmethod
    def delete_ids_sync(record_ids: list[str]) -> None:
        if record_ids:
            _get_collection().delete(ids=record_ids)

    @staticmethod
    def get_by_ids_sync(record_ids: list[str]) -> dict[str, Any]:
        if not record_ids:
            return {}
        return _get_collection().get(ids=record_ids, include=["documents", "metadatas"])

    @staticmethod
    def scan_bot_memory_sync(group_id: int, limit: int, offset: int = 0) -> dict[str, Any]:
        return _get_collection().get(
            where={"$and": [
                {"group_id": {"$eq": group_id}},
                {"$or": [
                    {"mem_type": {"$eq": "reflection"}},
                    {"$and": [
                        {"mem_type": {"$ne": "tool_episode"}},
                        {"mem_type": {"$ne": "experience"}},
                    ]},
                ]},
            ]},
            limit=max(1, limit), offset=max(0, offset),
            include=["documents", "metadatas"],
        )

    @staticmethod
    def prune_sync(
        fact_max_age_seconds: float | None = None,
        reflection_max_age_seconds: float | None = None,
    ) -> None:
        collection = _get_collection()
        now = time.time()
        fact_age = config.MEMORY_TTL_DAYS * 86400 if fact_max_age_seconds is None else fact_max_age_seconds
        reflection_age = config.REFLECT_TTL_DAYS * 86400 if reflection_max_age_seconds is None else reflection_max_age_seconds
        collection.delete(where={"$and": [{"timestamp": {"$lt": now - fact_age}}, {"mem_type": {"$ne": "reflection"}}]})
        collection.delete(where={"$and": [{"timestamp": {"$lt": now - reflection_age}}, {"mem_type": {"$eq": "reflection"}}]})
