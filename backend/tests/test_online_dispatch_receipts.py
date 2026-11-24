import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

import db
from core import workflow
from core.orchestration import registry
from online_tasks import CODING_WORKFLOW_ID
from online_tasks.dispatch_receipts import accept_dispatch
from online_tasks.store import TaskStore
from online_tasks.task_queue_store import TaskQueueStore


@pytest_asyncio.fixture
async def dispatch_context(tmp_path, monkeypatch):
    central = str(tmp_path / 'central.db')
    group = str(tmp_path / 'group.db')
    monkeypatch.setattr(db, 'DB_PATH', central)
    monkeypatch.setattr(db.writer, 'DB_PATH', central)
    await db.init_central_db(central)
    async with db.write_connect(central) as conn:
        await conn.execute("INSERT INTO groups(id, name) VALUES (1, 'online')")
        await conn.execute("INSERT INTO members(id, group_id, name, type, role) VALUES (2, 1, 'coder', 'bot', 'developer')")
        await conn.commit()
    queue = TaskQueueStore()
    await queue.submit_story('default', 'PAY-1', 'Upgrade', {'repo_url': 'https://github.com/example/service'}, 1)
    job = await queue.claim('default', 2)
    await TaskStore().create_task(job['task_id'], 1, 2, 'https://github.com/example/service', 'Upgrade')
    async with db.connect(group) as conn:
        await conn.execute('''CREATE TABLE workflow_state(group_id INTEGER PRIMARY KEY, orchestrator_id TEXT,
                              state_json TEXT, status TEXT, updated_at TEXT)''')
        await conn.commit()
    body = dict(orchestrator_id=CODING_WORKFLOW_ID, task_id=job['task_id'], bot_id=2,
                requirements='Upgrade', test_command='', dispatch_attempt=1)
    spec = registry.get(CODING_WORKFLOW_ID).parse_spec(body, {2: {'id': 2, 'name': 'coder'}})
    with db.bind_db(group):
        yield body, spec, group, central
    workflow.end(1)
    await db.aclose_writer(group)
    await db.aclose_writer(central)


@pytest.mark.asyncio
async def test_concurrent_duplicate_starts_only_once_and_survives_cleared_snapshot(dispatch_context):
    body, spec, group, _ = dispatch_context
    results = await asyncio.gather(*(accept_dispatch(1, body, spec, workflow) for _ in range(5)))
    assert sum(result is not None for result in results) == 1
    async with db.write_connect() as conn:
        await conn.execute('DELETE FROM workflow_state')
        await conn.commit()
    workflow.end(1)
    assert await accept_dispatch(1, body, spec, workflow) is None


@pytest.mark.asyncio
async def test_crash_after_receipt_has_recoverable_snapshot(dispatch_context):
    body, spec, _, _ = dispatch_context
    step = await accept_dispatch(1, body, spec, workflow)
    assert len(step.next_units) == 1
    # Simulate a crash before apply(): no work was launched but snapshot is durable.
    workflow.end(1)
    from core.workflow_store import load_all_active
    saved = (await load_all_active(1))[0]
    orch = registry.get(CODING_WORKFLOW_ID)
    orch.restore(1, saved['state'])
    assert len(orch.resume_units(1)) == 1
    assert saved['state']['dispatch_attempt'] == 1
    assert await accept_dispatch(1, body, spec, workflow) is None
    workflow.bind(1, CODING_WORKFLOW_ID)


@pytest.mark.asyncio
async def test_changed_payload_and_wrong_owner_are_rejected(dispatch_context):
    body, spec, _, _ = dispatch_context
    await accept_dispatch(1, body, spec, workflow)
    with pytest.raises(ValueError, match='payload differs'):
        await accept_dispatch(1, {**body, 'requirements': 'different'}, spec, workflow)
    with pytest.raises(ValueError, match='assigned task'):
        await accept_dispatch(9, body, spec, workflow)


@pytest.mark.asyncio
async def test_snapshot_failure_rolls_back_receipt_and_allows_redelivery(dispatch_context, monkeypatch):
    body, spec, _, _ = dispatch_context
    import online_tasks.dispatch_receipts as receipts
    original = receipts._upsert_state
    monkeypatch.setattr(receipts, '_upsert_state', AsyncMock(side_effect=RuntimeError('disk failure')))
    with pytest.raises(RuntimeError, match='disk failure'):
        await accept_dispatch(1, body, spec, workflow)
    async with db.connect() as conn:
        assert (await (await conn.execute('SELECT COUNT(*) FROM online_dispatch_receipts')).fetchone())[0] == 0
    monkeypatch.setattr(receipts, '_upsert_state', original)
    assert await accept_dispatch(1, body, spec, workflow) is not None


@pytest.mark.asyncio
async def test_old_dispatch_ignored_after_new_round(dispatch_context):
    body, spec, _, central = dispatch_context
    await accept_dispatch(1, body, spec, workflow)
    async with db.write_connect(central) as conn:
        await conn.execute('UPDATE jira_story_jobs SET attempt=2')
        await conn.commit()
    assert await accept_dispatch(1, body, spec, workflow) is None
    newer = {**body, 'dispatch_attempt': 2}
    spec = {**spec, 'dispatch_attempt': 2}
    assert await accept_dispatch(1, newer, spec, workflow) is not None
    assert await accept_dispatch(1, body, spec, workflow) is None


@pytest.mark.asyncio
async def test_old_snapshot_cannot_confirm_new_dispatch(dispatch_context, monkeypatch):
    from online_tasks.task_queue import TaskQueueService
    from unittest.mock import MagicMock
    body, spec, group, _ = dispatch_context
    await accept_dispatch(1, body, spec, workflow)
    monkeypatch.setattr('online_tasks.task_queue.group_db_path', lambda _: group)
    store = MagicMock(finish=AsyncMock())
    task_store = MagicMock(update_status=AsyncMock())
    service = TaskQueueService('default', store, MagicMock(), task_store, lambda: 'w0', AsyncMock())
    job = {'task_id': body['task_id'], 'attempt': 2}
    await service._reconcile(job, {'group_id': 1, 'status': 'created'})
    assert store.finish.await_args.args[1] == 'interrupted'
    task_store.update_status.assert_not_awaited()
    job['attempt'] = 1
    await service._reconcile(job, {'group_id': 1, 'status': 'created'})
    assert store.finish.await_args.args[1] == 'linked'


@pytest.mark.asyncio
@pytest.mark.parametrize('attempt', [0, -1, True, '1'])
async def test_invalid_attempt_never_starts_workflow(dispatch_context, attempt):
    body, spec, _, _ = dispatch_context
    with pytest.raises(ValueError, match='positive integer'):
        await accept_dispatch(1, {**body, 'dispatch_attempt': attempt}, spec, workflow)
