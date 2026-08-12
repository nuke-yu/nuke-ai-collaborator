"""Canonical conversation Memory use case.

This service is the replacement for ``ai.memory_provider``.  It stores and
recalls bounded canonical records and deliberately has no dependency on the
legacy AI package, Chroma, or legacy runtime adapters.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from memory.contracts import (
    ForgetMemory,
    MemoryAuthorizationError,
    ObserveMemory,
    RecallMemory,
    RecallResult,
    MemoryHit,
)
from memory.domain import MemoryScope, ScopeKind
from memory.infrastructure import safe_memory_mapping, safe_memory_text
from memory.ports import MemoryDatabasePort, MemoryQueryPort, MemoryCommandPort


class CanonicalConversationMemoryService(MemoryCommandPort, MemoryQueryPort):
    """Group-isolated lexical conversation Memory over canonical SQLite."""

    def __init__(self, database: MemoryDatabasePort) -> None:
        self._database = database

    async def observe(self, command: ObserveMemory) -> None:
        group_id, bot_id = _require_bot_scope(command.scope)
        content = safe_memory_text(command.content)
        metadata_json = safe_memory_mapping(command.metadata)
        record_id = _conversation_record_id(
            group_id, bot_id, command.source_id, content
        )
        now = int(time.time() * 1000)
        async with await self._database.connect(
            "memory_records", group_id, write=True
        ) as db:
            await db.execute(
                """INSERT INTO memory_records
                (record_id,kind,group_id,bot_id,status,content,confidence,
                 importance,source_ids,metadata_json,algorithm_version,
                 owner_type,authority,subject_key,sensitivity,evidence_json,
                 created_by,effective_from,created_at,updated_at)
                VALUES (?, 'conversation', ?, ?, 'active', ?, 0.5, 0.5,
                        ?, ?, 'conversation-v1', ?, ?, '', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                  content=excluded.content,
                  metadata_json=excluded.metadata_json,
                  updated_at=excluded.updated_at""",
                (
                    record_id,
                    group_id,
                    bot_id,
                    content,
                    json.dumps([command.source_id], ensure_ascii=False),
                    metadata_json,
                    "bot",
                    "observation",
                    "group",
                    "{}",
                    command.scope.actor_id,
                    now,
                    now,
                    now,
                ),
            )
            await db.commit()

    async def recall(self, query: RecallMemory) -> RecallResult:
        group_id, bot_id = _require_bot_scope(query.scope)
        terms = _terms(query.query)
        async with await self._database.connect(
            "memory_records", group_id, write=False
        ) as db:
            async with db.execute(
                """SELECT record_id,kind,content,confidence,importance,
                          metadata_json,created_at,updated_at
                   FROM memory_records
                   WHERE group_id=? AND bot_id=?
                     AND kind IN ('conversation','summary','tool_episode')
                     AND status='active'
                   ORDER BY updated_at DESC LIMIT 1000""",
                (group_id, bot_id),
            ) as cursor:
                rows = await cursor.fetchall()

        now = time.time() * 1000
        ranked: list[tuple[float, tuple[Any, ...]]] = []
        for row in rows:
            content_terms = _terms(str(row[2]))
            overlap = len(terms & content_terms)
            if terms and overlap == 0:
                continue
            age_days = max(0.0, (now - float(row[7])) / 86_400_000)
            recency = max(0.0, 1.0 - age_days / 30.0)
            score = (overlap / max(1, len(terms))) * 0.65 + float(row[3]) * 0.15 + recency * 0.2
            ranked.append((score, row))
        ranked.sort(key=lambda item: (item[0], item[1][7]), reverse=True)

        char_budget = query.token_budget * 4 if query.token_budget else 6000
        hits: list[MemoryHit] = []
        rendered: list[str] = []
        used = 0
        for score, row in ranked[: query.limit]:
            content = safe_memory_text(row[2], limit=2000)
            block = f'<conversation_memory record_id="{row[0]}">\n{content}\n</conversation_memory>'
            if used + len(block) > char_budget:
                break
            used += len(block)
            rendered.append(block)
            try:
                metadata = json.loads(row[5] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            hits.append(
                MemoryHit(
                    record_id=str(row[0]),
                    kind=str(row[1]),
                    content=content,
                    score=score,
                    provenance={
                        "group_id": group_id,
                        "bot_id": bot_id,
                        "source_type": metadata.get("source_type", "conversation"),
                    },
                )
            )
        return RecallResult(
            hits=tuple(hits),
            rendered_context="[Canonical Conversation Memory]\n" + "\n".join(rendered) if rendered else "",
            algorithm_trace=(
                {
                    "algorithm_id": "nuke.canonical.conversation.lexical",
                    "version": "v1",
                    "candidate_count": len(rows),
                },
            ),
        )

    async def forget(self, command: ForgetMemory) -> None:
        group_id, bot_id = _require_bot_scope(command.scope)
        async with await self._database.connect(
            "memory_records", group_id, write=True
        ) as db:
            if command.record_ids:
                placeholders = ",".join("?" for _ in command.record_ids)
                await db.execute(
                    f"DELETE FROM memory_records WHERE group_id=? AND bot_id=? "
                    f"AND kind='conversation' AND record_id IN ({placeholders})",
                    (group_id, bot_id, *command.record_ids),
                )
            else:
                await db.execute(
                    "DELETE FROM memory_records WHERE group_id=? AND bot_id=? AND kind='conversation'",
                    (group_id, bot_id),
                )
            await db.commit()


def _require_bot_scope(scope: MemoryScope) -> tuple[int, int]:
    if scope.kind is not ScopeKind.BOT or scope.group_id is None or scope.bot_id is None:
        raise MemoryAuthorizationError("canonical conversation memory requires bot scope")
    return scope.group_id, scope.bot_id


def _conversation_record_id(group_id: int, bot_id: int, source_id: str, content: str) -> str:
    raw = f"{group_id}:{bot_id}:{source_id}:{content}"
    return "conversation:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def _terms(value: str) -> set[str]:
    lowered = value.lower()
    terms = set(re.findall(r"[a-z0-9_.:/-]{2,}", lowered))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    terms.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return terms
