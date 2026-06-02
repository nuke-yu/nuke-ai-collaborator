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


async def append_event(session_id: str, event_type: str, payload: dict) -> None:
    async with _db.write_connect() as conn:
        await conn.execute(
            "INSERT INTO session_events (session_id, event_type, payload) VALUES (?, ?, ?)",
            (session_id, event_type, json.dumps(payload, ensure_ascii=False)),
        )
        await conn.execute(
            "UPDATE agent_sessions SET updated_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
        await conn.commit()


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


async def get_events(session_id: str) -> list[dict]:
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM session_events WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.get("payload", "{}"))
        result.append(d)
    return result


async def update_session_status(session_id: str, status: str) -> None:
    async with _db.write_connect() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, session_id),
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
