# sessions/store.py
import json
import aiosqlite
import db as _db
from ai.pricing import calculate_cost


async def create_session(
    session_id: str,
    bot_id: int,
    group_id: int,
    config: dict,
    user_message: str,
    parent_id: str | None = None,
    executor_id: str = "tool_loop_v1",
) -> str:
    async with _db.write_connect() as conn:
        await conn.execute(
            """INSERT INTO agent_sessions
               (id, parent_id, bot_id, group_id, executor_id, config_json, user_message)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, parent_id, bot_id, group_id, executor_id,
             json.dumps(config, ensure_ascii=False), user_message),
        )
        await conn.commit()
    return session_id


async def append_event(session_id: str, event_type: str, payload: dict) -> int:
    from observability import persist_artifact, prepare_payload
    from runtime.tracing import get_trace_id
    from sessions.evidence import insert_event_evidence_links, normalize_evidence_links

    evidence_links = normalize_evidence_links(payload.get("evidence_links"))
    prepared = prepare_payload(
        event_type, payload, trace_id=get_trace_id()
    )
    async with _db.write_connect() as conn:
        async with conn.execute(
            "SELECT group_id FROM agent_sessions WHERE id = ?", (session_id,)
        ) as cur:
            session_row = await cur.fetchone()
        if session_row is None:
            raise ValueError(f"Session not found: {session_id}")
        await persist_artifact(conn, int(session_row[0]), prepared.artifact)
        async with conn.execute(
            "INSERT INTO session_events (session_id, event_type, payload) VALUES (?, ?, ?)",
            (session_id, event_type, json.dumps(prepared.payload, ensure_ascii=False)),
        ) as cursor:
            session_event_id = int(cursor.lastrowid)
        if event_type.startswith("model_request_"):
            from sessions.model_usage import project_model_usage_event
            await project_model_usage_event(
                conn,
                session_event_id=session_event_id,
                session_id=session_id,
                event_type=event_type,
                payload=payload,
            )
        await insert_event_evidence_links(
            conn,
            session_event_id=session_event_id,
            session_id=session_id,
            links=evidence_links,
        )
        await conn.execute(
            "UPDATE agent_sessions SET updated_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
        await conn.commit()

    # Record telemetry after successful persistence in DB
    try:
        from observability import classify_event, get_otel_exporter, get_prometheus_metrics
        resolved = classify_event(event_type, prepared.payload)
        get_otel_exporter().record_event_policy(event_type, prepared.payload, resolved, trace_id=get_trace_id())
        duration_s = float(payload.get("duration_s") or payload.get("elapsed_s") or 0.0)
        status_str = "error" if "error" in payload or payload.get("is_error") else "success"
        get_prometheus_metrics().record_event_policy(event_type, resolved, duration_s=duration_s, status=status_str)
    except Exception:
        pass

    return session_event_id


async def get_session(session_id: str) -> dict | None:
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM agent_sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["config"] = json.loads(d.pop("config_json", "{}"))
    if d.get("last_snapshot_json"):
        try:
            d["last_snapshot"] = json.loads(d["last_snapshot_json"])
        except Exception:
            d["last_snapshot"] = []
    else:
        d["last_snapshot"] = []

    # Fetch bot details from central DB
    async with _db.global_db() as gconn:
        gconn.row_factory = aiosqlite.Row
        async with gconn.execute(
            "SELECT name, avatar_color FROM members WHERE id = ?",
            (d["bot_id"],)
        ) as gcur:
            bot_row = await gcur.fetchone()
    if bot_row:
        d["bot_name"] = bot_row["name"]
        d["bot_avatar_color"] = bot_row["avatar_color"]
    else:
        d["bot_name"] = "Unknown Bot"
        d["bot_avatar_color"] = "#6b7280"

    d["cost_usd"] = _session_cost(d)
    return d


def _session_cost(session: dict) -> float:
    """Estimate total USD cost for a session from its config model + token totals."""
    config = session.get("config") or {}
    return calculate_cost(
        config.get("provider", ""),
        config.get("model_name", ""),
        {
            "input_tokens": session.get("input_tokens") or 0,
            "output_tokens": session.get("output_tokens") or 0,
            "cache_read_tokens": session.get("cache_read_tokens") or 0,
            "cache_creation_tokens": session.get("cache_creation_tokens") or 0,
        },
    )


async def get_events(session_id: str, *, hydrate_artifacts: bool = False) -> list[dict]:
    from observability import hydrate_payload

    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT se.*, s.group_id AS artifact_group_id
               FROM session_events se JOIN agent_sessions s ON s.id = se.session_id
               WHERE se.session_id = ? ORDER BY se.id ASC""",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        event_ids = [int(row["id"]) for row in rows]
        links_by_event: dict[int, list[dict]] = {event_id: [] for event_id in event_ids}
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids)
            async with conn.execute(
                f"""SELECT session_event_id,evidence_kind,evidence_ref,relation,metadata_json
                      FROM session_evidence_links
                     WHERE session_event_id IN ({placeholders}) ORDER BY id""",
                event_ids,
            ) as link_cur:
                link_rows = await link_cur.fetchall()
            for link in link_rows:
                links_by_event[int(link[0])].append({
                    "kind": link[1],
                    "ref": link[2],
                    "relation": link[3],
                    "metadata": json.loads(link[4] or "{}"),
                })
        result = []
        for r in rows:
            d = dict(r)
            group_id = int(d.pop("artifact_group_id"))
            payload = json.loads(d.get("payload", "{}"))
            if hydrate_artifacts:
                payload = await hydrate_payload(conn, group_id, payload)
            d["payload"] = payload
            d["evidence_links"] = links_by_event.get(int(d["id"]), [])
            result.append(d)
    return result


async def update_session_status(session_id: str, status: str) -> None:
    from observability import prepare_payload
    from runtime.tracing import get_trace_id

    async with _db.write_connect() as conn:
        async with conn.execute(
            "SELECT status FROM agent_sessions WHERE id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
        previous_status = row[0] if row else None
        await conn.execute(
            "UPDATE agent_sessions SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, session_id),
        )
        if previous_status is not None and previous_status != status:
            event_payload = prepare_payload(
                "session_status",
                {"from_status": previous_status, "status": status},
                trace_id=get_trace_id(),
            ).payload
            await conn.execute(
                "INSERT INTO session_events (session_id, event_type, payload) VALUES (?, ?, ?)",
                (
                    session_id,
                    "session_status",
                    json.dumps(event_payload, ensure_ascii=False),
                ),
            )
        await conn.commit()


async def get_orphaned_sessions(group_id: int | None = None) -> list[dict]:
    """M-2: Return orphaned 'running' sessions using parameterized SQL."""
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        query = "SELECT * FROM agent_sessions WHERE status = 'running'"
        params = []
        if group_id:
            query += " AND group_id = ?"
            params.append(group_id)
        query += " ORDER BY created_at ASC"
        
        async with conn.execute(query, params) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["config"] = json.loads(d.pop("config_json", "{}"))
        result.append(d)
    return result


async def save_snapshot(session_id: str, messages: list) -> None:
    async with _db.write_connect() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET last_snapshot_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(messages, ensure_ascii=False), session_id),
        )
        await conn.commit()


async def add_tokens(
    session_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> None:
    async with _db.write_connect() as conn:
        await conn.execute(
            """UPDATE agent_sessions
               SET input_tokens          = input_tokens          + ?,
                   output_tokens         = output_tokens         + ?,
                   cache_read_tokens     = cache_read_tokens     + ?,
                   cache_creation_tokens = cache_creation_tokens + ?,
                   updated_at            = datetime('now')
               WHERE id = ?""",
            (input_tokens, output_tokens, cache_read_tokens,
             cache_creation_tokens, session_id),
        )
        await conn.commit()


async def get_group_sessions(group_id: int, limit: int = 50) -> list[dict]:
    # 1. Fetch bot members from central DB
    async with _db.global_db() as gconn:
        gconn.row_factory = aiosqlite.Row
        async with gconn.execute(
            "SELECT id, name, avatar_color FROM members WHERE group_id = ?",
            (group_id,)
        ) as gcur:
            members_rows = await gcur.fetchall()
    members_map = {row["id"]: dict(row) for row in members_rows}

    # 2. Fetch sessions from group DB
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT * FROM agent_sessions
               WHERE group_id = ?
               ORDER BY updated_at DESC, created_at DESC
               LIMIT ?""",
            (group_id, limit)
        ) as cur:
            rows = await cur.fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["config"] = json.loads(d.pop("config_json", "{}"))
        if d.get("last_snapshot_json"):
            try:
                d["last_snapshot"] = json.loads(d["last_snapshot_json"])
            except Exception:
                d["last_snapshot"] = []
        else:
            d["last_snapshot"] = []

        bot_info = members_map.get(d["bot_id"], {})
        d["bot_name"] = bot_info.get("name", "Unknown Bot")
        d["bot_avatar_color"] = bot_info.get("avatar_color", "#6b7280")

        d["cost_usd"] = _session_cost(d)
        result.append(d)
    return result
