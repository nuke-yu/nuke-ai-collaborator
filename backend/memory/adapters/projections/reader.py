"""Read-only Chroma reader for canonical projection audits and backfills."""
from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from memory.ports import BotMemoryProjectionReaderPort

from .chroma_client import ChromaProjectionClient


class ChromaBotMemoryProjectionReader(BotMemoryProjectionReaderPort):
    async def read_by_ids(
        self, projection_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, Any]]:
        result = await asyncio.to_thread(
            ChromaProjectionClient.get_by_ids_sync, list(projection_ids)
        )
        return _projection_items(result)

    async def scan_group(
        self, group_id: int, *, limit: int, offset: int = 0
    ) -> Mapping[str, Mapping[str, Any]]:
        result = await asyncio.to_thread(
            ChromaProjectionClient.scan_bot_memory_sync, group_id, limit, offset
        )
        return _projection_items(result)


def _projection_items(result: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    ids = (result or {}).get("ids") or []
    documents = (result or {}).get("documents") or []
    metadatas = (result or {}).get("metadatas") or []
    return {
        str(projection_id): {
            "content": str(documents[index]) if index < len(documents) else "",
            "metadata": dict(metadatas[index]) if index < len(metadatas) and metadatas[index] else {},
        }
        for index, projection_id in enumerate(ids)
    }
