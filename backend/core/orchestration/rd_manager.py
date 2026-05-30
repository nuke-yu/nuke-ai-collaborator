import asyncio
import logging
import re
from bus import bus
from bus.events import ToolResult, TicketCreated
from workspace import group_workspace

log = logging.getLogger(__name__)

# Regex to find tickets in BOARD.md
# Format: | JIRA-123 | Title | Priority | ... |
TICKET_RE = re.compile(r"\|\s*(?P<id>[A-Z]+-\d+)\s*\|\s*(?P<title>[^|]+)\s*\|\s*(?P<priority>[^|]+)\s*\|")

class RDManager:
    """
    R&D Observer: Monitors BOARD.md for changes and dispatches business events.
    """
    def __init__(self):
        self._last_tickets: dict[int, set[str]] = {}  # group_id -> set of ticket IDs

    async def start(self):
        """Initialize known tickets and start background listener."""
        log.info("RDManager starting...")
        
        # Pre-scan existing groups to avoid re-firing old tickets
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
                tickets = self._parse_backlog(content)
                self._last_tickets[group_id] = set(tickets.keys())
            except Exception:
                pass

    async def _listen(self):
        # Listen for any tool result that might have modified BOARD.md
        sub = bus.subscribe(ToolResult)
        async with sub:
            async for ev in sub:
                # We check all successful write_file calls
                if ev.get("tool_name") == "write_file" and not ev.get("error"):
                    # Check the board in this group
                    await self.check_board(ev["group_id"])

    async def check_board(self, group_id: int):
        """Read BOARD.md and emit events for new tickets."""
        ws = group_workspace(group_id)
        board_path = ws / "BOARD.md"
        if not board_path.exists():
            return

        try:
            # Note: We use to_thread because read_text is synchronous
            content = await asyncio.to_thread(board_path.read_text, encoding="utf-8")
            tickets = self._parse_backlog(content)
            
            last = self._last_tickets.get(group_id, set())
            new_ids = tickets.keys() - last
            
            for tid in new_ids:
                t = tickets[tid]
                log.info("RDManager: detected new ticket %s in group %d", tid, group_id)
                await bus.publish(TicketCreated(
                    group_id=group_id,
                    ticket_id=tid,
                    title=t["title"],
                    description=t["title"], 
                    priority=t["priority"]
                ))
            
            # Update cache
            self._last_tickets[group_id] = set(tickets.keys())
        except Exception:
            log.exception("RDManager error checking board for group %d", group_id)

    def _parse_backlog(self, content: str) -> dict[str, dict]:
        """Extract ticket data from the ## Backlog section."""
        lines = content.splitlines()
        backlog_found = False
        tickets = {}
        for line in lines:
            if line.startswith("## Backlog"):
                backlog_found = True
                continue
            if backlog_found and line.startswith("## "):
                break # Next section started
            
            if backlog_found:
                m = TICKET_RE.search(line)
                if m:
                    tid = m.group("id").strip()
                    tickets[tid] = {
                        "title": m.group("title").strip(),
                        "priority": m.group("priority").strip()
                    }
        return tickets

rd_manager = RDManager()
