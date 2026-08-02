"""Durable bidirectional links between Session Events and learned evidence."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

import aiosqlite

import db as _db


EVIDENCE_KINDS = frozenset({"memory", "skill"})
EVIDENCE_RELATIONS = frozenset({"injected", "cited", "invoked"})


def evidence_kind(evidence_ref: str) -> str:
    return "skill" if evidence_ref.startswith("skill:") else "memory"


def normalize_evidence_links(raw_links: object) -> tuple[dict[str, Any], ...]:
    """Validate and deduplicate the compact link contract stored in event payloads."""
    if raw_links is None:
        return ()
    if not isinstance(raw_links, (list, tuple)):
        raise ValueError("evidence_links must be an array")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_links:
        if not isinstance(raw, Mapping):
            raise ValueError("evidence link must be an object")
        ref = str(raw.get("ref") or "").strip()
        kind = str(raw.get("kind") or evidence_kind(ref)).strip()
        relation = str(raw.get("relation") or "").strip()
        metadata = raw.get("metadata") or {}
        if not ref or any(ch.isspace() for ch in ref) or len(ref) > 512:
            raise ValueError("invalid evidence ref")
        if kind not in EVIDENCE_KINDS:
            raise ValueError(f"invalid evidence kind: {kind}")
        if relation not in EVIDENCE_RELATIONS:
            raise ValueError(f"invalid evidence relation: {relation}")
        if not isinstance(metadata, Mapping):
            raise ValueError("evidence link metadata must be an object")
        key = (kind, ref, relation)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "kind": kind,
            "ref": ref,
            "relation": relation,
            "metadata": dict(metadata),
        })
    return tuple(normalized)


async def insert_event_evidence_links(
    conn,
    *,
    session_event_id: int,
    session_id: str,
    links: Iterable[Mapping[str, Any]],
) -> None:
    values = normalize_evidence_links(list(links))
    if not values:
        return
    await conn.executemany(
        """INSERT OR IGNORE INTO session_evidence_links
           (session_event_id,session_id,evidence_kind,evidence_ref,relation,metadata_json)
           VALUES (?,?,?,?,?,?)""",
        [
            (
                session_event_id,
                session_id,
                link["kind"],
                link["ref"],
                link["relation"],
                json.dumps(link["metadata"], ensure_ascii=False, sort_keys=True),
            )
            for link in values
        ],
    )


async def get_event_evidence(session_event_id: int) -> list[dict[str, Any]]:
    """Forward traversal: one Session Event to all Memory/Skill evidence."""
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT evidence_kind,evidence_ref,relation,metadata_json,created_at
               FROM session_evidence_links WHERE session_event_id=? ORDER BY id""",
            (session_event_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "kind": row["evidence_kind"],
            "ref": row["evidence_ref"],
            "relation": row["relation"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


async def get_evidence_events(
    group_id: int, evidence_ref: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Reverse traversal: one Memory/Skill ref to its group-local Session Events."""
    ref = str(evidence_ref or "").strip()
    if not ref or any(ch.isspace() for ch in ref):
        raise ValueError("invalid evidence ref")
    bounded_limit = max(1, min(int(limit), 500))
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT se.id AS session_event_id,se.session_id,se.event_type,se.payload,
                      se.created_at,sel.evidence_kind,sel.relation,sel.metadata_json
                 FROM session_evidence_links sel
                 JOIN session_events se ON se.id=sel.session_event_id
                 JOIN agent_sessions s ON s.id=se.session_id
                WHERE s.group_id=? AND sel.evidence_ref=?
                ORDER BY se.id DESC LIMIT ?""",
            (group_id, ref, bounded_limit),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "session_event_id": row["session_event_id"],
            "session_id": row["session_id"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload"] or "{}"),
            "created_at": row["created_at"],
            "evidence": {
                "kind": row["evidence_kind"],
                "ref": ref,
                "relation": row["relation"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
            },
        }
        for row in rows
    ]
