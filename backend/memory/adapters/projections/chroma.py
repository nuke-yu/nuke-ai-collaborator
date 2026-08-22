"""Chroma delivery for canonical Bot Memory projection intents.

Chroma is a derived index.  The adapter intentionally contains no canonical
Memory reads or writes; it only consumes already-committed outbox payloads.
"""
from __future__ import annotations

import asyncio
from typing import Mapping, Any

from memory.contracts.projection import (
    BOT_MEMORY_VECTOR_DELETE,
    BOT_MEMORY_VECTOR_UPSERT,
    EXPERIENCE_VECTOR_UPSERT,
)


class ChromaBotMemoryProjectionDelivery:
    async def deliver(self, projection_type: str, payload: Mapping[str, Any]) -> None:
        from .chroma_client import ChromaProjectionClient

        if projection_type == EXPERIENCE_VECTOR_UPSERT:
            await asyncio.to_thread(
                ChromaProjectionClient.write_sync,
                str(payload["record_id"]),
                str(payload["content"]),
                {
                    "group_id": int(payload["group_id"]),
                    "bot_id": int(payload.get("bot_id") or 0),
                    "mem_type": "experience",
                    "timestamp": float(payload.get("timestamp") or 0),
                    "importance": float(payload.get("confidence") or 0),
                },
            )
            return

        if projection_type == BOT_MEMORY_VECTOR_UPSERT:
            await asyncio.to_thread(
                ChromaProjectionClient.write_sync,
                str(payload["projection_id"]),
                str(payload["content"]),
                dict(payload["metadata"]),
            )
            delete_ids = [str(item) for item in payload.get("delete_ids", ()) if str(item)]
            if delete_ids:
                await asyncio.to_thread(ChromaProjectionClient.delete_ids_sync, delete_ids)
            return
        if projection_type == BOT_MEMORY_VECTOR_DELETE:
            await asyncio.to_thread(
                ChromaProjectionClient.delete_ids_sync,
                [str(payload["projection_id"])],
            )
            return
        raise ValueError(f"unsupported bot memory projection type: {projection_type}")
