import asyncio
import logging
from db import connect
from bus import bus
from bus.events import ToolResult, TicketCreated
from workspace import group_workspace, write_file

log = logging.getLogger(__name__)

class RDManager:
    """
    R&D Observer & Reconciler (V3): 
    1. Monitors Ticket events (or DB directly) as the source of truth.
    2. Renders the tickets to BOARD.md (one-way data flow).
    """
    def __init__(self):
        # We no longer cache state from regex parsing.
        # This is kept for backward compatibility if any imports expect the property.
        self._last_tickets = {}

    async def start(self):
        """Initialize and start background listener."""
        log.info("RDManager (V3) starting...")
        # Note: Event listening is simplified since we don't parse BOARD.md anymore.
        asyncio.create_task(self._listen())

    async def _listen(self):
        # We can listen to Ticket state changes from the EventBus to re-render BOARD.md
        pass

    async def check_board(self, group_id: int):
        """
        (Legacy stub) Previously reconciled BOARD.md with DB.
        Now it just ensures BOARD.md reflects the DB state.
        """
        await self.render_board(group_id)

    async def render_board(self, group_id: int):
        """One-way render from SQLite 'tickets' table to BOARD.md."""

        try:
            async with connect() as db:
                async with db.execute(
                    "SELECT ticket_id, title, status, COALESCE(project, '') "
                    "FROM tickets WHERE group_id = ? ORDER BY id DESC",
                    (group_id,)
                ) as cur:
                    rows = await cur.fetchall()

            if not rows:
                content = "# 任务看板 (BOARD.md)\n\n(暂无任务)"
                await write_file(0, "BOARD.md", content, group_id=group_id)
                return

            backlog, in_progress, done = [], [], []

            for tid, title, status, project in rows:
                proj_cell = project or "-"
                line = f"| {tid} | {proj_cell} | {title} | [-] | {status.title()} |"
                if status == "backlog":
                    backlog.append(line)
                elif status == "in_progress":
                    in_progress.append(line)
                elif status == "done":
                    done.append(line)

            header = "| ID | Project | Title | Assignee | Status |\n|---|---|---|---|---|\n"
            content = "# 任务看板 (BOARD.md)\n\n(由系统基于真实数据库渲染，手动修改无效)\n\n"
            content += f"## Backlog\n{header}" + "\n".join(backlog) + "\n\n"
            content += f"## In Progress\n{header}" + "\n".join(in_progress) + "\n\n"
            content += f"## Done\n{header}" + "\n".join(done) + "\n"

            await write_file(0, "BOARD.md", content, group_id=group_id)

        except Exception:
            log.exception("RDManager failed to render board for group %d", group_id)

rd_manager = RDManager()
