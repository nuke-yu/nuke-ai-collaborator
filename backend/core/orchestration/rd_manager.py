import asyncio
import logging
import re
import json
from bus import bus
from bus.events import ToolResult, TicketCreated
from workspace import group_workspace, write_file

log = logging.getLogger(__name__)

# Regex to find tickets in BOARD.md
# Format: | JIRA-123 | Title | [Assignee] | Status | ... |
TICKET_RE = re.compile(r"\|\s*(?P<id>[A-Z]+-\d+)\s*\|\s*(?P<title>[^|]+)\s*\|")

class RDManager:
    """
    R&D Observer & Reconciler: 
    1. Monitors BOARD.md for changes.
    2. Syncs ticket status to Database.
    3. Archives 'Done' tickets from File to Database to keep context lean.
    """
    def __init__(self):
        self._last_tickets: dict[int, dict[str, str]] = {}  # group_id -> {ticket_id: status}

    async def start(self):
        """Initialize known tickets and start background listener."""
        log.info("RDManager starting...")
        
        from db import connect
        try:
            async with connect() as db:
                async with db.execute("SELECT id FROM groups") as cur:
                    rows = await cur.fetchall()
                    for (gid,) in rows:
                        await self._init_group(gid)
        except Exception:
            log.exception("RDManager: failed to pre-scan groups")

        asyncio.create_task(self._listen())

    async def _init_group(self, group_id: int):
        ws = group_workspace(group_id)
        board_path = ws / "BOARD.md"
        if board_path.exists():
            try:
                content = board_path.read_text(encoding="utf-8")
                tickets = self._parse_board(content)
                self._last_tickets[group_id] = {tid: t["status"] for tid, t in tickets.items()}
            except Exception:
                pass

    async def _listen(self):
        sub = bus.subscribe(ToolResult)
        async with sub:
            async for ev in sub:
                # Watch for write_file to BOARD.md
                if ev.get("tool_name") == "write_file" and not ev.get("error"):
                    # Use a small delay to ensure the file system has settled and 
                    # multiple rapid writes (if any) are caught together.
                    await asyncio.sleep(0.5)
                    await self.check_board(ev["group_id"])

    async def check_board(self, group_id: int):
        """Reconcile BOARD.md with Database and perform auto-cleaning."""
        ws = group_workspace(group_id)
        board_path = ws / "BOARD.md"
        if not board_path.exists():
            return

        try:
            content = await asyncio.to_thread(board_path.read_text, encoding="utf-8")
            tickets = self._parse_board(content)
            
            last_map = self._last_tickets.get(group_id, {})
            
            # 1. Detect New & Changed
            for tid, t in tickets.items():
                old_status = last_map.get(tid)
                
                # If new, fire creation event
                if old_status is None:
                    log.info("RDManager: new ticket %s in group %d", tid, group_id)
                    await bus.publish(TicketCreated(
                        group_id=group_id, ticket_id=tid,
                        title=t["title"], description=t["title"], priority="medium"
                    ))
                
                # If status changed or new, sync to DB
                if old_status != t["status"]:
                    await self._sync_to_db(group_id, tid, t)

            # 2. Archive 'Done' tickets
            done_ids = [tid for tid, t in tickets.items() if t["status"].lower() == "done"]
            if done_ids:
                await self._perform_archiving(group_id, content, done_ids)
            
            # 3. Update local cache (after archiving might have changed the content)
            # Re-read to be sure of the state after archival
            final_content = await asyncio.to_thread(board_path.read_text, encoding="utf-8")
            final_tickets = self._parse_board(final_content)
            self._last_tickets[group_id] = {tid: t["status"] for tid, t in final_tickets.items()}
            
        except Exception:
            log.exception("RDManager error checking board for group %d", group_id)

    async def _sync_to_db(self, group_id: int, ticket_id: str, data: dict):
        from db import connect
        try:
            async with connect() as db:
                completed_at = "datetime('now')" if data["status"].lower() == "done" else "NULL"
                await db.execute("""
                    INSERT INTO tickets (group_id, ticket_id, title, status, updated_at, completed_at)
                    VALUES (?, ?, ?, ?, datetime('now'), """ + completed_at + """)
                    ON CONFLICT(group_id, ticket_id) DO UPDATE SET
                        status = excluded.status,
                        updated_at = datetime('now'),
                        completed_at = CASE WHEN excluded.status = 'done' THEN datetime('now') ELSE NULL END
                """, (group_id, ticket_id, data["title"], data["status"].lower()))
                await db.commit()
        except Exception:
            log.exception("RDManager failed to sync ticket %s to DB", ticket_id)

    async def _perform_archiving(self, group_id: int, content: str, done_ids: list[str]):
        """Remove 'Done' tickets from BOARD.md while keeping them in DB."""
        lines = content.splitlines()
        new_lines = []
        archived_count = 0
        
        for line in lines:
            # Check if this line contains one of our done_ids
            should_remove = False
            for tid in done_ids:
                # Basic check: line contains | JIRA-XXX | and | Done |
                if f"| {tid} |" in line and "| done |" in line.lower():
                    should_remove = True
                    break
            
            if should_remove:
                archived_count += 1
                continue
            new_lines.append(line)
        
        if archived_count > 0:
            log.info("RDManager: archiving %d tickets from BOARD.md in group %d", archived_count, group_id)
            # Use the VFS-locked write_file to safely update the board
            # bot_id=0 represents the system manager
            await write_file(0, "BOARD.md", "\n".join(new_lines))

    def _parse_board(self, content: str) -> dict[str, dict]:
        """Parse all sections of the board to find tickets and their status."""
        sections = {"backlog": [], "in_progress": [], "done": []}
        current_section = None
        
        tickets = {}
        for line in content.splitlines():
            l = line.lower()
            if "## backlog" in l: current_section = "backlog"
            elif "## 进行中" in l or "## in progress" in l: current_section = "in_progress"
            elif "## 已完成" in l or "## done" in l: current_section = "done"
            elif line.startswith("## "): current_section = None
            
            if current_section:
                m = TICKET_RE.search(line)
                if m:
                    tid = m.group("id").strip()
                    tickets[tid] = {
                        "title": m.group("title").strip(),
                        "status": current_section
                    }
        return tickets

rd_manager = RDManager()
