"""Supervisor-side submission and scheduling for online coding tasks."""
import asyncio
import json
import logging
from pathlib import Path

import db
from online_tasks import CODING_WORKFLOW_ID
from online_tasks.story_input import build_story_input
from online_tasks.task_queue_store import safe_error
from runtime.dbpaths import group_db_path

log = logging.getLogger(__name__)


class TaskQueueService:
    def __init__(self, site, store, tasks, task_store, pick_worker, get_pr, *, github_owner="nuke-yu", max_parallel=2):
        self.site = site
        self.github_owner = github_owner
        self.store = store
        self._tasks = tasks
        self._task_store = task_store
        self._pick_worker = pick_worker
        self._get_pr = get_pr
        self._max_parallel = max_parallel
        self._wake = asyncio.Event()

    def wake(self):
        self._wake.set()

    async def submit_story(self, jira_id, summary, description, acceptance_criteria, operator_id):
        story = build_story_input(
            jira_id, summary, description, acceptance_criteria, github_owner=self.github_owner,
        )
        return await self.store.submit_story(
            self.site, story["jira_id"], story["requirements"], story["mapping"], operator_id,
        )

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
                    """SELECT 1 FROM workflow_state WHERE group_id=? AND orchestrator_id=?
                       AND json_extract(state_json, '$.task_id')=?
                       AND COALESCE(json_extract(state_json, '$.dispatch_attempt'), 1)=?""",
                    (record["group_id"], CODING_WORKFLOW_ID, job["task_id"], job["attempt"]),
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
                    async def before_dispatch():
                        # Abort ACK has stopped the previous execution. Freeze its
                        # event boundary before the new Worker can publish evidence.
                        path = group_db_path(record["group_id"])
                        floor = 0
                        if Path(path).exists():
                            async with db.connect(path) as conn:
                                async with conn.execute("SELECT COALESCE(MAX(id), 0) FROM session_events") as cur:
                                    floor = (await cur.fetchone())[0]
                        await self.store.set_event_floor(job, floor)
                    await self._tasks.retry_task(
                        job["task_id"], jira_dispatch=True,
                        recover_created=record["status"] == "created", before_dispatch=before_dispatch,
                        dispatch_attempt=job["attempt"],
                    )
                    await self.store.finish(job, "linked")
                else:
                    await self._reconcile(job, record)
                return
            await self._tasks.discard_jira_staging_group(job["task_id"])
            await self._tasks.create_task(
                **json.loads(job["mapping"]), requirements=job["requirements"],
                worker_id=worker, jira_task_id=job["task_id"], jira_operator_id=int(job["operator_id"]),
                dispatch_attempt=job["attempt"],
            )
            await self.store.finish(job, "linked")
        except Exception as exc:
            record = await self._task_store.get_task(job["task_id"])
            if job["state"] == "retry":
                await self.store.finish(job, "interrupted", str(exc))
            elif record and record["status"] == "created":
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
            log.warning("Task %s PR lookup failed: %s", job["task_id"], safe_error(str(exc)))
            result = None
        await self.store.record_pr(job["id"], job["attempt"], result, validation)

    async def _evidence(self, job, event_type):
        if (job.get("event_floor") is None and job["attempt"] > 1) or (
                job.get("event_floor") is not None and job["event_floor"] < 0):
            return {}  # Legacy retries have no reliable event boundary.
        path = group_db_path(job["group_id"])
        if not Path(path).exists():
            return {}
        async with db.connect(path) as conn:
            async with conn.execute(
                """SELECT e.payload, e.session_id FROM session_events e JOIN agent_sessions s ON e.session_id=s.id
                   WHERE s.group_id=? AND s.bot_id=? AND e.event_type=?
                     AND json_extract(e.payload, '$.task_id')=?
                     AND e.id>?
                   ORDER BY e.id DESC LIMIT 1""", (job["group_id"], job["bot_id"], event_type,
                                               job["task_id"], job.get("event_floor") or 0),
            ) as rows:
                row = await rows.fetchone()
        return {**json.loads(row[0]), "session_id": row[1]} if row else {}

    async def run(self):
        operations = set()
        review = None
        try:
            for job in await self.store.interrupted(self.site):
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
                            log.exception("Task queue operation failed")
                if review is None or review.done():
                    prs = await self.store.pending_prs(self.site)
                    if prs:
                        async def refresh_all(jobs):
                            await asyncio.gather(*(self._refresh_pr(job) for job in jobs))
                        review = asyncio.create_task(refresh_all(prs))
                        operations.add(review)
                for _ in range(self._max_parallel):
                    worker = self._pick_worker()
                    if not worker:
                        break
                    job = await self.store.claim(self.site, self._max_parallel)
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
