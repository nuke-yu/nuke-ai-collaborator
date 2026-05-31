"""CELL-06: one-time data splitter — legacy single chat.db → central.db + per-group DBs.

Run ONCE, with the app stopped, to migrate an existing single-file database into
the Project-Cell Isolation V3 layout (one central DB + one private DB per group).
Idempotent-ish: writes into freshly init'd target DBs, so re-running against the
same targets would duplicate rows — point at empty targets.

Row IDs are preserved (copied explicitly) so reply_to_id / pinned / reactions /
embeddings / session_events references stay valid inside each group DB.

CLI:  python -m db.split_tool <legacy_chat.db> <central.db> <group_root_dir>
"""
import asyncio
import os

import db as _db

# group tables that carry a direct group_id column
_DIRECT = [
    "messages", "agent_sessions", "role_summaries", "member_read",
    "workflow_state", "group_locks", "tickets", "pinned_messages",
]
# group tables partitioned indirectly (no group_id column of their own)
_INDIRECT = {
    "message_embeddings": "WHERE message_id IN (SELECT id FROM messages WHERE group_id=?)",
    "message_reactions":  "WHERE message_id IN (SELECT id FROM messages WHERE group_id=?)",
    "session_events":     "WHERE session_id IN (SELECT id FROM agent_sessions WHERE group_id=?)",
}
# central tables copied wholesale, in FK-safe order (parents before children)
_CENTRAL = ["groups", "members", "role_templates", "permission_rules", "cron_jobs"]


async def _table_cols(conn, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in await cur.fetchall()}


async def _copy(src, dst, table: str, where: str = "", params: tuple = ()) -> int:
    """Copy rows of `table` from src→dst, restricting to columns that exist in BOTH
    (robust to schema drift). Returns rows copied."""
    tgt_cols = await _table_cols(dst, table)
    cur = await src.execute(f"SELECT * FROM {table} {where}", params)
    src_cols = [d[0] for d in cur.description]
    rows = await cur.fetchall()
    if not rows:
        return 0
    cols = [c for c in src_cols if c in tgt_cols]
    idx = [src_cols.index(c) for c in cols]
    placeholders = ",".join("?" * len(cols))
    await dst.executemany(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
        [tuple(r[i] for i in idx) for r in rows],
    )
    return len(rows)


async def split_database(legacy_path: str, central_path: str, group_root: str) -> dict:
    """Split `legacy_path` into `central_path` + `<group_root>/group_{id}/chat.db`.
    Returns a small report dict for verification/logging."""
    report = {"central": {}, "groups": {}}

    # 1. Central DB
    await _db.init_central_db(central_path)
    async with _db.connect(legacy_path) as src, _db.write_connect(central_path) as dst:
        # init_central_db seeds default role_templates for fresh installs; when
        # splitting an EXISTING db we want legacy's templates verbatim (incl. any
        # custom ones), so drop the seed first to avoid id collisions on copy.
        await dst.execute("DELETE FROM role_templates")
        for t in _CENTRAL:
            report["central"][t] = await _copy(src, dst, t)
        await dst.commit()

    # 2. One private DB per group
    async with _db.connect(legacy_path) as src:
        cur = await src.execute("SELECT id FROM groups ORDER BY id")
        group_ids = [r[0] for r in await cur.fetchall()]

    for gid in group_ids:
        gpath = os.path.join(group_root, f"group_{gid}", "chat.db")
        os.makedirs(os.path.dirname(gpath), exist_ok=True)
        await _db.init_group_db(gpath)
        counts = {}
        async with _db.connect(legacy_path) as src, _db.write_connect(gpath) as dst:
            for t in _DIRECT:                       # messages/sessions first (FK parents)
                counts[t] = await _copy(src, dst, t, "WHERE group_id=?", (gid,))
            for t, where in _INDIRECT.items():
                counts[t] = await _copy(src, dst, t, where, (gid,))
            await dst.commit()
        report["groups"][gid] = counts

    await _db.aclose_writer()  # release the per-target writer connections
    return report


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        print("usage: python -m db.split_tool <legacy_chat.db> <central.db> <group_root_dir>")
        sys.exit(2)
    rep = asyncio.run(split_database(sys.argv[1], sys.argv[2], sys.argv[3]))
    print("central:", rep["central"])
    for gid, c in rep["groups"].items():
        print(f"group_{gid}:", {k: v for k, v in c.items() if v})
