"""Repair legacy Chroma IDs and optionally rebuild group facts from messages.

Run from ``backend/`` with the application stopped:

    python3 -m scripts.rebuild_chroma_fact_ids --dry-run
    python3 -m scripts.rebuild_chroma_fact_ids
    python3 -m scripts.rebuild_chroma_fact_ids --rebuild --group-id 3

The default apply command only renames surviving legacy facts and uses no model
calls. ``--rebuild`` is the recovery path for facts lost to historic ID
collisions: it deletes fact/reflection vectors for the selected groups, preserves
tool episodes, and replays bot messages chronologically. It can consume model
tokens. Stop the app first so online writes cannot race the rebuild.
"""
import argparse
import asyncio
import os

import db
from ai.client import call_ai_once
from memory.adapters.projections.chroma_client import _get_collection
from memory.adapters.projections.maintenance import migrate_legacy_fact_ids
from memory.adapters.projections import ChromaBotMemoryProjectionDelivery
from memory.application import BotFactObservationService, CanonicalBotFactObserver, CanonicalObservationEvent
from memory.infrastructure import ProjectionOutbox, SQLiteMemoryDatabase
from db import bind_db
from runtime.dbpaths import group_db_path


def _read_collection_sync() -> dict:
    return _get_collection().get(include=["metadatas"])


def _delete_vectors_sync(ids: list[str]) -> None:
    collection = _get_collection()
    for offset in range(0, len(ids), 500):
        collection.delete(ids=ids[offset:offset + 500])


def _rebuild_vector_ids(rows: dict, selected_bots: set[tuple[int, int]]) -> list[str]:
    """Select only derived fact/reflection vectors owned by the target bots."""
    all_ids = (rows or {}).get("ids") or []
    all_metas = (rows or {}).get("metadatas") or []
    selected = []
    for pos, item_id in enumerate(all_ids):
        metadata = dict(all_metas[pos]) if pos < len(all_metas) and all_metas[pos] else {}
        group_id = metadata.get("group_id")
        bot_id = metadata.get("bot_id")
        mem_type = metadata.get("mem_type", "fact")
        if (
            group_id is not None and bot_id is not None
            and (int(group_id), int(bot_id)) in selected_bots
            and mem_type in ("fact", "reflection")
        ):
            selected.append(str(item_id))
    return selected


async def rebuild_group_facts(*, group_ids: set[int] | None, dry_run: bool) -> dict:
    collection_rows = await asyncio.to_thread(_read_collection_sync)
    async with db.global_db() as central:
        sql = (
            "SELECT g.id, m.id, COALESCE(m.role, ''), "
            "COALESCE(m.model_provider, 'deepseek'), COALESCE(m.model_name, 'deepseek-chat') "
            "FROM groups g JOIN members m ON m.group_id=g.id WHERE m.type='bot'"
        )
        params: list[int] = []
        if group_ids:
            sql += f" AND g.id IN ({','.join('?' for _ in group_ids)})"
            params.extend(sorted(group_ids))
        async with central.execute(sql, params) as cursor:
            bot_rows = await cursor.fetchall()

    bots_by_group: dict[int, dict[int, tuple[str, str, str]]] = {}
    for group_id, bot_id, role, provider, model in bot_rows:
        bots_by_group.setdefault(int(group_id), {})[int(bot_id)] = (role, provider, model)

    selected_groups = set(bots_by_group)
    selected_bots = {
        (group_id, bot_id)
        for group_id, bots in bots_by_group.items()
        for bot_id in bots
    }
    delete_ids = _rebuild_vector_ids(collection_rows, selected_bots)

    stats = {
        "groups": 0, "messages": 0, "processed": 0,
        "vectors_deleted": len(delete_ids), "skipped_db": 0,
    }
    group_messages: dict[int, list[tuple]] = {}
    for group_id, bots in bots_by_group.items():
        path = group_db_path(group_id)
        if not os.path.isfile(path):
            stats["skipped_db"] += 1
            continue
        stats["groups"] += 1
        with bind_db(path):
            async with db.get_db() as group_db:
                placeholders = ",".join("?" for _ in bots)
                async with group_db.execute(
                    "SELECT id, member_id, content, created_at, sender_provider, sender_model "
                    f"FROM messages WHERE is_deleted=0 AND member_id IN ({placeholders}) "
                    "ORDER BY created_at, id",
                    list(bots),
                ) as cursor:
                    group_messages[group_id] = await cursor.fetchall()
        stats["messages"] += len(group_messages[group_id])

    if dry_run:
        return stats

    await asyncio.to_thread(_delete_vectors_sync, delete_ids)
    for group_id in selected_groups:
        path = group_db_path(group_id)
        if not os.path.isfile(path):
            continue
        with bind_db(path):
            async with db.write_connect() as group_db:
                await group_db.execute("DELETE FROM reflection_state")
                await group_db.commit()

    for group_id, messages in group_messages.items():
        bots = bots_by_group[group_id]
        for message_id, bot_id, content, created_at, sender_provider, sender_model in messages:
            if not content or len(content.strip()) < 15:
                continue
            role, default_provider, default_model = bots[int(bot_id)]
            database = SQLiteMemoryDatabase()
            outbox = ProjectionOutbox(database, ChromaBotMemoryProjectionDelivery())
            observer = CanonicalBotFactObserver(
                database,
                BotFactObservationService(database, outbox),
                call_ai_once,
            )
            await observer.observe(CanonicalObservationEvent(
                bot_id=int(bot_id), group_id=group_id, role=role,
                bot_name=role, message_id=int(message_id), text=content,
                provider=sender_provider or default_provider,
                model=sender_model or default_model,
            ))
            await outbox.drain(group_id, limit=100)
            stats["processed"] += 1
    return stats


async def _run(args) -> int:
    renamed = await migrate_legacy_fact_ids(dry_run=args.dry_run)
    print(f"legacy ID migration: {renamed}")
    if args.rebuild:
        rebuilt = await rebuild_group_facts(
            group_ids=set(args.group_id) if args.group_id else None,
            dry_run=args.dry_run,
        )
        print(f"group fact rebuild: {rebuilt}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Repair group-scoped Chroma fact IDs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--group-id", type=int, action="append")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
