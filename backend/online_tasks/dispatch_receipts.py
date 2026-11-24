"""Worker-side durable admission of online queue dispatches in the Group DB."""
import hashlib
import json

import db
from db.task_dispatch_schema import ONLINE_DISPATCH_RECEIPT_DDL
from core.workflow_store import _upsert_state
from online_tasks import CODING_WORKFLOW_ID


async def accept_dispatch(group_id, body, spec, workflow):
    """Commit receipt and initial snapshot before allowing execution side effects.

    Duplicate delivery never replays work. A crash after commit is recovered by
    the existing workflow snapshot recovery, even when apply() has not run yet.
    """
    task_id = body.get('task_id')
    attempt = body.get('dispatch_attempt')
    if not isinstance(task_id, str) or not task_id.startswith('jira_'):
        raise ValueError('Queue dispatch requires an assigned task')
    if type(attempt) is not int or attempt < 1:
        raise ValueError('Queue dispatch attempt must be a positive integer')
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    # Central data establishes the current task, owner Group and execution round.
    async with db.global_db() as conn:
        async with conn.execute('''SELECT j.attempt, t.group_id, t.bot_id FROM jira_story_jobs j
            JOIN agent_tasks t ON t.task_id=j.task_id WHERE j.task_id=?''', (task_id,)) as rows:
            current = await rows.fetchone()
    if not current or current[1] != group_id or current[2] != body.get('bot_id'):
        raise ValueError('Queue dispatch does not match the assigned task')
    if attempt != current[0]:
        return None
    async with db.write_connect() as conn:
        # Additive initialization also supports already-created Group databases.
        await conn.execute(ONLINE_DISPATCH_RECEIPT_DDL)
        await conn.execute('BEGIN IMMEDIATE')
        async with conn.execute('SELECT attempt, payload_hash FROM online_dispatch_receipts WHERE task_id=?',
                                (task_id,)) as rows:
            prior = await rows.fetchone()
        if prior and attempt <= prior[0]:
            if attempt == prior[0] and digest != prior[1]:
                raise ValueError('Dispatch payload differs from its persisted receipt')
            await conn.commit()
            return None
        step = workflow.start(group_id, spec, CODING_WORKFLOW_ID)
        try:
            # serialize() is the durable form used by runner.apply_step.
            from core.orchestration import registry
            snapshot = registry.get(CODING_WORKFLOW_ID).serialize(group_id)
            await _upsert_state(conn, group_id, CODING_WORKFLOW_ID, snapshot)
            await conn.execute('''INSERT INTO online_dispatch_receipts VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET attempt=excluded.attempt, payload_hash=excluded.payload_hash''',
                (task_id, group_id, attempt, digest))
            await conn.commit()
        except BaseException:
            workflow.end(group_id)
            raise
    return step
