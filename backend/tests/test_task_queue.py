from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import db
from api.admin_deps import require_operator
from online_tasks.task_queue import TaskQueueService
from online_tasks.story_input import build_story_input, repository_mapping_from_summary
from online_tasks.task_queue_store import TaskQueueStore
from online_tasks.service import TaskOrchestrator
from online_tasks.store import TaskStore
from plugins.agent_dashboard.task_queue_api import router_for


REPOSITORY = {
    "repo_url": "https://github.com/example/service",
    "base_branch": "release/one", "test_command": "pytest -q",
}
SITE = "https://team.atlassian.net"


def payload(number, summary="[service] Upgrade the library"):
    return build_story_input(f"PAY-{number}", summary, "Upgrade the library", "Run tests.",
                             github_owner="example")


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    path = str(tmp_path / "central.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db.writer, "DB_PATH", path)
    await db.init_central_db(path)
    yield TaskQueueStore()
    await db.aclose_writer(path)


@pytest.mark.asyncio
async def test_independent_story_queue_deduplicates_and_retries(store):
    run_ids = []
    for number in range(1, 81):
        item = payload(number)
        run_id = await store.submit_story(SITE, item["jira_id"], item["requirements"], item["mapping"], 42)
        assert await store.submit_story(SITE, item["jira_id"], item["requirements"], item["mapping"], 42) == run_id
        run_ids.append(run_id)

    first = await store.claim(SITE, 2)
    second = await store.claim(SITE, 2)
    assert first and second
    assert first["issue_key"] != second["issue_key"]
    assert await store.claim(SITE, 2) is None

    await store.finish(first, "failed", "Authorization: Bearer secret-production-token")
    failed = await store.read(first["run_id"])
    assert "secret-production-token" not in failed["error"]
    await store.retry(first["run_id"], first["issue_key"])
    new_attempt = await store.claim(SITE, 2)
    assert new_attempt["task_id"] == first["task_id"] and new_attempt["attempt"] == 2
    await store.finish(first, "failed", "old attempt should lose")
    assert (await store.read(first["run_id"]))["status"] == "preparing"
    assert len(run_ids) == 80


@pytest.mark.asyncio
async def test_story_task_blocks_missing_marker(store):
    item = payload(1, "Upgrade dependency")
    run_id = await store.submit_story(SITE, item["jira_id"], item["requirements"], item["mapping"], 42)
    blocked = await store.read(run_id)
    assert blocked["status"] == "blocked"
    assert "[service-name]" in blocked["error"]
    assert await store.claim(SITE, 2) is None
    corrected = payload(1)
    assert await store.submit_story(
        SITE, corrected["jira_id"], corrected["requirements"], corrected["mapping"], 42,
    ) == run_id
    assert (await store.read(run_id))["status"] == "queued"


def test_summary_service_marker_resolves_same_named_repository():
    assert repository_mapping_from_summary("[aaa-service] Upgrade Redis", "nuke-yu") == {
        "repo_url": "https://github.com/nuke-yu/aaa-service.git",
        "base_branch": "main", "test_command": "",
    }
    assert repository_mapping_from_summary("Upgrade [aaa-service]", "nuke-yu") is None


def test_dashboard_fields_are_the_only_agent_requirements_and_are_redacted():
    item = build_story_input(
        "PAY-1", "[service] Upgrade", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "Run tests.",
        github_owner="example",
    )
    assert item["jira_id"] == "PAY-1"
    assert "Summary:\n[service] Upgrade" in item["requirements"]
    assert "Description:" in item["requirements"] and "Acceptance criteria:" in item["requirements"]
    assert "abcdefghijklmnopqrstuvwxyz" not in item["requirements"]


@pytest.mark.asyncio
async def test_operator_api_submits_one_story_and_dispatches(store):
    tasks = MagicMock(create_task=AsyncMock(), discard_jira_staging_group=AsyncMock())
    task_store = MagicMock(get_task=AsyncMock(return_value=None))
    service = TaskQueueService(SITE, store, tasks, task_store, lambda: "w0", AsyncMock(), github_owner="example")
    app = FastAPI()
    app.dependency_overrides[require_operator] = lambda: {"uid": 42}
    app.include_router(router_for(service), prefix="/api/agent")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        body = {"jira_id": "PAY-2", "summary": "[service] Upgrade", "description": "Upgrade the library",
                "acceptance_criteria": "Run tests."}
        response = await client.post("/api/agent/story-tasks", json=body)
        assert response.status_code == 202
        assert (await client.post("/api/agent/story-tasks", json=body)).json() == response.json()
        assert (await client.post("/api/agent/epic-runs", json={"epic_key": "PAY-99"})).status_code == 404
        await service._prepare(await store.claim(SITE, 1), "w0")
        args = tasks.create_task.call_args.kwargs
        assert args["worker_id"] == "w0" and args["jira_task_id"].startswith("jira_")
        assert args["dispatch_attempt"] == 1
        assert args["repo_url"] == "https://github.com/example/service.git"
        assert args["base_branch"] == "main"
        response = await client.get(response.json()["url"])
        assert response.json()["issue_key"] == "PAY-2"


@pytest.mark.asyncio
async def test_jira_dispatch_uses_real_task_registry_and_scoped_bot(store, monkeypatch):
    import workspace

    async with db.write_connect(db.DB_PATH) as conn:
        await conn.execute("""INSERT INTO users(id, username, password_hash)
                           VALUES (42, 'operator', 'unused')""")
        await conn.commit()
    task_store = TaskStore()
    tasks = TaskOrchestrator(task_store=task_store)
    for method in ("_preflight_check_repo", "_clone_repo", "_dispatch_agent"):
        monkeypatch.setattr(tasks, method, AsyncMock())
    for method in ("init_group_workspace", "init_bot_workspace"):
        monkeypatch.setattr(workspace, method, AsyncMock())
    coordinator = TaskQueueService(SITE, store, tasks, task_store, lambda: "w0", AsyncMock())
    item = payload(1)
    run = await store.submit_story(SITE, item["jira_id"], item["requirements"], REPOSITORY, 42)
    job = await store.claim(SITE, 1)
    await coordinator._prepare(job, "w0")

    output = await store.read(run)
    assert output["task_id"] == job["task_id"]
    assert output["status"] == "dispatched"
    async with db.global_db() as conn:
        bots = await db.get_members(conn, output["group_id"])
        cfg = bots[0]["executor_config"]
        async with conn.execute("SELECT user_id FROM group_memberships WHERE group_id=?",
                                (output["group_id"],)) as rows:
            assert (await rows.fetchone())[0] == 42
    assert cfg["permission_mode"] == "default"
    assert cfg["jira_delivery"]["base_branch"] == "release/one"
    tasks._dispatch_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_jira_publish_checks_test_result_and_records_redacted_evidence(monkeypatch):
    from executors.plugins import rd_tools

    target = {**REPOSITORY, "task_id": "jira_127"}
    interaction = MagicMock(append_session_event=AsyncMock())
    runner = SimpleNamespace(
        bot={"executor_config": {"jira_delivery": target}},
        ctx=SimpleNamespace(active_ticket_id="jira_127", interaction=interaction),
    )
    run = AsyncMock(side_effect=[
        ("exit_code: 1\nstderr:\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz", False),
        ("exit_code: 0", False),
    ])
    monkeypatch.setattr(rd_tools.tool_executor, "execute", run)

    async def publish(_, title, description, *, target, verify):
        await verify("deadbeef")
        return {"no_change": True, "head_sha": "deadbeef"}

    monkeypatch.setattr(rd_tools, "get_git", lambda: SimpleNamespace(create_task_pr=publish))
    context = {"runner": runner, "group_id": 7, "session_id": "session"}
    with pytest.raises(RuntimeError, match="validation command failed"):
        await rd_tools._create_pr("Story", context=context)
    evidence = interaction.append_session_event.await_args.args[2]
    assert not evidence["passed"] and "abcdefghijklmnopqrstuvwxyz" not in evidence["output"]
    assert "无需创建 PR" in await rd_tools._create_pr("Story", context=context)
    assert interaction.append_session_event.await_args.args[2]["no_change"]


@pytest.mark.asyncio
async def test_jira_git_publishes_verified_sha_to_explicit_base_and_reuses_pr(monkeypatch):
    from integrations import github_client

    target = {**REPOSITORY, "task_id": "jira_127"}
    commands = []

    async def git(*args, **kwargs):
        commands.append(args)
        return {
            ("rev-parse", "--abbrev-ref", "HEAD"): "task_jira_127",
            ("remote", "get-url", "origin"): REPOSITORY["repo_url"],
            ("rev-parse", "HEAD"): "123456",
            ("rev-parse", "refs/heads/release/one^{tree}"): "base-tree",
            ("rev-parse", "HEAD^{tree}"): "head-tree",
        }.get(args, "")

    pr_url = "https://github.com/example/service/pull/1"
    gh = AsyncMock(return_value=pr_url)
    prs = AsyncMock(side_effect=[[], [{"headRefOid": "123456", "state": "OPEN", "url": pr_url}]])
    monkeypatch.setattr(github_client, "_git", git)
    monkeypatch.setattr(github_client, "commit_all", AsyncMock())
    monkeypatch.setattr(github_client, "_task_pull_requests", prs)
    monkeypatch.setattr(github_client, "_gh", gh)
    verify = AsyncMock()
    client = github_client.GitHubClient()
    for _ in range(2):
        result = await client.create_task_pr(7, "Story", "Details", target=target, verify=verify)
        assert result["url"] == pr_url and result["head_sha"] == "123456"
    assert commands.count(("push", REPOSITORY["repo_url"], "123456:refs/heads/task_jira_127")) == 1
    assert gh.await_count == 1
    assert gh.await_args.args[-2:] == ("--base", "release/one")


@pytest.mark.asyncio
async def test_retry_archives_delivery_and_rejects_late_pr_result(store):
    item = payload(1)
    run = await store.submit_story(SITE, item['jira_id'], item['requirements'], item['mapping'], 42)
    first = await store.claim(SITE, 2)
    await store.finish(first, 'linked')
    await store.record_pr(first['id'], 1, ('https://github.com/example/service/pull/1', 'needs_review'),
                          {'passed': True, 'head_sha': 'old'})
    await store.retry(run, item['jira_id'])
    second = await store.claim(SITE, 2)
    await store.finish(second, 'linked')
    await store.record_pr(first['id'], 1, (None, 'no_change'), {'head_sha': 'stale'})
    result = await store.read(run)
    assert result['delivery_status'] is None and result['validation'] is None
    assert result['attempts'][0]['pr_state'] == 'needs_review'
    assert result['attempts'][1]['pr_state'] is None


@pytest.mark.asyncio
async def test_created_recovery_waits_for_abort_ack_before_evidence_boundary(store, monkeypatch):
    item = payload(1)
    run = await store.submit_story(SITE, item['jira_id'], item['requirements'], item['mapping'], 42)
    job = await store.claim(SITE, 2)
    async with db.write_connect() as conn:
        await conn.execute("INSERT INTO groups(id, name) VALUES (901, 'recovery')")
        await conn.execute("INSERT INTO members(id, group_id, name, type, role) VALUES (902, 901, 'coder', 'bot', 'developer')")
        await conn.commit()
    task_store = TaskStore()
    await task_store.create_task(task_id=job['task_id'], group_id=901, bot_id=902,
                                repo_url=REPOSITORY['repo_url'], requirements='Upgrade')
    await store.finish(job, 'interrupted')
    await store.retry(run, item['jira_id'])
    retry = await store.claim(SITE, 2)
    tasks = TaskOrchestrator(task_store=task_store)
    order = []

    async def abort(*args, **kwargs):
        order.append('ack')

    async def floor(*args):
        order.append('boundary')

    async def dispatch(*args, **kwargs):
        order.append('dispatch')

    monkeypatch.setattr(tasks, '_send_abort', abort)
    monkeypatch.setattr(tasks, '_dispatch_agent', dispatch)
    monkeypatch.setattr(store, 'set_event_floor', floor)
    monkeypatch.setattr('online_tasks.task_queue.group_db_path', lambda _: '/nonexistent/group.db')
    service = TaskQueueService(SITE, store, tasks, task_store, lambda: 'w0', AsyncMock())
    await service._prepare(retry, 'w0')
    assert order == ['ack', 'boundary', 'dispatch']
    assert (await store.read(run))['status'] == 'running'


@pytest.mark.asyncio
async def test_created_recovery_abort_timeout_never_dispatches(store, monkeypatch):
    item = payload(1)
    run = await store.submit_story(SITE, item['jira_id'], item['requirements'], item['mapping'], 42)
    job = await store.claim(SITE, 2)
    async with db.write_connect() as conn:
        await conn.execute("INSERT INTO groups(id, name) VALUES (901, 'recovery')")
        await conn.execute("INSERT INTO members(id, group_id, name, type, role) VALUES (902, 901, 'coder', 'bot', 'developer')")
        await conn.commit()
    task_store = TaskStore()
    await task_store.create_task(task_id=job['task_id'], group_id=901, bot_id=902,
                                repo_url=REPOSITORY['repo_url'], requirements='Upgrade')
    await store.finish(job, 'interrupted')
    await store.retry(run, item['jira_id'])
    tasks = TaskOrchestrator(task_store=task_store)
    monkeypatch.setattr(tasks, '_send_abort', AsyncMock(side_effect=TimeoutError('ACK timeout')))
    dispatch = AsyncMock()
    monkeypatch.setattr(tasks, '_dispatch_agent', dispatch)
    service = TaskQueueService(SITE, store, tasks, task_store, lambda: 'w0', AsyncMock())
    await service._prepare(await store.claim(SITE, 2), 'w0')
    dispatch.assert_not_awaited()
    assert (await store.read(run))['status'] == 'interrupted'
    assert (await task_store.get_task(job['task_id']))['status'] == 'created'
    await store.retry(run, item['jira_id'])  # still recoverable after a failed ACK


@pytest.mark.asyncio
async def test_evidence_reads_only_events_after_attempt_boundary(store, tmp_path, monkeypatch):
    import json
    path = str(tmp_path / 'events.db')
    async with db.connect(path) as conn:
        await conn.execute('CREATE TABLE agent_sessions(id TEXT, group_id INTEGER, bot_id INTEGER)')
        await conn.execute('CREATE TABLE session_events(id INTEGER PRIMARY KEY, payload TEXT, session_id TEXT, event_type TEXT)')
        await conn.execute("INSERT INTO agent_sessions VALUES ('s1', 1, 2)")
        for index, sha in [(1, 'old'), (2, 'new')]:
            await conn.execute('INSERT INTO session_events VALUES (?, ?, ?, ?)',
                               (index, json.dumps({'task_id': 'jira_test', 'head_sha': sha}), 's1', 'jira_validation'))
        await conn.commit()
    monkeypatch.setattr('online_tasks.task_queue.group_db_path', lambda _: path)
    service = TaskQueueService(SITE, store, MagicMock(), MagicMock(), lambda: 'w0', AsyncMock())
    job = {'group_id': 1, 'bot_id': 2, 'task_id': 'jira_test', 'attempt': 2, 'event_floor': 1}
    assert (await service._evidence(job, 'jira_validation'))['head_sha'] == 'new'
    job['event_floor'] = 2
    assert await service._evidence(job, 'jira_validation') == {}
    job['event_floor'] = -1  # retry not yet acknowledged
    assert await service._evidence(job, 'jira_validation') == {}
    job['event_floor'] = None  # legacy retry without a boundary fails closed
    assert await service._evidence(job, 'jira_validation') == {}


@pytest.mark.parametrize('field,limit', [('summary', 500), ('description', 12000), ('acceptance_criteria', 8000)])
def test_story_rejects_overlong_fields_without_echoing_content(field, limit):
    fields = dict(jira_id='PAY-1', summary='[service] Upgrade', description='Upgrade', acceptance_criteria='Pass')
    fields[field] = 'x' * (limit + 1)
    with pytest.raises(ValueError, match=f'{field} must be at most {limit} characters'):
        build_story_input(**fields)


def test_maximum_story_preserves_description_and_acceptance_tail():
    fields = dict(jira_id='PAY-1', summary='[service] ' + 's' * 490,
                  description='d' * 11996 + '尾部需求', acceptance_criteria='a' * 7996 + '末项验收')
    item = build_story_input(**fields)
    for field in ('summary', 'description', 'acceptance_criteria'):
        assert fields[field] == item[field]
        assert fields[field] in item['requirements']
    assert item['requirements'].endswith('末项验收')


@pytest.mark.asyncio
async def test_api_rejects_overlong_story_without_queueing(store):
    service = TaskQueueService(SITE, store, MagicMock(), MagicMock(), lambda: 'w0', AsyncMock())
    app = FastAPI()
    app.dependency_overrides[require_operator] = lambda: {'uid': 42}
    app.include_router(router_for(service), prefix='/api/agent')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/agent/story-tasks', json={
            'jira_id': 'PAY-1', 'summary': '[service] Upgrade', 'description': 'd' * 12001,
            'acceptance_criteria': 'All requirements must be preserved',
        })
    assert response.status_code == 422
    assert response.json()['detail'] == 'description must be at most 12000 characters'
    assert await store.claim(SITE, 2) is None
