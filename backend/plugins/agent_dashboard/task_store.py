"""
plugins/agent_dashboard/task_store.py — Persistent storage for agent tasks.

P1-1: Replaces in-memory _tasks dict with database-backed storage.
All task state is persisted to the agent_tasks table and survives
process restarts.

Usage:
    from plugins.agent_dashboard.task_store import TaskStore

    store = TaskStore()
    await store.create_task(task_id, group_id, bot_id, ...)
    task = await store.get_task(task_id)
    await store.update_status(task_id, "running")
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)


class TaskStore:
    """Database-backed storage for agent tasks."""

    async def create_task(
        self,
        task_id: str,
        group_id: int,
        bot_id: int,
        repo_url: str,
        requirements: str,
        base_branch: str = "main",
        test_command: str = "",
        model: str = "deepseek-chat",
        max_iterations: int = 100,
    ) -> dict:
        """Create a new task record.

        Returns:
            dict with task_id, group_id, bot_id, status, created_at
        """
        from db import write_connect

        async with write_connect() as db:
            await db.execute(
                """
                INSERT INTO agent_tasks (
                    task_id, group_id, bot_id, repo_url, requirements,
                    base_branch, test_command, model, max_iterations, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'created')
                """,
                (
                    task_id, group_id, bot_id, repo_url, requirements,
                    base_branch, test_command, model, max_iterations,
                ),
            )
            await db.commit()

            # Fetch the created record to return with timestamps
            return await self.get_task(task_id)

    async def get_task(self, task_id: str) -> Optional[dict]:
        """Get a task by ID.

        Returns:
            dict with all task fields, or None if not found
        """
        from db import connect

        async with connect() as db:
            cur = await db.execute(
                """
                SELECT task_id, group_id, bot_id, repo_url, requirements,
                       base_branch, test_command, model, max_iterations,
                       status, created_at, updated_at, pr_url, error_message
                FROM agent_tasks
                WHERE task_id = ?
                """,
                (task_id,),
            )
            row = await cur.fetchone()

        if not row:
            return None

        return {
            "task_id": row[0],
            "group_id": row[1],
            "bot_id": row[2],
            "repo_url": row[3],
            "requirements": row[4],
            "base_branch": row[5],
            "test_command": row[6],
            "model": row[7],
            "max_iterations": row[8],
            "status": row[9],
            "created_at": row[10],
            "updated_at": row[11],
            "pr_url": row[12],
            "error_message": row[13],
        }

    async def list_tasks(
        self,
        group_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """List tasks with optional filters.

        Args:
            group_id: Filter by group (optional)
            status: Filter by status (optional)
            limit: Max number of tasks to return

        Returns:
            List of task dicts
        """
        from db import connect

        query = """
            SELECT task_id, group_id, bot_id, repo_url, requirements,
                   base_branch, test_command, model, max_iterations,
                   status, created_at, updated_at, pr_url, error_message
            FROM agent_tasks
        """
        params = []
        conditions = []

        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)

        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        async with connect() as db:
            cur = await db.execute(query, params)
            rows = await cur.fetchall()

        return [
            {
                "task_id": row[0],
                "group_id": row[1],
                "bot_id": row[2],
                "repo_url": row[3],
                "requirements": row[4],
                "base_branch": row[5],
                "test_command": row[6],
                "model": row[7],
                "max_iterations": row[8],
                "status": row[9],
                "created_at": row[10],
                "updated_at": row[11],
                "pr_url": row[12],
                "error_message": row[13],
            }
            for row in rows
        ]

    async def update_status(
        self,
        task_id: str,
        status: str,
        pr_url: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Update task status and optional fields.

        Args:
            task_id: Task ID
            status: New status (created, running, completed, failed, aborted)
            pr_url: PR URL (for completed tasks)
            error_message: Error message (for failed tasks)

        Returns:
            True if updated, False if task not found
        """
        from db import write_connect

        async with write_connect() as db:
            cur = await db.execute(
                """
                UPDATE agent_tasks
                SET status = ?,
                    pr_url = COALESCE(?, pr_url),
                    error_message = COALESCE(?, error_message),
                    updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
                """,
                (status, pr_url, error_message, task_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task record.

        Returns:
            True if deleted, False if task not found
        """
        from db import write_connect

        async with write_connect() as db:
            cur = await db.execute(
                "DELETE FROM agent_tasks WHERE task_id = ?",
                (task_id,),
            )
            await db.commit()
            return cur.rowcount > 0
