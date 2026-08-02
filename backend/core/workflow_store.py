"""
core/workflow_store.py — 工作流编排状态的持久化（崩溃恢复用）

编排器（DeclarativeOrchestrator）把进度放在内存 dict 里；本模块是它的耐久快照：
每个 group 一行，状态变化时整行覆写，工作流结束时删除。重启后 runner.resume_workflows
读 active 行还原编排器、重新派发在飞的工作单元。

与 sessions（tool_loop_v1 的 WAL）不同：编排器是纯状态机、无副作用，所以快照足矣，
不需要细粒度事件回放 + 幂等回滚。
"""
import json

import db as _db


async def _upsert_state(conn, group_id: int, orchestrator_id: str, state: dict) -> None:
    await conn.execute(
        """INSERT INTO workflow_state (group_id, orchestrator_id, state_json, status, updated_at)
           VALUES (?, ?, ?, 'active', datetime('now'))
           ON CONFLICT(group_id) DO UPDATE SET
               orchestrator_id = excluded.orchestrator_id,
               state_json      = excluded.state_json,
               status          = 'active',
               updated_at      = datetime('now')""",
        (group_id, orchestrator_id, json.dumps(state, ensure_ascii=False)),
    )


async def save_state(group_id: int, orchestrator_id: str, state: dict) -> None:
    """整行覆写某个 group 的编排快照（status 置 active）。"""
    # 写操作必须走串行化 writer（DFT-053），避免绕过单锁导致 database is locked。
    async with _db.write_connect() as conn:
        await _upsert_state(conn, group_id, orchestrator_id, state)
        await conn.commit()


async def clear_state(group_id: int) -> None:
    """工作流结束/被结束时删除快照。"""
    async with _db.write_connect() as conn:
        await conn.execute("DELETE FROM workflow_state WHERE group_id = ?", (group_id,))
        await conn.commit()


async def commit_transition(
    group_id: int,
    orchestrator_id: str,
    *,
    state: dict | None,
    observations: list[dict],
    clear: bool = False,
) -> list[dict]:
    """Atomically persist one state-machine transition and its observations."""
    from observability.workflow import insert_workflow_observations

    async with _db.write_connect() as conn:
        if clear:
            await conn.execute("DELETE FROM workflow_state WHERE group_id = ?", (group_id,))
        elif state is not None:
            await _upsert_state(conn, group_id, orchestrator_id, state)
        envelopes = await insert_workflow_observations(
            conn, group_id, orchestrator_id, observations
        )
        await conn.commit()
    return envelopes


async def load_all_active(group_id: int | None = None) -> list[dict]:
    """重启时拉取所有 active 工作流，state_json 解析进 'state' 键。"""
    import aiosqlite

    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM workflow_state WHERE status = 'active'" + (f" AND group_id = {group_id}" if group_id else "") + " ORDER BY group_id ASC"
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["state"] = json.loads(d.pop("state_json", "{}"))
        result.append(d)
    return result
