import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

import db
from core import workflow
from core.orchestration.base import OrchestratorStep
from runtime.dispatch import dispatch_user_message


@pytest_asyncio.fixture
async def message_db(tmp_path, monkeypatch):
    central, group = str(tmp_path / 'central.db'), str(tmp_path / 'group.db')
    monkeypatch.setattr(db, 'DB_PATH', central)
    monkeypatch.setattr(db.writer, 'DB_PATH', central)
    await db.init_central_db(central)
    await db.init_group_db(group)
    async with db.write_connect(central) as conn:
        await conn.execute("INSERT INTO groups(id,name) VALUES(1,'team')")
        for member in (2, 3):
            await conn.execute("INSERT INTO members(id,group_id,name,type) VALUES(?,1,?,'human')", (member, str(member)))
        await conn.commit()
    with db.bind_db(group):
        yield group
    await db.aclose_writer(group)
    await db.aclose_writer(central)


@pytest.mark.asyncio
async def test_web_redelivery_is_persisted_and_dispatched_once(message_db, monkeypatch):
    orch = MagicMock(dispatch=AsyncMock(return_value=OrchestratorStep()))
    monkeypatch.setattr(workflow, '_orch_for', lambda _: orch)
    ack = AsyncMock()
    monkeypatch.setattr('runtime.dispatch.bus.broadcast', ack)
    monkeypatch.setattr('runtime.dispatch.bus.publish', AsyncMock())
    monkeypatch.setattr('core.orchestration.interaction.StandardInteraction.mark_read', AsyncMock())
    monkeypatch.setattr('core.recap.clear_recap', AsyncMock())
    frame = dict(group_id=1, member_id=2, content='hello', client_message_id=str(uuid.uuid4()))
    await dispatch_user_message(frame)
    await dispatch_user_message(frame)
    assert orch.dispatch.await_count == 1
    receipts = [call.args[1] for call in ack.await_args_list if call.args[1]['type'] == 'client_message_ack']
    assert len(receipts) == 2 and receipts[0]['id'] == receipts[1]['id']
    # A different authenticated member has an independent idempotency namespace.
    await dispatch_user_message({**frame, 'member_id': 3})
    assert orch.dispatch.await_count == 2
    async with db.connect() as conn:
        assert (await (await conn.execute('SELECT COUNT(*) FROM messages')).fetchone())[0] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('instance', ['old', None])
async def test_stale_or_missing_gate_instance_has_no_side_effects(monkeypatch, instance):
    orch = MagicMock()
    orch.get.return_value = {'awaiting_confirm': {'gate_id': '1-0', 'gate_instance_id': 'new'}}
    monkeypatch.setattr(workflow, '_orch_for', lambda _: orch)
    apply = AsyncMock()
    recap = AsyncMock()
    mark = AsyncMock()
    monkeypatch.setattr(workflow, 'apply_step', apply)
    monkeypatch.setattr(workflow, 'mark_gate_confirmed', mark)
    monkeypatch.setattr('core.recap.clear_recap', recap)
    assert not await workflow.confirm(1, '1-0', gate_instance_id=instance, require_instance=True)
    orch.confirm.assert_not_called()
    apply.assert_not_awaited()
    recap.assert_not_awaited()
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_gate_confirmation_advances_once(monkeypatch):
    orch = MagicMock()
    state = {'awaiting_confirm': {'gate_id': '1-0', 'gate_instance_id': 'new'}}
    orch.get.side_effect = lambda _: state
    def confirm(*args, **kwargs):
        state['awaiting_confirm'] = None
        return OrchestratorStep()
    orch.confirm.side_effect = confirm
    monkeypatch.setattr(workflow, '_orch_for', lambda _: orch)
    monkeypatch.setattr(workflow, 'apply_step', AsyncMock())
    mark = AsyncMock()
    monkeypatch.setattr(workflow, 'mark_gate_confirmed', mark)
    monkeypatch.setattr('core.recap.clear_recap', AsyncMock())
    await asyncio.gather(*(workflow.confirm(1, '1-0', gate_instance_id='new', require_instance=True) for _ in range(3)))
    orch.confirm.assert_called_once()
    mark.assert_awaited_once_with(1, '1-0', gate_instance_id='new')


@pytest.mark.asyncio
async def test_mark_updates_only_matching_gate_instance(message_db, monkeypatch):
    import json
    from core.runner import mark_gate_confirmed
    async with db.write_connect() as conn:
        for instance in ('old', 'new'):
            await db.save_message(conn, 1, 2, 'Confirm', meta={'kind': 'confirm_gate', 'gate_id': '1-0',
                                                           'gate_instance_id': instance, 'status': 'pending'})
    broadcast = AsyncMock()
    monkeypatch.setattr('core.runner.bus.broadcast', broadcast)
    await mark_gate_confirmed(1, '1-0', gate_instance_id='new')
    async with db.connect() as conn:
        rows = await (await conn.execute('SELECT meta FROM messages ORDER BY id')).fetchall()
    assert [json.loads(row[0])['status'] for row in rows] == ['pending', 'confirmed']
    assert broadcast.await_count == 1


@pytest.mark.asyncio
async def test_supervisor_forwards_gate_instance_and_authenticated_message_identity(monkeypatch):
    import main
    send = AsyncMock()
    monkeypatch.setattr(main.sup_mod, 'supervisor', MagicMock(send_to_worker=send))
    monkeypatch.setattr(main.manager, 'get_online_member_ids', lambda _: [])
    await main._handle_incoming_message({'type': 'confirm', 'gate_id': '1-0', 'gate_instance_id': 'new'}, 1, 2, 'trace')
    assert send.await_args.args[1]['gate_instance_id'] == 'new'
    client_id = str(uuid.uuid4())
    await main._handle_incoming_message({'content': 'hello', 'client_message_id': client_id, 'member_id': 99}, 1, 2, 'trace')
    frame = send.await_args.args[1]
    assert frame['client_message_id'] == client_id and frame['member_id'] == 2
