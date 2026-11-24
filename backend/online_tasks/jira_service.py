"""Supervisor-side discovery and scheduling for Jira-backed coding tasks."""
import asyncio
import json
import logging
from pathlib import Path

import db
from online_tasks import CODING_WORKFLOW_ID
from online_tasks.jira_source import JiraSourceError
from online_tasks.jira_store import safe_error
from runtime.dbpaths import group_db_path

log = logging.getLogger(__name__)


class JiraTaskService:
    def __init__(self, source, store, tasks, task_store, pick_worker, get_pr, *, max_parallel=2):
        self.source = source
        self.store = store
        self._tasks = tasks
        self._task_store = task_store
        self._pick_worker = pick_worker
        self._get_pr = get_pr
        self._max_parallel = max_parallel
        self._wake = asyncio.Event()

    def wake(self):
        self._wake.set()

    async def discover(self, run):
        try:
            if not run["cursor"]:
                issue = await self.source.get(run["issue_key"])
                expected = self.source.story_type if run["kind"] == "story" else self.source.epic_type
                if issue.kind != expected:
                    raise JiraSourceError(f"Expected Jira issue type {expected}")
                if run["kind"] == "story":
                    await self.store.save_page(run["id"], [issue], None, run["generation"])
                    return
                epic_key = issue.key
            else:
                epic_key = run["issue_key"]
            issues, cursor = await self.source.children(epic_key, run["cursor"])
            await self.store.save_page(run["id"], issues, cursor, run["generation"])
        except JiraSourceError as exc:
            await self.store.discovery_failed(run, str(exc), retryable=exc.retryable)

    async def _reconcile(self, job, record):
        if record["status"] != "created":
            await self.store.finish(job, "linked")
            return
        # Workflow state is written by the Worker before it launches WorkUnits.
        # A receipt can be lost after IPC; never blindly start that Group again.
        path = group_db_path(record["group_id"])
        if Path(path).exists():
            async with db.connect(path) as conn:
                async with conn.execute(
                    """SELECT 1 FROM workflow_state WHERE group_id=? AND orchestrator_id=?""",
                    (record["group_id"], CODING_WORKFLOW_ID),
                ) as cur:
                    if await cur.fetchone():
                        await self._task_store.update_status(job["task_id"], "dispatched")
                        await self.store.finish(job, "linked")
                        return
        await self.store.finish(job, "interrupted", "Dispatch unconfirmed. Inspect the task before retrying.")

    async def _prepare(self, job, worker):
        try:
            record = await self._task_store.get_task(job["task_id"])
            if record:
                if job["state"] == "retry":
                    await self._tasks.retry_task(job["task_id"], jira_dispatch=True)
                    await self.store.finish(job, "linked")
                else:
                    await self._reconcile(job, record)
                return
            await self._tasks.discard_jira_staging_group(job["task_id"])
            await self._tasks.create_task(
                **json.loads(job["mapping"]), requirements=job["requirements"],
                worker_id=worker, jira_task_id=job["task_id"], jira_operator_id=int(job["operator_id"]),
            )
            await self.store.finish(job, "linked")
        except Exception as exc:
            record = await self._task_store.get_task(job["task_id"])
            if record and record["status"] == "created":
                await self._reconcile(job, record)
            else:
                await self.store.finish(job, "linked" if record else "failed", str(exc))

    async def _refresh_pr(self, job):
        validation = None
        try:
            mapping = json.loads(job["mapping"])
            event = await self._evidence(job, "jira_delivery")
            test = await self._evidence(job, "jira_validation")
            if test:
                validation = {key: test[key] for key in ("session_id", "head_sha", "passed", "command")
                              if key in test}
            if event.get("no_change"):
                result = (None, "no_change")
            else:
                verified = event.get("head_sha") or (test.get("head_sha") if test.get("passed") else "")
                result = await self._get_pr(
                    mapping["repo_url"], mapping["base_branch"], f"task_{job['task_id']}",
                    verified,
                )
                if result and result[1] == "needs_review" and not verified:
                    result = (result[0], "pr_unverified")
        except Exception as exc:
            log.warning("Jira task %s PR lookup failed: %s", job["task_id"], safe_error(str(exc)))
            result = None
        await self.store.record_pr(job["id"], result, validation)

    async def _evidence(self, job, event_type):
        path = group_db_path(job["group_id"])
        if not Path(path).exists():
            return {}
        async with db.connect(path) as conn:
            async with conn.execute(
                """SELECT e.payload, e.session_id FROM session_events e JOIN agent_sessions s ON e.session_id=s.id
                   WHERE s.group_id=? AND s.bot_id=? AND e.event_type=?
                     AND json_extract(e.payload, '$.task_id')=?
                   ORDER BY e.id DESC LIMIT 1""", (job["group_id"], job["bot_id"], event_type, job["task_id"]),
            ) as rows:
                row = await rows.fetchone()
        return {**json.loads(row[0]), "session_id": row[1]} if row else {}

    async def run(self):
        operations = set()
        discovery = None
        review = None
        try:
            for job in await self.store.interrupted(self.source.site):
                record = await self._task_store.get_task(job["task_id"])
                if record:
                    await self._reconcile(job, record)
                else:
                    await self.store.finish(job, "queued")
            while True:
                self._wake.clear()
                for op in tuple(operations):
                    if op.done():
                        operations.remove(op)
                        try:
                            op.result()
                        except Exception:
                            log.exception("Jira queue operation failed")
                if discovery is None or discovery.done():
                    run = await self.store.next_discovery(self.source.site)
                    if run:
                        discovery = asyncio.create_task(self.discover(run))
                        operations.add(discovery)
                if review is None or review.done():
                    prs = await self.store.pending_prs(self.source.site)
                    if prs:
                        async def refresh_all(jobs):
                            await asyncio.gather(*(self._refresh_pr(job) for job in jobs))
                        review = asyncio.create_task(refresh_all(prs))
                        operations.add(review)
                for _ in range(self._max_parallel):
                    worker = self._pick_worker()
                    if not worker:
                        break
                    job = await self.store.claim(self.source.site, self._max_parallel)
                    if job is None:
                        break
                    operation = asyncio.create_task(self._prepare(job, worker))
                    operations.add(operation)
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
        finally:
            for op in operations:
                op.cancel()
            await asyncio.gather(*operations, return_exceptions=True)
            await self.source.close()
