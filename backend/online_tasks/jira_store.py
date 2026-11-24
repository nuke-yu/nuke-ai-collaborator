"""Durable Jira requests, shared Story jobs, and atomic dispatch claims."""
import json
import time
import uuid

import db
from executors.redaction import redact_secrets

_LIVE = """(j.state = 'starting' OR (j.state = 'linked'
            AND t.status IN ('created','dispatched','running','paused','stuck','retrying','restarted')))"""
_STORY_VIEW = """SELECT j.id, j.issue_key, j.state, j.attempt, j.error, j.mapping,
    j.pr_url, j.pr_state, j.validation, m.jira_done, m.mapping_conflict, m.seen_generation, t.task_id, t.group_id,
    t.status AS task_status, t.error_message AS task_error
    FROM jira_run_stories m JOIN jira_story_jobs j ON j.id = m.job_id
    LEFT JOIN agent_tasks t ON j.task_id = t.task_id"""


class JiraConflict(ValueError):
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


class JiraTaskStore:
    async def submit(self, site, kind, key, mappings, max_parallel, operator_id):
        async with db.write_connect(db.DB_PATH) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            query = "SELECT * FROM jira_task_runs WHERE site=? AND kind=? AND issue_key=?"
            prior = await _one(conn, query, (site, kind, key))
            if prior:
                merged = json.loads(prior["mappings"])
                for story, mapping in mappings.items():
                    if story in merged and merged[story] != mapping:
                        raise JiraConflict(f"Mapping for {story} differs from the submitted run")
                    merged[story] = mapping
                if merged != json.loads(prior["mappings"]):
                    await conn.execute("UPDATE jira_task_runs SET mappings=? WHERE id=?",
                                       (json.dumps(merged), prior["id"]))
                    for story, mapping in mappings.items():
                        await conn.execute(
                            """UPDATE jira_story_jobs SET mapping=?, state='queued', error=NULL
                               WHERE issue_key=? AND state='unmapped' AND id IN
                               (SELECT job_id FROM jira_run_stories WHERE run_id=?)""",
                            (json.dumps(mapping), story, prior["id"]),
                        )
                await conn.commit()
                return prior["id"]
            run_id = uuid.uuid4().hex
            await conn.execute(
                """INSERT INTO jira_task_runs(id, site, kind, issue_key, mappings, max_parallel, operator_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, site, kind, key, json.dumps(mappings), max_parallel, str(operator_id)),
            )
            await conn.commit()
            return run_id

    async def next_discovery(self, site):
        async with db.global_db() as conn:
            return await _one(conn, """SELECT * FROM jira_task_runs WHERE site=? AND discovery='pending'
                   AND retry_at<=? ORDER BY CASE kind WHEN 'story' THEN 0 ELSE 1 END, rowid LIMIT 1""",
                              (site, time.time()))

    async def save_page(self, run_id, issues, next_cursor, generation=None):
        async with db.write_connect(db.DB_PATH) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            run = await _one(conn, "SELECT * FROM jira_task_runs WHERE id=?", (run_id,))
            if generation is not None and run["generation"] != generation:
                await conn.rollback()
                return
            mappings = json.loads(run["mappings"])
            for item in issues:
                mapping = mappings.get(item.key)
                if mapping is None and run["kind"] == "story":
                    mapping = mappings.get(run["issue_key"])
                if not item.requirements:
                    state, error = "blocked", "Story description is missing or exceeds the supported length"
                elif mapping is None:
                    state, error = "unmapped", "Repository mapping required"
                else:
                    state, error = "queued", None
                await conn.execute(
                    """INSERT INTO jira_story_jobs(site, issue_id, issue_key, task_id, requirements, mapping, state, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(site, issue_id) DO UPDATE SET issue_key=excluded.issue_key,
                           requirements=CASE WHEN jira_story_jobs.attempt=0 THEN excluded.requirements
                                             ELSE jira_story_jobs.requirements END,
                           mapping=COALESCE(jira_story_jobs.mapping, excluded.mapping),
                           state=CASE
                             WHEN jira_story_jobs.attempt>0 THEN jira_story_jobs.state
                             WHEN excluded.requirements='' THEN 'blocked'
                             WHEN COALESCE(jira_story_jobs.mapping, excluded.mapping) IS NULL THEN 'unmapped'
                             ELSE 'queued' END,
                           error=CASE WHEN jira_story_jobs.attempt>0 THEN jira_story_jobs.error
                             WHEN excluded.requirements='' THEN excluded.error
                             WHEN COALESCE(jira_story_jobs.mapping, excluded.mapping) IS NULL THEN 'Repository mapping required'
                             ELSE NULL END""",
                    (run["site"], item.id, item.key, f"jira_{uuid.uuid4().hex}", item.requirements,
                     json.dumps(mapping) if mapping else None, state, error),
                )
                encoded = json.dumps(mapping) if mapping else None
                await conn.execute(
                    """UPDATE jira_run_stories SET jira_done=? WHERE job_id IN
                       (SELECT id FROM jira_story_jobs WHERE site=? AND issue_id=?)""",
                    (int(item.done), run["site"], item.id),
                )
                await conn.execute(
                    """INSERT INTO jira_run_stories(run_id, job_id, jira_done, mapping_conflict, seen_generation)
                       SELECT ?, id, ?, CASE WHEN ? IS NOT NULL AND ?<>mapping THEN 1 ELSE 0 END, ?
                       FROM jira_story_jobs WHERE site=? AND issue_id=?
                       ON CONFLICT(run_id, job_id) DO UPDATE SET
                           jira_done=excluded.jira_done, mapping_conflict=excluded.mapping_conflict,
                           seen_generation=excluded.seen_generation""",
                    (run_id, int(item.done), encoded, encoded, run["generation"], run["site"], item.id),
                )
            await conn.execute(
                """UPDATE jira_task_runs SET cursor=?, discovery=?, retry_at=0,
                       discovery_attempts=0, error=NULL WHERE id=?""",
                (next_cursor, "pending" if next_cursor else "ready", run_id),
            )
            await conn.commit()

    async def discovery_failed(self, run, error, *, retryable):
        count = run["discovery_attempts"] + 1
        retry = retryable and count < 6
        async with db.write_connect(db.DB_PATH) as conn:
            await conn.execute(
                """UPDATE jira_task_runs SET discovery=?, error=?, retry_at=?, discovery_attempts=?
                   WHERE id=? AND generation=?""",
                ("pending" if retry else "failed", safe_error(error),
                 time.time() + min(2 ** count, 60) if retry else 0, count, run["id"], run["generation"]),
            )
            await conn.commit()

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
                WHERE j.site=? AND j.state IN ('queued','retry') AND m.jira_done=0 AND m.mapping_conflict=0
                  AND r.discovery='ready' AND m.seen_generation=r.generation
                  AND r.paused=0 AND r.max_parallel >
                  (SELECT COUNT(*) FROM jira_story_jobs j LEFT JOIN agent_tasks t ON j.task_id=t.task_id
                   WHERE j.claimed_run=r.id AND {_LIVE})
                ORDER BY CASE r.kind WHEN 'story' THEN 0 ELSE 1 END, j.id, r.rowid LIMIT 1""",
                             (site,))
            if job:
                await conn.execute(
                    """UPDATE jira_story_jobs SET state='starting', claimed_run=?, attempt=attempt+1,
                       error=NULL WHERE id=?""", (job["run_id"], job["id"]),
                )
                job["attempt"] += 1
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
            return await _many(conn, """SELECT j.*, t.status AS task_status FROM jira_story_jobs j
                LEFT JOIN agent_tasks t ON j.task_id=t.task_id WHERE j.site=? AND j.state='starting'""", (site,))

    async def retry(self, run_id, key):
        async with db.write_connect(db.DB_PATH) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            job = await _one(conn, _STORY_VIEW + " WHERE m.run_id=? AND j.issue_key=?", (run_id, key))
            if not job:
                raise KeyError(key)
            run = await _one(conn, "SELECT generation, discovery FROM jira_task_runs WHERE id=?", (run_id,))
            if run["discovery"] != "ready" or job["seen_generation"] != run["generation"]:
                raise JiraConflict("Story is outside the run's current discovery results")
            retryable = {"failed", "aborted", "stuck", "stuck_permanently", "incomplete"}
            if job["jira_done"] or job["mapping_conflict"] or job["state"] not in {"linked", "failed", "interrupted"} or (
                    job["task_id"] and job["task_status"] not in retryable) or (
                    job["state"] == "linked" and not job["task_id"]):
                raise JiraConflict("Story is not in a retryable state")
            await conn.execute("UPDATE jira_story_jobs SET state='retry', error=NULL WHERE id=?",
                               (job["id"],))
            await conn.commit()

    async def set_paused(self, run_id, paused):
        async with db.write_connect(db.DB_PATH) as conn:
            cur = await conn.execute(
                """UPDATE jira_task_runs SET paused=?,
                   discovery=CASE WHEN ?=0 AND discovery='failed' THEN 'pending' ELSE discovery END,
                   retry_at=CASE WHEN ?=0 THEN 0 ELSE retry_at END,
                   discovery_attempts=CASE WHEN ?=0 THEN 0 ELSE discovery_attempts END
                   WHERE id=?""", (paused, paused, paused, paused, run_id))
            await conn.commit()
            return cur.rowcount == 1

    async def sync(self, run_id):
        async with db.write_connect(db.DB_PATH) as conn:
            await conn.execute(
                """UPDATE jira_task_runs SET generation=generation+1, discovery='pending', cursor=NULL,
                   error=NULL, discovery_attempts=0, retry_at=0 WHERE id=?""", (run_id,),
            )
            await conn.commit()

    async def read(self, run_id):
        async with db.global_db() as conn:
            run = await _one(conn,
                """SELECT id, kind, issue_key, discovery, paused, max_parallel, error, generation
                   FROM jira_task_runs WHERE id=?""",
                (run_id,))
            if run is None:
                return None
            jobs = await _many(conn, _STORY_VIEW + " WHERE m.run_id=? ORDER BY j.issue_key", (run_id,))
            attempts = await _many(conn,
                """SELECT a.job_id, a.number, a.stage, a.started_at, a.dispatch_ended_at, a.error
                   FROM jira_job_attempts a JOIN jira_run_stories m ON m.job_id=a.job_id
                   WHERE m.run_id=? ORDER BY a.number""", (run_id,))
        attempts_by_job = {}
        for entry in attempts:
            attempts_by_job.setdefault(entry.pop("job_id"), []).append(entry)
        stories = []
        for job in jobs:
            error = job["error"] or safe_error(job["task_error"] or "")
            if run["discovery"] == "ready" and job["seen_generation"] != run["generation"]:
                status = "removed_from_epic"
            elif job["jira_done"]:
                status = "skipped_completed"
            elif job["mapping_conflict"]:
                status, error = "blocked", "Repository mapping conflicts with the existing Story task"
            elif job["pr_state"]:
                status = job["pr_state"]
            elif job["state"] in {"queued", "retry", "starting", "unmapped", "blocked", "failed", "interrupted"}:
                status = {"retry": "queued", "starting": "preparing", "unmapped": "blocked"}.get(job["state"], job["state"])
            else:
                status = job["task_status"] or "task_record_missing"
                if status == "completed":
                    status = "awaiting_pr_confirmation"
            stories.append({
                "issue_key": job["issue_key"], "status": status,
                "attempt": job["attempt"], "task_id": job["task_id"], "group_id": job["group_id"],
                "attempts": attempts_by_job.get(job["id"], []),
                "repository": json.loads(job["mapping"])["repo_url"] if job["mapping"] else None,
                "validation": json.loads(job["validation"]) if job["validation"] else None,
                "pr_url": job["pr_url"], "error": error or None,
            })
        counts = {}
        for story in stories:
            status = story["status"]
            counts[status] = counts.get(status, 0) + 1
        return {**run, "paused": bool(run["paused"]), "counts": counts, "stories": stories}

    async def pending_prs(self, site, limit=4):
        async with db.global_db() as conn:
            return await _many(conn, """SELECT j.id, j.mapping, j.task_id, t.group_id, t.bot_id
                FROM jira_story_jobs j JOIN agent_tasks t ON t.task_id=j.task_id
                WHERE j.site=? AND j.state IN ('linked','interrupted')
                AND (t.status IN ('completed','incomplete','failed','aborted','stuck_permanently')
                     OR t.pr_url IS NOT NULL)
                AND j.pr_checked_at<=? AND (j.pr_state IS NULL OR
                    j.pr_state IN ('needs_review','pr_head_changed','pr_unverified'))
                ORDER BY j.pr_checked_at LIMIT ?""", (site, time.time() - 60, limit))

    async def record_pr(self, job_id, result, validation):
        async with db.write_connect(db.DB_PATH) as conn:
            encoded = json.dumps(validation) if validation else None
            if result:
                await conn.execute("""UPDATE jira_story_jobs SET pr_checked_at=?, pr_url=?, pr_state=?,
                    validation=COALESCE(?, validation) WHERE id=?""", (time.time(), *result, encoded, job_id))
            else:
                await conn.execute("""UPDATE jira_story_jobs SET pr_checked_at=?,
                    validation=COALESCE(?, validation) WHERE id=?""", (time.time(), encoded, job_id))
            await conn.commit()
