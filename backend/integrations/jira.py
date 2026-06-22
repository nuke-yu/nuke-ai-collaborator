"""integrations/jira.py — Jira 接入（MVP1 用本地 tickets 表替身）。

LocalJiraClient 把工单写进本群的 tickets 表（已有表，无需建新表）；description 与
acceptance_criteria 存在 metadata_json 里。真 Jira 接入时实现一个调 REST API 的
JiraClient 子类并 set_jira() 即可，rd_tools 调用方不变。
"""
import json
import logging
from abc import ABC, abstractmethod

from db import write_connect, get_db

log = logging.getLogger(__name__)


_VALID_STATUSES = frozenset({"backlog", "in_progress", "done"})


class JiraClient(ABC):
    @abstractmethod
    async def create_ticket(self, group_id: int, title: str,
                            description: str = "", acceptance_criteria: str = "",
                            project: str = "") -> dict:
        """创建一个工单，返回 {ticket_id, title, project}。"""

    @abstractmethod
    async def list_tickets(self, group_id: int) -> list[dict]:
        """列出本群工单：[{ticket_id, title, status, project, description, acceptance_criteria}]。"""

    @abstractmethod
    async def update_ticket(self, group_id: int, ticket_id: str, status: str,
                            project: str | None = None) -> dict:
        """更新工单状态（及可选的项目名），返回 {ticket_id, status, project}。"""


class LocalJiraClient(JiraClient):
    """替身：工单落本地 tickets 表。"""

    async def create_ticket(self, group_id: int, title: str,
                            description: str = "", acceptance_criteria: str = "",
                            project: str = "") -> dict:
        meta = json.dumps(
            {"description": description, "acceptance_criteria": acceptance_criteria},
            ensure_ascii=False,
        )
        async with write_connect() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM tickets WHERE group_id = ?", (group_id,)
            ) as cur:
                n = (await cur.fetchone())[0] + 1
            ticket_id = f"DFT-{n}"
            await db.execute(
                "INSERT INTO tickets (ticket_id, group_id, title, status, project, metadata_json) "
                "VALUES (?, ?, ?, 'backlog', ?, ?)",
                (ticket_id, group_id, title, project, meta),
            )
            await db.commit()
        return {"ticket_id": ticket_id, "title": title, "project": project}

    async def list_tickets(self, group_id: int) -> list[dict]:
        async with get_db() as db:
            async with db.execute(
                "SELECT ticket_id, title, status, project, metadata_json FROM tickets "
                "WHERE group_id = ? ORDER BY id", (group_id,)
            ) as cur:
                rows = await cur.fetchall()
        out = []
        for tid, title, status, project, meta_json in rows:
            try:
                meta = json.loads(meta_json) if meta_json else {}
            except Exception:
                meta = {}
            out.append({
                "ticket_id": tid, "title": title, "status": status,
                "project": project or "",
                "description": meta.get("description", ""),
                "acceptance_criteria": meta.get("acceptance_criteria", ""),
            })
        return out

    async def update_ticket(self, group_id: int, ticket_id: str, status: str,
                            project: str | None = None) -> dict:
        if status not in _VALID_STATUSES:
            raise ValueError(f"无效状态 '{status}'，可选值：{sorted(_VALID_STATUSES)}")
        async with write_connect() as db:
            if project is not None:
                cur = await db.execute(
                    "UPDATE tickets SET status = ?, project = ? "
                    "WHERE ticket_id = ? AND group_id = ?",
                    (status, project, ticket_id, group_id),
                )
            else:
                cur = await db.execute(
                    "UPDATE tickets SET status = ? WHERE ticket_id = ? AND group_id = ?",
                    (status, ticket_id, group_id),
                )
            await db.commit()
        if cur.rowcount == 0:
            raise ValueError(f"工单 {ticket_id} 不存在或不属于本群组")
        if status == "done":
            try:
                from workspace.git_worktree import promote_worktree
                await promote_worktree(group_id, ticket_id)
            except Exception as e:
                log.warning(f"Failed to promote git worktree for task {ticket_id}: {e}", exc_info=True)
        # Read back the final project value (may have been set previously)
        async with get_db() as db:
            async with db.execute(
                "SELECT project FROM tickets WHERE ticket_id = ? AND group_id = ?",
                (ticket_id, group_id),
            ) as cur:
                row = await cur.fetchone()
        final_project = (row[0] or "") if row else ""
        return {"ticket_id": ticket_id, "status": status, "project": final_project}


_client: JiraClient = LocalJiraClient()


def get_jira() -> JiraClient:
    return _client


def set_jira(client: JiraClient) -> None:
    global _client
    _client = client
