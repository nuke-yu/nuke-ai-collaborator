"""Migration 052: bounded cursor for observation gap repair."""

import time


async def apply(db) -> None:
    async with db.execute("PRAGMA table_info(pipeline_jobs)") as cur:
        columns = {row[1] for row in await cur.fetchall()}
    if {"group_id", "job_type", "input_id", "input_version"} <= columns:
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_input ON pipeline_jobs(group_id,job_type,input_id,input_version)")
    async with db.execute("PRAGMA table_info(messages)") as cur:
        columns = {row[1] for row in await cur.fetchall()}
    if {"id", "group_id", "member_id"} <= columns:
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_group_member_id ON messages(group_id,member_id,id)")
        await db.execute("""CREATE TABLE IF NOT EXISTS memory_observation_scan_state (
            group_id INTEGER PRIMARY KEY, scan_after_message_id INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0
        )""")
        await db.execute("""INSERT OR IGNORE INTO memory_observation_scan_state
            (group_id,scan_after_message_id,updated_at)
            SELECT group_id,MAX(id),? FROM messages GROUP BY group_id""", (int(time.time() * 1000),))
    await db.commit()
