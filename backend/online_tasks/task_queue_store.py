"""Durable single-Story requests, shared jobs, and atomic dispatch claims."""
import json
import time
import uuid

import db
from executors.redaction import redact_secrets

_LIVE = """(j.state = 'starting' OR (j.state = 'linked'
            AND t.status IN ('created','dispatched','running','paused','stuck','retrying','restarted')))"""
_STORY_VIEW = """SELECT j.id, j.issue_key, j.state, j.attempt, j.error, j.mapping,
    j.pr_url, j.pr_state, j.validation, m.jira_done, m.mapping_conflict, m.seen_generation,
    j.task_id, t.group_id,
    t.status AS task_status, t.error_message AS task_error
    FROM jira_run_stories m JOIN jira_story_jobs j ON j.id = m.job_id
    LEFT JOIN agent_tasks t ON j.task_id = t.task_id"""


class TaskQueueConflict(ValueError):
    pass


def safe_error(error: str) -> str:
    text, _ = redact_secrets(error)
    return text[:1000]


async def _one(conn, query, args=()):
    async with conn.execute(query, args) as rows:
        row = await rows.fetchone()
        return dict(zip((col[0] for col in rows.description), row)) if row else None


async def _many(conn, query, args=()):
    async with conn.execute(query, args) as rows:
        keys = [col[0] for col in rows.description]
        return [dict(zip(keys, row)) for row in await rows.fetchall()]


class TaskQueueStore:
    async def submit_story(self, site, key, requirements, mapping, operator_id):
        if mapping is not None and (not isinstance(mapping, dict) or not mapping.get("repo_url")):
            raise ValueError("Repository mapping must contain a GitHub repository URL")
        if not isinstance(requirements, str) or not requirements.strip():
            raise ValueError("Story requirements must not be empty")
        mappings = {key: mapping} if mapping else {}
        async with db.write_connect(db.DB_PATH) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            query = "SELECT * FROM jira_task_runs WHERE site=? AND kind='story' AND issue_key=?"
            prior = await _one(conn, query, (site, key))
            if prior:
                existing = await _one(conn, """SELECT id, state, attempt FROM jira_story_jobs
                    WHERE site=? AND issue_id=?""", (site, key))
                if existing:
                    if existing["state"] == "blocked" and existing["attempt"] == 0:
                        state = "queued" if mapping else "blocked"
                        error = None if mapping else (
                            "Story summary must start with [service-name] matching its GitHub repository"
                        )
                        await conn.execute(
                            """UPDATE jira_story_jobs SET requirements=?, mapping=?, state=?, error=? WHERE id=?""",
                            (requirements, json.dumps(mapping) if mapping else None, state, error, existing["id"]),
                        )
                        await conn.execute("UPDATE jira_task_runs SET mappings=? WHERE id=?",
                                           (json.dumps(mappings), prior["id"]))
                    await conn.commit()
                    return prior["id"]
                # Rehydrate legacy pending records from the Dashboard payload.
                run_id = prior["id"]
                await conn.execute("UPDATE jira_task_runs SET mappings=?, discovery='ready', error=NULL WHERE id=?",
                                   (json.dumps(mappings), run_id))
                await self._insert_story_job(conn, run_id, site, key, requirements, mapping)
                await conn.commit()
                return run_id
            run_id = uuid.uuid4().hex
            await conn.execute(
                """INSERT INTO jira_task_runs(id, site, kind, issue_key, mappings, max_parallel,
                   operator_id, discovery) VALUES (?, ?, 'story', ?, ?, 1, ?, 'ready')""",
                (run_id, site, key, json.dumps(mappings), str(operator_id)),
            )
            await self._insert_story_job(conn, run_id, site, key, requirements, mapping)
            await conn.commit()
            return run_id

    async def _insert_story_job(self, conn, run_id, site, key, requirements, mapping):
        state = "queued" if mapping else "blocked"
        error = None if mapping else "Story summary must start with [service-name] matching its GitHub repository"
        cursor = await conn.execute(
            """INSERT INTO jira_story_jobs(site, issue_id, issue_key, task_id, requirements, mapping, state, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (site, key, key, f"jira_{uuid.uuid4().hex}", requirements,
             json.dumps(mapping) if mapping else None, state, error),
        )
        await conn.execute(
            """INSERT INTO jira_run_stories(run_id, job_id, jira_done, mapping_conflict, seen_generation)
               VALUES (?, ?, 0, 0, 1)""", (run_id, cursor.lastrowid),
        )

    async def claim(self, site, limit):
        async with db.write_connect(db.DB_PATH) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            count = await _one(conn, f"""SELECT COUNT(*) AS n FROM jira_story_jobs j
                LEFT JOIN agent_tasks t ON j.task_id = t.task_id WHERE {_LIVE}""")
            if count["n"] >= limit:
                await conn.rollback()
                return None
            job = await _one(conn, f"""SELECT j.*, r.id AS run_id, r.operator_id FROM jira_story_jobs j
                JOIN jira_run_stories m ON m.job_id=j.id
                JOIN jira_task_runs r ON m.run_id=r.id
                WHERE j.site=? AND r.kind='story' AND j.state IN ('queued','retry')
                  AND m.jira_done=0 AND m.mapping_conflict=0
                  AND r.discovery='ready' AND m.seen_generation=r.generation
                  AND r.paused=0 AND r.max_parallel >
                  (SELECT COUNT(*) FROM jira_story_jobs j LEFT JOIN agent_tasks t ON j.task_id=t.task_id
                   WHERE j.claimed_run=r.id AND {_LIVE})
                ORDER BY j.id, r.rowid LIMIT 1""",
                             (site,))
            if job:
                await conn.execute(
                    """UPDATE jira_story_jobs SET state='starting', claimed_run=?, attempt=attempt+1,
                       error=NULL WHERE id=?""", (job["run_id"], job["id"]),
                )
                job["attempt"] += 1
                await conn.execute(
                    "INSERT INTO task_attempt_delivery(job_id, attempt, event_floor) VALUES (?, ?, ?)",
                    (job["id"], job["attempt"], 0 if job["attempt"] == 1 else -1),
                )
                await conn.execute(
                    "INSERT INTO jira_job_attempts(job_id, number, run_id, stage) VALUES (?, ?, ?, 'starting')",
                    (job["id"], job["attempt"], job["run_id"]),
                )
            await conn.commit()
            return job

    async def finish(self, job, state, error=None):
        async with db.write_connect(db.DB_PATH) as conn:
            message = safe_error(error) if error else None
            update = await conn.execute(
                """UPDATE jira_story_jobs SET state=?, error=? WHERE id=? AND state='starting' AND attempt=?""",
                (state, message, job["id"], job["attempt"]),
            )
            if update.rowcount:
                await conn.execute(
                    """UPDATE jira_job_attempts SET stage=?, error=?, dispatch_ended_at=CURRENT_TIMESTAMP
                       WHERE job_id=? AND number=?""", (state, message, job["id"], job["attempt"]),
                )
            await conn.commit()

    async def interrupted(self, site):
        async with db.global_db() as conn:
            return await _many(conn, """SELECT j.* FROM jira_story_jobs j
                WHERE j.site=? AND j.state='starting'
                AND j.claimed_run IN (SELECT id FROM jira_task_runs WHERE kind='story')""", (site,))

    async def retry(self, run_id, key):
        async with db.write_connect(db.DB_PATH) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            job = await _one(conn, _STORY_VIEW + " WHERE m.run_id=? AND j.issue_key=?", (run_id, key))
            if not job:
                raise KeyError(key)
            run = await _one(conn, "SELECT generation, discovery FROM jira_task_runs WHERE id=?", (run_id,))
            if run["discovery"] != "ready" or job["seen_generation"] != run["generation"]:
                raise TaskQueueConflict("Story task is not ready to retry")
            retryable = {"failed", "aborted", "stuck", "stuck_permanently", "incomplete"}
            if job["state"] == "interrupted":
                retryable.add("created")
            if job["jira_done"] or job["mapping_conflict"] or job["state"] not in {"linked", "failed", "interrupted"} or (
                    job["task_status"] is not None and job["task_status"] not in retryable):
                raise TaskQueueConflict("Story is not in a retryable state")
            await conn.execute("""INSERT OR IGNORE INTO task_attempt_delivery
                (job_id, attempt, pr_url, pr_state, validation) VALUES (?, ?, ?, ?, ?)""",
                (job["id"], job["attempt"], job["pr_url"], job["pr_state"], job["validation"]))
            await conn.execute("""UPDATE jira_story_jobs SET state='retry', error=NULL,
                               pr_url=NULL, pr_state=NULL, validation=NULL, pr_checked_at=0 WHERE id=?""",
                               (job["id"],))
            await conn.commit()

    async def read(self, run_id):
        async with db.global_db() as conn:
            run = await _one(conn,
                """SELECT id, issue_key, discovery, error
                   FROM jira_task_runs WHERE id=? AND kind='story'""",
                (run_id,))
            if run is None:
                return None
            job = await _one(conn, _STORY_VIEW + " WHERE m.run_id=? ORDER BY j.issue_key LIMIT 1", (run_id,))
            if job:
                attempts = await _many(conn,
                    """SELECT a.job_id, a.number, a.stage, a.started_at, a.dispatch_ended_at, a.error,
                       d.pr_url, d.pr_state, d.validation
                       FROM jira_job_attempts a JOIN jira_run_stories m ON m.job_id=a.job_id
                       LEFT JOIN task_attempt_delivery d ON d.job_id=a.job_id AND d.attempt=a.number
                       WHERE m.run_id=? ORDER BY a.number""", (run_id,))
        if not job:
            status = "failed" if run["discovery"] == "failed" else "loading"
            return {"id": run_id, "issue_key": run["issue_key"], "status": status,
                    "attempt": 0, "task_id": None, "group_id": None,
                    "attempts": [], "repository": None, "validation": None,
                    "pr_url": None, "error": safe_error(run["error"] or "") or None}

        error = job["error"] or safe_error(job["task_error"] or "")
        if job["jira_done"]:
            status = "skipped_completed"
        elif job["mapping_conflict"]:
            status, error = "blocked", "Repository mapping conflicts with the existing Story task"
        elif job["state"] in {"queued", "retry", "starting", "unmapped", "blocked", "failed", "interrupted"}:
            status = {"retry": "queued", "starting": "preparing", "unmapped": "blocked"}.get(job["state"], job["state"])
        else:
            status = job["task_status"] or "task_record_missing"
            if status == "completed":
                status = "awaiting_pr_confirmation"
        execution_status = status
        if status == "awaiting_pr_confirmation" and job["pr_state"]:
            status = job["pr_state"]
        return {
            "id": run_id, "issue_key": job["issue_key"], "status": status,
            "execution_status": execution_status, "delivery_status": job["pr_state"],
            "attempt": job["attempt"], "task_id": job["task_id"], "group_id": job["group_id"],
            "attempts": attempts, "repository": json.loads(job["mapping"])["repo_url"] if job["mapping"] else None,
            "validation": json.loads(job["validation"]) if job["validation"] else None,
            "pr_url": job["pr_url"], "error": error or None,
        }

    async def pending_prs(self, site, limit=4):
        async with db.global_db() as conn:
            return await _many(conn, """SELECT j.id, j.attempt, j.mapping, j.task_id, t.group_id, t.bot_id,
                d.event_floor
                FROM jira_story_jobs j JOIN agent_tasks t ON t.task_id=j.task_id
                LEFT JOIN task_attempt_delivery d ON d.job_id=j.id AND d.attempt=j.attempt
                WHERE j.site=? AND j.state='linked'
                AND (t.status IN ('completed','incomplete','failed','aborted','stuck_permanently')
                     OR t.pr_url IS NOT NULL)
                AND j.pr_checked_at<=? AND (j.pr_state IS NULL OR
                    j.pr_state IN ('needs_review','pr_head_changed','pr_unverified'))
                ORDER BY j.pr_checked_at LIMIT ?""", (site, time.time() - 60, limit))

    async def set_event_floor(self, job, event_floor):
        async with db.write_connect(db.DB_PATH) as conn:
            await conn.execute("""UPDATE task_attempt_delivery SET event_floor=?
                WHERE job_id=? AND attempt=?""", (event_floor, job["id"], job["attempt"]))
            await conn.commit()

    async def record_pr(self, job_id, attempt, result, validation):
        async with db.write_connect(db.DB_PATH) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            current = await _one(conn, "SELECT state, attempt FROM jira_story_jobs WHERE id=?", (job_id,))
            if not current or current["state"] != "linked" or current["attempt"] != attempt:
                await conn.rollback()
                return
            encoded = json.dumps(validation) if validation else None
            if result:
                await conn.execute("""UPDATE jira_story_jobs SET pr_checked_at=?, pr_url=?, pr_state=?,
                    validation=COALESCE(?, validation) WHERE id=?""", (time.time(), *result, encoded, job_id))
            else:
                await conn.execute("""UPDATE jira_story_jobs SET pr_checked_at=?,
                    validation=COALESCE(?, validation) WHERE id=?""", (time.time(), encoded, job_id))
            await conn.execute("""UPDATE task_attempt_delivery SET
                pr_url=(SELECT pr_url FROM jira_story_jobs WHERE id=?),
                pr_state=(SELECT pr_state FROM jira_story_jobs WHERE id=?),
                validation=(SELECT validation FROM jira_story_jobs WHERE id=?)
                WHERE job_id=? AND attempt=?""", (job_id, job_id, job_id, job_id, attempt))
            await conn.commit()
