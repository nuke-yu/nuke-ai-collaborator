import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import db
from api.admin_deps import require_operator
from online_tasks.jira_service import JiraTaskService
from online_tasks.jira_source import JiraCloudSource, JiraIssue, JiraSourceError
from online_tasks.jira_store import JiraTaskStore
from online_tasks.service import TaskOrchestrator
from online_tasks.store import TaskStore
from plugins.agent_dashboard.jira_api import router_for


REPOSITORY = {
    "repo_url": "https://github.com/example/service",
    "base_branch": "release/one", "test_command": "pytest -q",
}
SITE = "https://team.atlassian.net"


def story(number, done=False):
    return JiraIssue(str(number), f"PAY-{number}", "story", done, "Upgrade the library")


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    path = str(tmp_path / "central.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db.writer, "DB_PATH", path)
    await db.init_central_db(path)
    yield JiraTaskStore()
    await db.aclose_writer(path)


@pytest.mark.asyncio
async def test_80_story_batch_resume_and_single_story_dedupe(store):
    items = [story(n, done=n == 1) for n in range(1, 81)]
    mappings = {item.key: REPOSITORY for item in items if item.key != "PAY-3"}
    epic = await store.submit(SITE, "epic", "PAY-99", mappings, 2, 42)
    await store.save_page(epic, items[:50], "next")
    assert await store.claim(SITE, 2) is None
    assert (await store.next_discovery(SITE))["cursor"] == "next"
    await store.save_page(epic, items[50:], None)

    direct = await store.submit(SITE, "story", "PAY-2", {"PAY-2": REPOSITORY}, 1, 42)
    await store.save_page(direct, [items[1]], None)
    assert (await store.read(epic))["counts"] == {
        "skipped_completed": 1, "blocked": 1, "queued": 78,
    }
    claims = await asyncio.gather(*(store.claim(SITE, 2) for _ in range(5)))
    claims = [claim for claim in claims if claim]
    assert len(claims) == 2
    assert {claim["issue_key"] for claim in claims} == {"PAY-2", "PAY-4"}
    assert len({claim["task_id"] for claim in claims}) == 2
    assert (await store.read(direct))["stories"][0]["status"] == "preparing"

    current = next(claim for claim in claims if claim["issue_key"] == "PAY-2")
    await store.finish(current, "failed", "Authorization: Bearer secret-production-token")
    assert "secret-production-token" not in (await store.read(direct))["stories"][0]["error"]
    await store.retry(direct, "PAY-2")
    new_attempt = await store.claim(SITE, 2)
    assert new_attempt["task_id"] == current["task_id"] and new_attempt["attempt"] == 2
    await store.finish(current, "failed", "old attempt should lose")
    assert (await store.read(direct))["stories"][0]["status"] == "preparing"


@pytest.mark.asyncio
async def test_capacity_pause_and_missing_mapping_can_be_completed(store):
    epic = await store.submit(SITE, "epic", "PAY-99", {"PAY-1": REPOSITORY}, 1, 42)
    await store.save_page(epic, [story(1), story(2)], None)
    assert await store.submit(SITE, "epic", "PAY-99", {"PAY-2": REPOSITORY}, 1, 42) == epic
    await store.set_paused(epic, True)
    assert await store.claim(SITE, 5) is None
    await store.set_paused(epic, False)
    claim = await store.claim(SITE, 5)
    assert claim["issue_key"] == "PAY-1"
    assert await store.claim(SITE, 5) is None
    await store.finish(claim, "failed")
    assert (await store.claim(SITE, 5))["issue_key"] == "PAY-2"


@pytest.mark.asyncio
async def test_sync_discards_superseded_page_and_removed_story(store):
    epic = await store.submit(SITE, "epic", "PAY-99", {"PAY-1": REPOSITORY, "PAY-2": REPOSITORY}, 1, 42)
    await store.save_page(epic, [story(1), story(2)], None)
    await store.sync(epic)
    await store.save_page(epic, [story(3)], None, generation=1)
    assert await store.claim(SITE, 1) is None
    await store.save_page(epic, [story(2)], None, generation=2)
    assert (await store.read(epic))["counts"] == {"removed_from_epic": 1, "queued": 1}
    assert (await store.claim(SITE, 1))["issue_key"] == "PAY-2"


@pytest.mark.asyncio
async def test_conflicting_mapping_is_blocked_and_original_can_still_run(store):
    direct = await store.submit(SITE, "story", "PAY-1", {"PAY-1": REPOSITORY}, 1, 42)
    await store.save_page(direct, [story(1)], None)
    other = {**REPOSITORY, "repo_url": "https://github.com/example/another"}
    epic = await store.submit(SITE, "epic", "PAY-99", {"PAY-1": other}, 1, 42)
    await store.save_page(epic, [story(1)], None)
    assert (await store.read(epic))["counts"] == {"blocked": 1}
    assert (await store.claim(SITE, 1))["run_id"] == direct


@pytest.mark.asyncio
async def test_jira_cloud_uses_page_token_and_reads_adf():
    def handle(req):
        item = {
            "key": "PAY-1", "id": "1001",
            "fields": {
                "issuetype": {"name": "Story"}, "status": {"statusCategory": {"key": "indeterminate"}},
                "summary": "Upgrade", "description": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Run integration tests."}]},
                ]},
            },
        }
        if req.method == "GET":
            assert req.url.path == "/rest/api/3/issue/PAY-1"
            return httpx.Response(200, json=item)
        assert req.url.path == "/rest/api/3/search/jql"
        query = json.loads(req.content)
        assert query["jql"] == 'parent = "PAY-99" ORDER BY key ASC'
        if query.get("nextPageToken") == "more":
            return httpx.Response(200, json={"issues": [], "isLast": True})
        return httpx.Response(200, json={"issues": [item], "nextPageToken": "more", "isLast": False})

    source = JiraCloudSource(SITE, "mail@example.com", "unused", transport=httpx.MockTransport(handle))
    try:
        issue = await source.get("pay-1")
        assert "Run integration tests." in issue.requirements and not issue.done
        assert await source.children("PAY-99", None) == ([issue], "more")
        assert await source.children("PAY-99", "more") == ([], None)
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_operator_api_discovery_and_dispatch(store):
    source = MagicMock(site=SITE, story_type="story", epic_type="epic")
    source.get = AsyncMock(return_value=story(2))
    source.close = AsyncMock()
    tasks = MagicMock(create_task=AsyncMock(), discard_jira_staging_group=AsyncMock())
    task_store = MagicMock(get_task=AsyncMock(return_value=None))
    service = JiraTaskService(source, store, tasks, task_store, lambda: "w0", AsyncMock())
    app = FastAPI()
    app.dependency_overrides[require_operator] = lambda: {"uid": 42}
    app.include_router(router_for(service), prefix="/api/agent")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        body = {"story_key": "pay-2", "repository": REPOSITORY}
        response = await client.post("/api/agent/story-runs", json=body)
        assert response.status_code == 202
        assert (await client.post("/api/agent/story-runs", json=body)).json() == response.json()
        await service.discover(await store.next_discovery(SITE))
        await service._prepare(await store.claim(SITE, 1), "w0")
        args = tasks.create_task.call_args.kwargs
        assert args["worker_id"] == "w0" and args["jira_task_id"].startswith("jira_")
        assert args["base_branch"] == "release/one"
        response = await client.get(response.json()["url"])
        assert response.json()["stories"][0]["issue_key"] == "PAY-2"


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
    coordinator = JiraTaskService(MagicMock(), store, tasks, task_store, lambda: "w0", AsyncMock())
    run = await store.submit(SITE, "story", "PAY-1", {"PAY-1": REPOSITORY}, 1, 42)
    await store.save_page(run, [story(1)], None)
    job = await store.claim(SITE, 1)
    await coordinator._prepare(job, "w0")

    output = (await store.read(run))["stories"][0]
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
async def test_epic_pagination_recovers_with_saved_cursor(store):
    source = MagicMock(site=SITE, story_type="story", epic_type="epic")
    source.get = AsyncMock(return_value=JiraIssue("99", "PAY-99", "epic", False, ""))
    source.children = AsyncMock(side_effect=[
        ([story(1)], "page-2"),
        JiraSourceError("rate limited", retryable=True),
        ([story(2)], None),
    ])
    coordinator = JiraTaskService(source, store, MagicMock(), MagicMock(), lambda: "w0", AsyncMock())
    run_id = await store.submit(SITE, "epic", "PAY-99", {"PAY-1": REPOSITORY, "PAY-2": REPOSITORY}, 1, 42)
    await coordinator.discover(await store.next_discovery(SITE))
    await coordinator.discover(await store.next_discovery(SITE))
    assert (await store.read(run_id))["error"] == "rate limited"
    assert await store.claim(SITE, 1) is None
    await store.set_paused(run_id, False)
    await coordinator.discover(await store.next_discovery(SITE))
    assert source.children.await_args.args == ("PAY-99", "page-2")
    assert (await store.read(run_id))["counts"] == {"queued": 2}


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
