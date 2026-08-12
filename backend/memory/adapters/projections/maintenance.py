"""Canonical maintenance operations for the physical Chroma projection."""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .chroma_client import ChromaProjectionClient, _get_collection

_LEGACY_FACT_ID_RE = re.compile(r"^(?P<message_id>\d+)_(?P<index>\d+)$")


async def migrate_legacy_fact_ids(*, dry_run: bool = False) -> dict[str, int]:
    def read() -> dict[str, Any]:
        return _get_collection().get(include=["documents", "metadatas"])
    rows = await asyncio.to_thread(read)
    ids, documents, metadatas = rows.get("ids") or [], rows.get("documents") or [], rows.get("metadatas") or []
    all_ids = {str(item) for item in ids}
    moves, delete_ids = [], []
    stats = {"scanned": len(ids), "legacy": 0, "migrated": 0, "deduplicated": 0, "missing_scope": 0}
    for index, old_id in enumerate(ids):
        match = _LEGACY_FACT_ID_RE.fullmatch(str(old_id))
        if not match:
            continue
        stats["legacy"] += 1
        metadata = dict(metadatas[index] or {}) if index < len(metadatas) else {}
        if metadata.get("group_id") is None or metadata.get("bot_id") is None:
            stats["missing_scope"] += 1
            continue
        new_id = f"fact_{int(metadata['bot_id'])}_{int(metadata['group_id'])}_{match.group('message_id')}_{match.group('index')}"
        delete_ids.append(str(old_id))
        if new_id in all_ids:
            stats["deduplicated"] += 1
            continue
        moves.append((new_id, documents[index] if index < len(documents) else "", metadata))
    stats["migrated"] = len(moves)
    if dry_run:
        return stats
    for offset in range(0, len(moves), 500):
        batch = moves[offset:offset + 500]
        await asyncio.to_thread(lambda batch=batch: _get_collection().upsert(
            ids=[item[0] for item in batch], documents=[item[1] for item in batch], metadatas=[item[2] for item in batch]
        ))
    for offset in range(0, len(delete_ids), 500):
        await asyncio.to_thread(_get_collection().delete, ids=delete_ids[offset:offset + 500])
    return stats


def _parse_created_at(value: str) -> float | None:
    text = (value or "").strip().replace("T", " ")
    for cut in ("Z", "+", "."):
        if cut in text:
            text = text.split(cut)[0].strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


async def backfill_scored_by_model(*, dry_run: bool = False, label: str = "legacy/unknown") -> dict[str, int]:
    def read() -> dict[str, Any]:
        return _get_collection().get(include=["metadatas"])
    rows = await asyncio.to_thread(read)
    ids = rows.get("ids") or []
    metas = rows.get("metadatas") or []
    updates = [(str(ids[i]), dict(metas[i] or {})) for i in range(len(ids)) if i >= len(metas) or not (metas[i] or {}).get("scored_by_model")]
    if not dry_run and updates:
        await asyncio.to_thread(
            lambda: _get_collection().update(
                ids=[item[0] for item in updates],
                metadatas=[{**item[1], "scored_by_model": label} for item in updates],
            )
        )
    return {"scanned": len(ids), "need": len(updates), "updated": 0 if dry_run else len(updates)}


async def backfill_timestamps(*, dry_run: bool = False) -> dict[str, int]:
    """Backfill vector timestamps from canonical group message databases."""
    from contextlib import nullcontext
    from db import get_db
    from db import bind_db
    from runtime.dbpaths import group_db_path

    def read() -> dict[str, Any]:
        return _get_collection().get(include=["metadatas"])
    rows = await asyncio.to_thread(read)
    ids = rows.get("ids") or []
    metas = rows.get("metadatas") or []
    pending = []
    for index, item_id in enumerate(ids):
        metadata = dict(metas[index] or {}) if index < len(metas) else {}
        if metadata.get("timestamp") is None:
            pending.append((str(item_id), metadata))
    stats = {"scanned": len(ids), "need": len(pending), "updated": 0, "no_group": 0, "skipped_no_msg": 0}
    by_group: dict[int, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for item_id, metadata in pending:
        if metadata.get("group_id") is None:
            stats["no_group"] += 1
            continue
        by_group[int(metadata["group_id"])].append((item_id, metadata))
    updates: list[tuple[str, dict[str, Any]]] = []
    for group_id, entries in by_group.items():
        message_ids = []
        for item_id, _ in entries:
            try:
                message_ids.append(int(str(item_id).split("_")[0]))
            except ValueError:
                continue
        if not message_ids:
            continue
        with bind_db(group_db_path(group_id)):
            async with get_db() as db:
                placeholders = ",".join("?" for _ in set(message_ids))
                async with db.execute(f"SELECT id,created_at FROM messages WHERE id IN ({placeholders})", tuple(sorted(set(message_ids)))) as cur:
                    created = {int(row[0]): _parse_created_at(str(row[1])) for row in await cur.fetchall()}
        for item_id, metadata in entries:
            try:
                timestamp = created.get(int(item_id.split("_")[0]))
            except ValueError:
                timestamp = None
            if timestamp is not None:
                updates.append((item_id, {**metadata, "timestamp": timestamp}))
            else:
                stats["skipped_no_msg"] += 1
    if not dry_run and updates:
        await asyncio.to_thread(
            lambda: _get_collection().update(
                ids=[item[0] for item in updates], metadatas=[item[1] for item in updates]
            )
        )
        stats["updated"] = len(updates)
    return stats
