#!/usr/bin/env python3
"""Headless mode for Nuke AI Collaborator.

Supports scriptable execution of AI tasks, CI/CD integration, and long-running
sessions with automatic restart and resume capabilities.

Based on gsd-2 headless implementation patterns.

Usage:
    python -m backend.headless auto --group-id 1 --member-id 2 --json
    python -m backend.headless next --group-id 1 --member-id 2 --resume session_123
    python -m backend.headless discuss "Review this code" --group-id 1 --member-id 2
"""

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from runtime import ipc
from runtime import supervisor as sup_mod
import db
from config import bootstrap_from_env


# ── Exit Codes (gsd-2 pattern) ───────────────────────────────────────────────

class ExitCode:
    """Standardized exit codes for headless mode."""
    SUCCESS = 0       # Task completed successfully
    ERROR = 1         # Error or timeout
    BLOCKED = 10      # Blocked (requires human intervention)
    CANCELLED = 11    # Cancelled by user signal


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class SessionInfo:
    """Session information for resume capability."""
    id: str
    group_id: int
    member_id: int
    created_at: float
    command: str
    status: str  # active, completed, blocked, cancelled
    query: str = ""

@dataclass
class Result:
    """Headless execution result."""
    exit_code: int
    interrupted: bool
    status: str
    text: str = ""
    data: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "interrupted": self.interrupted,
            "status": self.status,
            "text": self.text,
            "data": self.data,
        }


# ── Session Management ───────────────────────────────────────────────────────

SESSIONS_DIR = Path(__file__).parent / ".headless_sessions"


def ensure_sessions_dir():
    """Ensure sessions directory exists."""
    SESSIONS_DIR.mkdir(exist_ok=True)


def list_sessions(group_id: Optional[int] = None) -> list[SessionInfo]:
    """List all headless sessions, optionally filtered by group_id."""
    ensure_sessions_dir()
    sessions = []

    for file in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(file.read_text())
            session = SessionInfo(**data)
            if group_id is None or session.group_id == group_id:
                sessions.append(session)
        except (json.JSONDecodeError, KeyError):
            continue

    # Sort by created_at descending
    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return sessions


def save_session(session: SessionInfo):
    """Save session state to disk."""
    ensure_sessions_dir()
    file = SESSIONS_DIR / f"{session.id}.json"
    file.write_text(json.dumps(asdict(session), indent=2))


def delete_session(session_id: str):
    """Delete a session from disk."""
    file = SESSIONS_DIR / f"{session_id}.json"
    if file.exists():
        file.unlink()


def load_session(session_id: str) -> Optional[SessionInfo]:
    """Load a session by ID."""
    file = SESSIONS_DIR / f"{session_id}.json"
    if not file.exists():
        return None
    try:
        data = json.loads(file.read_text())
        return SessionInfo(**data)
    except (json.JSONDecodeError, KeyError):
        return None


def resolve_resume_session(sessions: list[SessionInfo], prefix: str) -> tuple[Optional[SessionInfo], Optional[str]]:
    """
    Resolve a session prefix to a single session.

    Returns:
        Tuple of (session, error_message). If success, error_message is None.
    """
    # Exact match takes priority
    exact = next((s for s in sessions if s.id == prefix), None)
    if exact:
        return exact, None

    # Prefix match
    matches = [s for s in sessions if s.id.startswith(prefix)]
    if not matches:
        return None, f"No session matching '{prefix}' found"
    if len(matches) > 1:
        list_str = "\n".join(f"  {s.id}" for s in matches)
        return None, f"Ambiguous session prefix '{prefix}' matches {len(matches)} sessions:\n{list_str}"

    return matches[0], None


def resolve_headless_context(args, session_context: Optional[SessionInfo]) -> tuple[str, str]:
    """Resolve the effective command/query for a headless run.

    When resuming and the caller does not override the command or query, keep
    using the saved session values instead of dropping back to the parser
    defaults.
    """
    command = args.command
    if session_context is not None and command == "auto":
        command = session_context.command

    query = getattr(args, "query", "") or ""
    if session_context is not None and not query:
        query = session_context.query or ""

    return command, query


# ── Supervisor Connection ───────────────────────────────────────────────────

_supervisor_addr: Optional[str] = None
_supervisor_writer: Optional[Any] = None
_supervisor_reader: Optional[Any] = None


async def connect_to_supervisor() -> bool:
    """Connect to the Supervisor IPC server."""
    global _supervisor_addr, _supervisor_writer, _supervisor_reader

    # Get supervisor address from environment or use default
    _supervisor_addr = os.getenv("NUPC_ADDR", "ipc:///tmp/nupe-supervisor")

    try:
        # For headless mode, we need to connect as a worker would
        # This is a simplified connection - in production, use the proper IPC channel
        reader, writer = await asyncio.open_unix_connection(
            _supervisor_addr.replace("ipc://", "")
        )
        _supervisor_reader, _supervisor_writer = reader, writer

        # Send HELLO frame
        hello = {"type": "hello", "worker_id": "headless-client"}
        await ipc.send_msg(_supervisor_writer, hello)

        # Wait for acknowledgment
        response = await ipc.recv_msg(_supervisor_reader)
        return response.get("status") == "ok"
    except Exception as e:
        print(f"[headless] Failed to connect to supervisor: {e}", file=sys.stderr)
        return False


async def send_to_supervisor(msg: dict) -> Optional[dict]:
    """Send a message to Supervisor and wait for response."""
    if not _supervisor_writer or not _supervisor_reader:
        raise RuntimeError("Not connected to supervisor")

    try:
        await ipc.send_msg(_supervisor_writer, msg)
        response = await asyncio.wait_for(ipc.recv_msg(_supervisor_reader), timeout=30.0)
        return response
    except asyncio.TimeoutError:
        raise TimeoutError("Supervisor response timeout")


# ── Message Types ───────────────────────────────────────────────────────────

MessageType = str

QUERY: MessageType = "query"
MUTATE: MessageType = "mutate"
USER_MESSAGE: MessageType = "user_message"

COMMAND_HANDLERS = {
    "auto": "auto_mode",
    "next": "advance_milestone",
    "discuss": "discussion",
    "plan": "planning",
}


# ── Core Execution Logic ────────────────────────────────────────────────────

async def run_query(group_id: int, member_id: int, fields: dict) -> dict:
    """Execute a query against the group database."""
    trace_id = f"headless-{int(time.time())}-{os.getpid()}"

    msg = ipc.protocol.envelope(
        QUERY,
        group_id=group_id,
        trace_id=trace_id,
        member_id=member_id,
        **fields
    )

    response = await send_to_supervisor(msg)
    return response or {}


async def run_user_message(
    group_id: int,
    member_id: int,
    content: str,
    command: str = None,
    timeout: int = 300000
) -> Result:
    """
    Send a user message to the group and wait for completion.

    Args:
        group_id: Target group ID
        member_id: Member sending the message
        content: Message content
        command: Optional command type (auto/next/discuss/plan)
        timeout: Timeout in milliseconds

    Returns:
        Result with exit_code, status, and text/data
    """
    trace_id = f"headless-{int(time.time())}-{os.getpid()}"

    # Build message based on command type
    if command and command in COMMAND_HANDLERS:
        msg_type = COMMAND_HANDLERS[command]
    else:
        msg_type = USER_MESSAGE

    msg = ipc.protocol.envelope(
        msg_type,
        group_id=group_id,
        trace_id=trace_id,
        member_id=member_id,
        content=content,
    )

    # Send message
    await send_to_supervisor(msg)

    # Wait for result with timeout
    try:
        # Poll for results
        start_time = time.time()
        last_result = None

        while time.time() - start_time < timeout / 1000:
            try:
                response = await asyncio.wait_for(
                    send_to_supervisor({"type": "poll", "trace_id": trace_id}),
                    timeout=5.0
                )

                if response:
                    last_result = response

                    # Check for completion indicators
                    if response.get("type") in ("stream_end", "result"):
                        break

            except asyncio.TimeoutError:
                continue

        # Build result
        if last_result:
            text = last_result.get("text", last_result.get("content", ""))
            data = last_result.get("data", {})

            # Determine exit code based on status
            status = last_result.get("status", "complete")
            if status in ("success", "complete", "completed"):
                exit_code = ExitCode.SUCCESS
            elif status in ("error", "timeout"):
                exit_code = ExitCode.ERROR
            elif status in ("blocked", "paused"):
                exit_code = ExitCode.BLOCKED
            elif status in ("cancelled", "interrupted"):
                exit_code = ExitCode.CANCELLED
            else:
                exit_code = ExitCode.ERROR

            return Result(
                exit_code=exit_code,
                interrupted=False,
                status=status,
                text=text,
                data=data,
            )
        else:
            return Result(
                exit_code=ExitCode.ERROR,
                interrupted=False,
                status="timeout",
                text="No response received within timeout period",
            )

    except asyncio.CancelledError:
        return Result(
            exit_code=ExitCode.CANCELLED,
            interrupted=True,
            status="cancelled",
            text="Task cancelled by user",
        )


async def run_headless_once(args) -> Result:
    """
    Execute a single headless run.

    Args:
        args: Parsed command line arguments

    Returns:
        Result with execution outcome
    """
    # Validate group and member exist
    async with db.global_db() as cdb:
        async with cdb.execute(
            "SELECT 1 FROM groups WHERE id = ?", (args.group_id,)
        ) as cur:
            if not await cur.fetchone():
                return Result(
                    exit_code=ExitCode.ERROR,
                    interrupted=False,
                    status="invalid_group",
                    text=f"Group {args.group_id} not found",
                )

        async with cdb.execute(
            "SELECT 1 FROM members WHERE id = ? AND group_id = ?",
            (args.member_id, args.group_id)
        ) as cur:
            if not await cur.fetchone():
                return Result(
                    exit_code=ExitCode.ERROR,
                    interrupted=False,
                    status="invalid_member",
                    text=f"Member {args.member_id} not found in group {args.group_id}",
                )

    # Load session context if resuming
    session_context = None
    if args.resume:
        sessions = list_sessions(args.group_id)
        session, error = resolve_resume_session(sessions, args.resume)

        if error:
            return Result(
                exit_code=ExitCode.ERROR,
                interrupted=False,
                status="invalid_session",
                text=error,
            )

        if session and session.status in ("active", "blocked"):
            session_context = session

    command, query = resolve_headless_context(args, session_context)

    # Create new session record
    session_id = f"headless_{args.group_id}_{args.member_id}_{int(time.time())}"
    new_session = SessionInfo(
        id=session_id,
        group_id=args.group_id,
        member_id=args.member_id,
        created_at=time.time(),
        command=command,
        query=query,
        status="active",
    )
    save_session(new_session)

    try:
        # Execute the command
        if command in ("auto", "next", "discuss", "plan"):
            result = await run_user_message(
                group_id=args.group_id,
                member_id=args.member_id,
                content=query,
                command=command,
                timeout=args.timeout,
            )
        else:
            result = await run_user_message(
                group_id=args.group_id,
                member_id=args.member_id,
                content=command,  # Use command as content for custom commands
                timeout=args.timeout,
            )

        # Update session status
        new_session.status = result.status
        save_session(new_session)

        return result

    except Exception as e:
        # Update session status on error
        new_session.status = "error"
        save_session(new_session)

        return Result(
            exit_code=ExitCode.ERROR,
            interrupted=False,
            status="error",
            text=f"Execution failed: {str(e)}",
        )


def should_restart_headless_run(result: Result) -> bool:
    """Determine if the headless run should be restarted."""
    # Only restart on error/timeout, not on blocked/cancelled
    return result.status in ("error", "timeout")


# ── CLI Argument Parser ─────────────────────────────────────────────────────

def parse_args(argv=None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="headless",
        description="Nuke AI Collaborator - Headless mode for scriptable execution",
    )

    # Command (positional)
    parser.add_argument(
        "command",
        nargs="?",
        default="auto",
        choices=["auto", "next", "discuss", "plan"],
        help="Command to execute (default: auto)",
    )

    # Group and member identification
    parser.add_argument(
        "--group-id",
        required=True,
        type=int,
        help="Target group ID",
    )

    parser.add_argument(
        "--member-id",
        required=True,
        type=int,
        help="Member ID sending the message",
    )

    # Query/content
    parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="Query or content to process (optional, can also use --query)",
    )

    parser.add_argument(
        "--query-text",
        dest="query",
        help="Query or content to process (alternative to positional)",
    )

    # Output options
    output_choices = ["text", "json", "stream-json"]
    parser.add_argument(
        "--output-format",
        choices=output_choices,
        default="text",
        help="Output format (default: text)",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (shortcut for --output-format json)",
    )

    # Timeout options
    parser.add_argument(
        "--timeout",
        type=int,
        default=300000,  # 5 minutes
        help="Timeout in milliseconds (default: 300000)",
    )

    parser.add_argument(
        "--response-timeout",
        type=int,
        default=30000,
        help="Response timeout in milliseconds (default: 30000)",
    )

    # Session management
    parser.add_argument(
        "--resume",
        type=str,
        help="Session ID to resume",
    )

    # Restart behavior
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=3,
        help="Maximum number of auto-restarts on error (default: 3)",
    )

    # Other options
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    parser.add_argument(
        "--bare",
        action="store_true",
        help="Suppress CLAUDE.md, AGENTS.md, and other context files",
    )

    return parser.parse_args(argv)


# ── Main Entry Point ────────────────────────────────────────────────────────

async def run_headless_main():
    """Main entry point for headless mode."""
    args = parse_args()

    # Handle --json shortcut
    if args.json:
        args.output_format = "json"

    # Ensure sessions directory exists
    ensure_sessions_dir()

    # Connect to supervisor
    connected = await connect_to_supervisor()
    if not connected:
        print(
            "[headless] Error: Cannot connect to supervisor. Is the server running?",
            file=sys.stderr
        )
        sys.exit(ExitCode.ERROR)

    # Set up signal handlers for clean interruption
    interrupted = False

    def signal_handler(signum, frame):
        nonlocal interrupted
        interrupted = True
        print("\n[headless] Received interrupt signal", file=sys.stderr)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Main loop with restart support
    max_restarts = args.max_restarts
    restart_count = 0

    while True:
        # Run single execution
        result = await run_headless_once(args)

        # Output result
        if args.output_format == "json" or args.output_format == "stream-json":
            print(json.dumps(result.to_dict()))
        else:
            print(result.text or "")

        # Check if we should exit
        if result.exit_code in (ExitCode.SUCCESS, ExitCode.BLOCKED):
            # Success or blocked - exit normally
            sys.exit(result.exit_code)

        if interrupted:
            # Signal received - exit
            sys.exit(ExitCode.CANCELLED)

        # Check if we should restart
        if not should_restart_headless_run(result):
            print(
                f"[headless] Restart suppressed: {result.status}",
                file=sys.stderr
            )
            sys.exit(result.exit_code)

        # Check restart limit
        if restart_count >= max_restarts:
            print(
                f"[headless] Max restarts ({max_restarts}) reached. Exiting.",
                file=sys.stderr
            )
            sys.exit(result.exit_code)

        # Calculate backoff
        restart_count += 1
        backoff_ms = min(5000 * restart_count, 30000)
        backoff_sec = backoff_ms / 1000

        print(
            f"[headless] Restarting in {backoff_sec:.0f}s "
            f"(attempt {restart_count}/{max_restarts})...",
            file=sys.stderr
        )

        await asyncio.sleep(backoff_sec)


def main():
    """Synchronous wrapper for async main."""
    asyncio.run(run_headless_main())


if __name__ == "__main__":
    main()
