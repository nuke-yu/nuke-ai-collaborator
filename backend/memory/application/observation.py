"""Canonical loading boundary for durable bot-turn observations."""
from __future__ import annotations

import json
import hashlib
import importlib
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from memory.contracts import (
    ExtractedFactObservation,
    IngestBotReflections,
    IngestBotFactObservations,
    MemoryOperationError,
    SynthesizedReflection,
)
from memory.domain import MemoryScope
from memory.infrastructure import SQLiteMemoryDatabase


def _default_fact_engine() -> Any:
    """Load the configured extraction adapter only at the composition edge.

    Application code depends on the extraction protocol (``extract_and_reconcile``),
    while the default concrete algorithm remains replaceable by the factory.
    Tests and deployments may inject another implementation directly.
    """
    module = importlib.import_module("memory.adapters.algorithms")
    return module.Mem0FactEngine()


@dataclass(frozen=True, slots=True)
class CanonicalObservationEvent:
    bot_id: int
    group_id: int
    role: str
    bot_name: str
    message_id: int
    text: str
    provider: str
    model: str
    thread_id: str | None = None
    enabled: bool = True


class CanonicalObservationLoader:
    """Load an observation from canonical group storage and central metadata."""

    def __init__(self, database: SQLiteMemoryDatabase | None = None) -> None:
        self._database = database or SQLiteMemoryDatabase()

    async def load(self, group_id: int, input_id: str) -> CanonicalObservationEvent | None:
        message_id, bot_id = _parse_input(input_id)
        async with await self._database.connect("messages", group_id, write=False) as conn:
            async with conn.execute(
                """SELECT content,sender_name,sender_provider,sender_model,meta,is_deleted
                   FROM messages WHERE id=? AND group_id=? AND member_id=?""",
                (message_id, group_id, bot_id),
            ) as cur:
                row = await cur.fetchone()
        if not row or row[5]:
            return None

        import db
        async with db.global_db() as central:
            bot = await db.get_member(central, bot_id)
        if bot is None:
            return None
        if int(bot.get("group_id") or 0) != group_id:
            raise MemoryOperationError(
                f"observation bot {bot_id} is outside group {group_id}"
            )
        config = bot.get("executor_config") or {}
        policy = str(config.get("memory", "chroma")).lower()
        if policy in {"off", "none", "null", "disabled", "false"}:
            enabled = False
        else:
            enabled = True
        try:
            metadata = json.loads(row[4] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        observation_meta = metadata.get("memory_observation") or {}
        return CanonicalObservationEvent(
            bot_id=bot_id,
            group_id=group_id,
            role=str(bot.get("role") or ""),
            bot_name=str(bot.get("name") or row[1] or ""),
            message_id=message_id,
            text=str(row[0]),
            provider=str(row[2] or bot.get("model_provider") or ""),
            model=str(row[3] or bot.get("model_name") or ""),
            thread_id=observation_meta.get("thread_id") or None,
            enabled=enabled,
        )


class CanonicalBotFactObserver:
    """Extract facts and commit them through the canonical fact application service."""

    def __init__(
        self,
        database: SQLiteMemoryDatabase,
        fact_service: Any,
        ai_call_fn: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._database = database
        self._fact_service = fact_service
        self._ai_call_fn = ai_call_fn

    async def observe(self, event: CanonicalObservationEvent) -> tuple[str, ...]:
        async with await self._database.connect("memory_records", event.group_id, write=False) as db:
            async with db.execute(
                """SELECT record_id,content,evidence_json FROM memory_records
                   WHERE group_id=? AND bot_id=? AND kind='fact'
                     AND status IN ('active','provisional')""",
                (event.group_id, event.bot_id),
            ) as cur:
                rows = await cur.fetchall()
        existing = []
        projection_by_record: dict[str, str] = {}
        for record_id, content, evidence_json in rows:
            existing.append({"record_id": str(record_id), "content": str(content)})
            try:
                evidence = json.loads(evidence_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                evidence = {}
            projection_id = str(evidence.get("legacy_projection_id") or "")
            if projection_id:
                projection_by_record[str(record_id)] = projection_id

        engine = _default_fact_engine()
        ai_call_fn = self._ai_call_fn
        if ai_call_fn is not None:
            model_call = ai_call_fn
            async def _bound_ai_call(system, messages, **kwargs):
                return await model_call(
                    system,
                    messages,
                    provider=event.provider or kwargs.get("provider", "deepseek"),
                    model=event.model or kwargs.get("model", "deepseek-chat"),
                    temperature=kwargs.get("temperature", 0.1),
                )
            ai_call_fn = _bound_ai_call
        actions = await engine.extract_and_reconcile(
            event.text,
            existing,
            ai_call_fn=ai_call_fn,
        )
        observations: list[ExtractedFactObservation] = []
        conflict_ids: list[str] = []
        replacements: list[tuple[str, str]] = []
        for index, action in enumerate(actions):
            if str(action.action_type) in {"NOOP", "DELETE"}:
                if action.target_record_id:
                    projection_id = projection_by_record.get(str(action.target_record_id))
                    if projection_id:
                        conflict_ids.append(projection_id)
                continue
            projection_id = f"fact_{event.bot_id}_{event.group_id}_{event.message_id}_{index}"
            if action.target_record_id:
                old_projection_id = projection_by_record.get(str(action.target_record_id))
                if old_projection_id:
                    conflict_ids.append(old_projection_id)
                    replacements.append((old_projection_id, projection_id))
            observations.append(
                ExtractedFactObservation(
                    content=action.content,
                    importance=max(0.0, min(1.0, float(action.confidence))),
                    projection_id=projection_id,
                    algorithm_action=str(action.action_type),
                )
            )
        if not observations:
            return ()
        scope = MemoryScope.bot(
            group_id=event.group_id,
            bot_id=event.bot_id,
            actor_id=f"bot:{event.bot_id}",
        )
        command = IngestBotFactObservations(
            scope=scope,
            source_id=str(event.message_id),
            facts=tuple(observations),
            role=event.role,
            provider=event.provider,
            model=event.model,
            thread_id=event.thread_id or "",
            legacy_conflict_ids=tuple(dict.fromkeys(conflict_ids)),
            legacy_conflict_replacements=tuple(replacements),
        )
        return await self._fact_service.ingest(command)


class CanonicalSummaryObserver:
    """Persist bounded role summaries as canonical records with a watermark."""

    def __init__(
        self,
        database: SQLiteMemoryDatabase,
        ai_call_fn: Callable[..., Awaitable[Any]],
        *,
        threshold: int = 5,
    ) -> None:
        self._database = database
        self._ai_call_fn = ai_call_fn
        self._threshold = max(1, threshold)

    async def observe(self, event: CanonicalObservationEvent) -> dict[str, Any]:
        thread_id = event.thread_id or ""
        last_id = 0
        async with await self._database.connect("memory_records", event.group_id, write=False) as db:
            async with db.execute(
                """SELECT metadata_json FROM memory_records
                   WHERE group_id=? AND bot_id=? AND kind='summary' AND status='active'
                   ORDER BY updated_at DESC LIMIT 1""",
                (event.group_id, event.bot_id),
            ) as cur:
                row = await cur.fetchone()
        if row:
            try:
                last_id = int(json.loads(row[0] or "{}").get("covered_through_id") or 0)
            except (TypeError, ValueError, json.JSONDecodeError):
                last_id = 0

        async with await self._database.connect("messages", event.group_id, write=False) as db:
            async with db.execute(
                """SELECT id,content FROM messages
                   WHERE group_id=? AND member_id=? AND id>? AND is_deleted=0
                   ORDER BY id LIMIT ?""",
                (event.group_id, event.bot_id, last_id, self._threshold),
            ) as cur:
                messages = await cur.fetchall()
        if len(messages) < self._threshold:
            return {"stage": "summary", "skipped": True, "reason": "threshold"}

        batch = messages[: self._threshold]
        prompt = "\n".join(f"[{message_id}] {content}" for message_id, content in batch)
        response = await self._ai_call_fn(
            "你是会话摘要助手，请用中文将以下内容提炼为5个以内的核心要点，每点一行。",
            [{"role": "user", "content": prompt}],
            provider=event.provider or "deepseek",
            model=event.model or "deepseek-chat",
            temperature=0.2,
        )
        summary = str(response.get("content", "") if isinstance(response, dict) else response).strip()
        if not summary:
            return {"stage": "summary", "skipped": True, "reason": "empty_model_result"}
        now = int(time.time() * 1000)
        metadata = {
            "schema_version": "canonical-summary-v1",
            "role": event.role,
            "thread_id": thread_id,
            "covered_through_id": int(batch[-1][0]),
            "source_type": "conversation_summary",
        }
        record_id = "summary:" + hashlib.sha256(
            f"{event.group_id}:{event.bot_id}:{thread_id}:{batch[-1][0]}".encode()
        ).hexdigest()[:24]
        async with await self._database.connect("memory_records", event.group_id, write=True) as db:
            await db.execute(
                """INSERT INTO memory_records
                   (record_id,kind,group_id,bot_id,status,content,confidence,importance,
                    source_ids,metadata_json,algorithm_version,owner_type,authority,
                    sensitivity,evidence_json,created_by,effective_from,created_at,updated_at)
                   VALUES (?, 'summary', ?, ?, 'active', ?, 0.7, 0.6, ?, ?,
                           'canonical-summary-v1','bot','bot_observation','group','{}',?,?,?,?)
                   ON CONFLICT(record_id) DO UPDATE SET content=excluded.content,
                     metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (record_id, event.group_id, event.bot_id, summary,
                 json.dumps([str(item[0]) for item in batch]),
                 json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                 event.message_id, event.message_id, now, now),
            )
            await db.commit()
        return {"stage": "summary", "skipped": False, "record_id": record_id}


class CanonicalReflectionObserver:
    """Consolidate canonical facts per thread with durable watermarks."""

    def __init__(
        self,
        database: SQLiteMemoryDatabase,
        reflection_service: Any,
        ai_call_fn: Callable[..., Awaitable[Any]],
        *,
        min_facts: int = 5,
        importance_threshold: float = 3.0,
        max_insights: int = 5,
        max_backlog: int = 50,
        max_level: int = 2,
    ) -> None:
        self._database = database
        self._reflection_service = reflection_service
        self._ai_call_fn = ai_call_fn
        self._min_facts = max(1, min_facts)
        self._importance_threshold = max(0.0, importance_threshold)
        self._max_insights = max(1, max_insights)
        self._max_backlog = max(self._min_facts, max_backlog)
        self._max_level = max(1, max_level)

    async def observe(self, event: CanonicalObservationEvent) -> dict[str, Any]:
        watermarks = await self._watermarks(event)
        async with await self._database.connect("memory_records", event.group_id, write=False) as db:
            async with db.execute(
                """SELECT record_id,content,importance,effective_from,metadata_json,
                          evidence_json,kind FROM memory_records
                   WHERE group_id=? AND bot_id=? AND status IN ('active','provisional')
                     AND kind IN ('fact','reflection')
                   ORDER BY effective_from,record_id""",
                (event.group_id, event.bot_id),
            ) as cur:
                rows = await cur.fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record_id, content, importance, effective_from, metadata_json, evidence_json, kind in rows:
            try:
                metadata = json.loads(metadata_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            thread_id = str(metadata.get("thread_id") or "")
            if not thread_id:
                continue
            timestamp = float(effective_from or 0) / 1000.0
            if timestamp <= watermarks.get(thread_id, 0.0):
                continue
            level = int(metadata.get("level") or 0)
            if kind == "reflection" and level >= self._max_level:
                continue
            try:
                evidence = json.loads(evidence_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                evidence = {}
            grouped.setdefault(thread_id, []).append({
                "record_id": str(record_id), "content": str(content),
                "importance": float(importance or 0.5), "timestamp": timestamp,
                "level": level,
                "projection_id": str(evidence.get("legacy_projection_id") or record_id),
            })

        reflected = 0
        for thread_id, members in grouped.items():
            members.sort(key=lambda item: (item["timestamp"], item["record_id"]))
            max_timestamp = max(item["timestamp"] for item in members)
            if len(members) > self._max_backlog and (
                len(members) < self._min_facts
                or sum(item["importance"] for item in members) < self._importance_threshold
            ):
                await self._set_watermark(event, thread_id, max_timestamp)
                continue
            if len(members) < self._min_facts or sum(item["importance"] for item in members) < self._importance_threshold:
                continue
            prompt = "\n".join(f"- {item['content']}" for item in members)
            response = await self._ai_call_fn(
                "你是记忆反思助手，请从以下事实中提炼高层洞察，每行一条并以 | 分隔重要性分数。",
                [{"role": "user", "content": prompt}],
                provider=event.provider or "deepseek",
                model=event.model or "deepseek-chat",
                temperature=0.3,
            )
            text = str(response.get("content", "") if isinstance(response, dict) else response).strip()
            insights: list[tuple[str, float]] = []
            if text and "NO_INSIGHT" not in text:
                for line in text.splitlines()[: self._max_insights]:
                    line = line.strip().lstrip("-").strip()
                    if not line:
                        continue
                    content, separator, raw_score = line.rpartition("|")
                    if not separator:
                        content, score = line, 0.7
                    else:
                        try:
                            score = max(0.0, min(1.0, float(raw_score.strip())))
                        except ValueError:
                            content, score = line, 0.7
                    if content.strip():
                        insights.append((content.strip()[:500], score))
            if insights:
                level = min(max(item["level"] for item in members) + 1, self._max_level)
                projections = tuple(
                    SynthesizedReflection(
                        content=content,
                        importance=score,
                        projection_id=f"refl_{event.bot_id}_{event.group_id}_{int(max_timestamp * 1000)}_{index}",
                        source_projection_ids=tuple(item["projection_id"] for item in members),
                        level=level,
                        observed_at=max(0, int((max_timestamp + (index + 1) * 0.001) * 1000)),
                    )
                    for index, (content, score) in enumerate(insights)
                )
                await self._reflection_service.ingest(IngestBotReflections(
                    scope=MemoryScope.bot(group_id=event.group_id, bot_id=event.bot_id, actor_id=f"bot:{event.bot_id}"),
                    reflections=projections, role=event.role, provider=event.provider,
                    model=event.model, thread_id=thread_id,
                ))
                reflected += len(insights)
            await self._set_watermark(event, thread_id, max_timestamp)
        return {"stage": "reflection", "skipped": reflected == 0, "insights": reflected}

    async def _watermarks(self, event: CanonicalObservationEvent) -> dict[str, float]:
        result: dict[str, float] = {}
        async with await self._database.connect("memory_records", event.group_id, write=False) as db:
            async with db.execute(
                "SELECT metadata_json FROM memory_records WHERE group_id=? AND bot_id=? AND kind='reflection_watermark' AND status='active'",
                (event.group_id, event.bot_id),
            ) as cur:
                rows = await cur.fetchall()
        for (raw,) in rows:
            try:
                item = json.loads(raw or "{}")
                result[str(item["thread_id"])] = float(item["covered_through_ts"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return result

    async def _set_watermark(self, event: CanonicalObservationEvent, thread_id: str, timestamp: float) -> None:
        now = int(time.time() * 1000)
        record_id = "reflection-watermark:" + hashlib.sha256(
            f"{event.group_id}:{event.bot_id}:{thread_id}".encode()
        ).hexdigest()[:24]
        metadata = {"thread_id": thread_id, "covered_through_ts": timestamp}
        async with await self._database.connect("memory_records", event.group_id, write=True) as db:
            await db.execute(
                """INSERT INTO memory_records
                   (record_id,kind,group_id,bot_id,status,content,confidence,importance,
                    metadata_json,algorithm_version,owner_type,authority,sensitivity,
                    evidence_json,created_by,effective_from,created_at,updated_at)
                   VALUES (?, 'reflection_watermark', ?, ?, 'active', '', 1.0, 0.0, ?,
                           'canonical-reflection-watermark-v1','bot','bot_observation','group','{}',?,?,?,?)
                   ON CONFLICT(record_id) DO UPDATE SET metadata_json=excluded.metadata_json,
                     effective_from=excluded.effective_from,updated_at=excluded.updated_at""",
                (record_id, event.group_id, event.bot_id,
                 json.dumps(metadata, ensure_ascii=False, sort_keys=True), event.bot_id,
                 int(timestamp * 1000), now, now),
            )
            await db.commit()


class CanonicalToolCompressionObserver:
    """Fold durable tool events into canonical episode records."""

    def __init__(
        self,
        database: SQLiteMemoryDatabase,
        ai_call_fn: Callable[..., Awaitable[Any]],
        *,
        threshold: int = 10,
        max_batch: int = 50,
        max_insights: int = 5,
    ) -> None:
        self._database = database
        self._ai_call_fn = ai_call_fn
        self._threshold = max(1, threshold)
        self._max_batch = max(self._threshold, max_batch)
        self._max_insights = max(1, max_insights)

    async def observe(self, event: CanonicalObservationEvent) -> dict[str, Any]:
        async with await self._database.connect("tool_events", event.group_id, write=False) as db:
            async with db.execute(
                """SELECT id,ts,tool,args_summary,result_summary,is_error,
                          files_touched,command FROM tool_events
                   WHERE group_id=? AND bot_id=? AND compressed=0
                   ORDER BY id LIMIT ?""",
                (event.group_id, event.bot_id, self._max_batch),
            ) as cur:
                rows = await cur.fetchall()
        if len(rows) < self._threshold:
            return {"stage": "tool_compression", "skipped": True, "reason": "threshold"}
        body = "\n".join(
            f"[{row[0]}] tool={row[2]} args={row[3]} result={row[4]} error={row[5]} files={row[6]} command={row[7] or ''}"
            for row in rows
        )
        response = await self._ai_call_fn(
            "你是工具执行记忆助手，请从以下工具事件中提炼持久结论，每行一条并以 | 分隔重要性分数。",
            [{"role": "user", "content": body}],
            provider=event.provider or "deepseek",
            model=event.model or "deepseek-chat",
            temperature=0.3,
        )
        text = str(response.get("content", "") if isinstance(response, dict) else response).strip()
        insights: list[tuple[str, float]] = []
        if text and "NO_INSIGHT" not in text:
            for line in text.splitlines()[: self._max_insights]:
                line = line.strip().lstrip("-").strip()
                if not line:
                    continue
                content, separator, raw_score = line.rpartition("|")
                if not separator:
                    content, score = line, 0.7
                else:
                    try:
                        score = max(0.0, min(1.0, float(raw_score.strip())))
                    except ValueError:
                        content, score = line, 0.7
                if content.strip():
                    insights.append((content.strip()[:500], score))

        now = int(time.time() * 1000)
        max_ts = max(int(row[1]) for row in rows)
        async with await self._database.connect("memory_records", event.group_id, write=True) as db:
            for index, (content, score) in enumerate(insights):
                record_id = "tool-episode:" + hashlib.sha256(
                    f"{event.group_id}:{event.bot_id}:{max_ts}:{index}".encode()
                ).hexdigest()[:24]
                metadata = {
                    "schema_version": "canonical-tool-episode-v1",
                    "thread_id": event.thread_id or "",
                    "source_type": "tool_events",
                    "source_event_ids": [int(row[0]) for row in rows],
                    "scored_by_model": f"{event.provider}/{event.model}",
                }
                await db.execute(
                    """INSERT INTO memory_records
                       (record_id,kind,group_id,bot_id,status,content,confidence,importance,
                        source_ids,metadata_json,algorithm_version,owner_type,authority,
                        sensitivity,evidence_json,created_by,effective_from,created_at,updated_at)
                       VALUES (?, 'tool_episode', ?, ?, 'active', ?, ?, ?, ?, ?,
                               'canonical-tool-compression-v1','bot','bot_observation','group','{}',?,?,?,?)
                       ON CONFLICT(record_id) DO UPDATE SET content=excluded.content,
                         metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                    (record_id, event.group_id, event.bot_id, content, score, score,
                     json.dumps([str(row[0]) for row in rows]),
                     json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                     event.bot_id, max_ts, now, now),
                )
            ids = [int(row[0]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            await db.execute(
                f"UPDATE tool_events SET compressed=1 WHERE group_id=? AND id IN ({placeholders})",
                (event.group_id, *ids),
            )
            await db.commit()
        return {"stage": "tool_compression", "skipped": False, "compressed": len(rows), "insights": len(insights)}


def _parse_input(input_id: str) -> tuple[int, int]:
    try:
        message_raw, bot_raw = input_id.split(":", 1)
        message_id, bot_id = int(message_raw), int(bot_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid observe_turn input: {input_id}") from exc
    if message_id <= 0 or bot_id <= 0:
        raise ValueError(f"invalid observe_turn input: {input_id}")
    return message_id, bot_id
