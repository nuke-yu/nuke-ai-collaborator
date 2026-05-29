# Session Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add crash-safe session checkpointing to `tool_loop_v1` so that long-running Bot tasks can be automatically recovered after a process restart.

**Architecture:** Two new DB tables — `agent_sessions` (lightweight metadata) and `session_events` (append-only WAL-style event log). Events are written **before** each operation (write `tool_call` event, then execute the tool). On startup, any `status='running'` session is reconstructed from its event log and resumed. Child sessions (fork skills) are recovered before their parent.

**Tech Stack:** Python 3.11 · aiosqlite · FastAPI · existing `db/migrations.py` pattern · existing `scheduler/` pattern for module isolation.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `backend/sessions/__init__.py` | Create | Public API: `start`, `append_event`, `complete`, `fail`, `recover_all` |
| `backend/sessions/store.py` | Create | Raw DB ops: insert session, append event, query events |
| `backend/sessions/recovery.py` | Create | On-startup recovery: find orphaned sessions, reconstruct messages, re-dispatch |
| `backend/db/migrations.py` | Modify | Add `migration_004` — two new tables |
| `backend/executors/plugins/tool_loop_v1.py` | Modify | Wire in session start/events at key points in `run()` |
| `backend/main.py` | Modify | Call `sessions.recover_all()` during lifespan startup |
| `backend/tests/test_sessions.py` | Create | Unit tests for store, recovery, and reconstruction logic |

---

## Task 1: DB Migration — `agent_sessions` + `session_events` tables

**Files:**
- Modify: `backend/db/migrations.py` (lines 90–94, MIGRATIONS list + new function)
- Test: `backend/tests/test_sessions.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_sessions.py
import os, sys, asyncio, unittest
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
from db.schema import init_db
import db.migrations as _migrations_mod

_HERE = Path(__file__).parent.parent
_TEST_DB = str(_HERE / "test_sessions.db")


def _use_test_db():
    _db_mod.DB_PATH = _TEST_DB

def _restore_db(orig):
    _db_mod.DB_PATH = orig


class TestMigration004(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _use_test_db()
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        import db.migrations as m
        self._orig_migrations = list(m.MIGRATIONS)

    async def asyncTearDown(self):
        import db.migrations as m
        m.MIGRATIONS = self._orig_migrations
        _restore_db(self._orig)
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def test_migration_004_creates_agent_sessions(self):
        import aiosqlite
        from db.migrations import migration_004, run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        async with aiosqlite.connect(_TEST_DB) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_sessions'"
            ) as cur:
                row = await cur.fetchone()
        self.assertIsNotNone(row)

    async def test_migration_004_creates_session_events(self):
        import aiosqlite
        from db.migrations import migration_004, run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        async with aiosqlite.connect(_TEST_DB) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_events'"
            ) as cur:
                row = await cur.fetchone()
        self.assertIsNotNone(row)

    async def test_migration_004_idempotent(self):
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
        # Running again must not raise
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -m pytest tests/test_sessions.py::TestMigration004 -v 2>&1 | head -30
```

Expected: FAIL — `migration_004` not defined yet.

- [ ] **Step 3: Add `migration_004` to `db/migrations.py`**

Insert after `migration_003` (before the MIGRATIONS list, around line 83):

```python
async def migration_004(db):
    """Add agent_sessions and session_events tables for crash-safe session recovery."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id            TEXT PRIMARY KEY,
            parent_id     TEXT DEFAULT NULL,
            bot_id        INTEGER NOT NULL,
            group_id      INTEGER NOT NULL,
            status        TEXT NOT NULL DEFAULT 'running',
            executor_id   TEXT NOT NULL DEFAULT 'tool_loop_v1',
            config_json   TEXT NOT NULL DEFAULT '{}',
            user_message  TEXT NOT NULL DEFAULT '',
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS session_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            payload     TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_events ON session_events(session_id, id)"
    )
    await db.commit()
```

Then append `migration_004` to the `MIGRATIONS` list:

```python
MIGRATIONS: list = [
    migration_001,
    migration_002,
    migration_003,
    migration_004,
]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -m pytest tests/test_sessions.py::TestMigration004 -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/db/migrations.py backend/tests/test_sessions.py
git commit -m "feat: add migration_004 for agent_sessions + session_events tables"
```

---

## Task 2: `sessions/store.py` — raw DB operations

**Files:**
- Create: `backend/sessions/__init__.py`
- Create: `backend/sessions/store.py`
- Test: `backend/tests/test_sessions.py` (add `TestSessionStore` class)

- [ ] **Step 1: Write the failing tests**

Add this class to `backend/tests/test_sessions.py`:

```python
class TestSessionStore(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _use_test_db()
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)

    async def asyncTearDown(self):
        _restore_db(self._orig)
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def test_create_session(self):
        from sessions.store import create_session, get_session
        sid = await create_session(
            session_id="s1",
            bot_id=1, group_id=1,
            config={"system_prompt": "hi", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="hello",
        )
        self.assertEqual(sid, "s1")
        row = await get_session("s1")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["bot_id"], 1)

    async def test_append_and_get_events(self):
        from sessions.store import create_session, append_event, get_events
        await create_session(
            session_id="s2", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="test",
        )
        await append_event("s2", "session_start", {"user_content": "test"})
        await append_event("s2", "tool_call", {"tool_call_id": "t1", "tool_name": "read_file", "arguments": {}})
        events = await get_events("s2")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "session_start")
        self.assertEqual(events[1]["event_type"], "tool_call")

    async def test_update_session_status(self):
        from sessions.store import create_session, update_session_status, get_session
        await create_session(
            session_id="s3", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="x",
        )
        await update_session_status("s3", "completed")
        row = await get_session("s3")
        self.assertEqual(row["status"], "completed")

    async def test_get_orphaned_sessions(self):
        from sessions.store import create_session, get_orphaned_sessions
        await create_session(
            session_id="s4", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="y",
        )
        orphans = await get_orphaned_sessions()
        ids = [o["id"] for o in orphans]
        self.assertIn("s4", ids)

    async def test_add_tokens(self):
        from sessions.store import create_session, add_tokens, get_session
        await create_session(
            session_id="s5", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="z",
        )
        await add_tokens("s5", input_tokens=100, output_tokens=50)
        await add_tokens("s5", input_tokens=20, output_tokens=10)
        row = await get_session("s5")
        self.assertEqual(row["input_tokens"], 120)
        self.assertEqual(row["output_tokens"], 60)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -m pytest tests/test_sessions.py::TestSessionStore -v 2>&1 | head -20
```

Expected: FAIL — `sessions.store` not found.

- [ ] **Step 3: Create `backend/sessions/__init__.py`**

```python
# sessions/__init__.py
from sessions.store import (
    create_session, append_event, get_session,
    get_events, update_session_status, get_orphaned_sessions, add_tokens,
)
from sessions.recovery import recover_all

__all__ = [
    "create_session", "append_event", "get_session",
    "get_events", "update_session_status", "get_orphaned_sessions", "add_tokens",
    "recover_all",
]
```

- [ ] **Step 4: Create `backend/sessions/store.py`**

```python
# sessions/store.py
import json
import aiosqlite
import db as _db

async def create_session(
    session_id: str,
    bot_id: int,
    group_id: int,
    config: dict,
    user_message: str,
    parent_id: str | None = None,
    executor_id: str = "tool_loop_v1",
) -> str:
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO agent_sessions
               (id, parent_id, bot_id, group_id, executor_id, config_json, user_message)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, parent_id, bot_id, group_id, executor_id,
             json.dumps(config, ensure_ascii=False), user_message),
        )
        await conn.commit()
    return session_id


async def append_event(session_id: str, event_type: str, payload: dict) -> None:
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO session_events (session_id, event_type, payload) VALUES (?, ?, ?)",
            (session_id, event_type, json.dumps(payload, ensure_ascii=False)),
        )
        await conn.execute(
            "UPDATE agent_sessions SET updated_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
        await conn.commit()


async def get_session(session_id: str) -> dict | None:
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM agent_sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["config"] = json.loads(d.pop("config_json", "{}"))
    return d


async def get_events(session_id: str) -> list[dict]:
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM session_events WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.get("payload", "{}"))
        result.append(d)
    return result


async def update_session_status(session_id: str, status: str) -> None:
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        await conn.execute(
            "UPDATE agent_sessions SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, session_id),
        )
        await conn.commit()


async def get_orphaned_sessions() -> list[dict]:
    """Return all sessions with status='running' — on startup these are orphans."""
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM agent_sessions WHERE status = 'running' ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["config"] = json.loads(d.pop("config_json", "{}"))
        result.append(d)
    return result


async def add_tokens(session_id: str, input_tokens: int, output_tokens: int) -> None:
    async with aiosqlite.connect(_db.DB_PATH) as conn:
        await conn.execute(
            """UPDATE agent_sessions
               SET input_tokens  = input_tokens  + ?,
                   output_tokens = output_tokens + ?,
                   updated_at    = datetime('now')
               WHERE id = ?""",
            (input_tokens, output_tokens, session_id),
        )
        await conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -m pytest tests/test_sessions.py::TestSessionStore -v
```

Expected: 5 PASSED.

- [ ] **Step 6: Commit**

```bash
git add backend/sessions/__init__.py backend/sessions/store.py backend/tests/test_sessions.py
git commit -m "feat: add sessions/store.py with create/append/query/status DB ops"
```

---

## Task 3: Message reconstruction from event log

**Files:**
- Create: `backend/sessions/recovery.py` (reconstruction function only, no dispatch yet)
- Test: `backend/tests/test_sessions.py` (add `TestMessageReconstruction` class)

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_sessions.py`:

```python
class TestMessageReconstruction(unittest.IsolatedAsyncioTestCase):

    def _make_event(self, etype, payload):
        return {"event_type": etype, "payload": payload}

    def test_reconstruct_basic_conversation(self):
        from sessions.recovery import reconstruct_messages
        config = {"system_prompt": "You are a helper.", "provider": "deepseek"}
        events = [
            self._make_event("session_start", {"user_content": "What is 2+2?"}),
            self._make_event("llm_response", {
                "content": "It is 4.", "tool_calls": None,
                "input_tokens": 10, "output_tokens": 5,
            }),
        ]
        msgs = reconstruct_messages(config, events)
        self.assertEqual(msgs[0], {"role": "system", "content": "You are a helper."})
        self.assertEqual(msgs[1], {"role": "user", "content": "What is 2+2?"})
        self.assertEqual(msgs[2], {"role": "assistant", "content": "It is 4."})
        self.assertEqual(len(msgs), 3)

    def test_reconstruct_with_tool_calls(self):
        from sessions.recovery import reconstruct_messages
        config = {"system_prompt": "sys", "provider": "deepseek"}
        tool_call_block = [{"id": "tc1", "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"}}]
        events = [
            self._make_event("session_start", {"user_content": "Read the file"}),
            self._make_event("llm_response", {
                "content": "", "tool_calls": tool_call_block,
                "input_tokens": 5, "output_tokens": 2,
            }),
            self._make_event("tool_call", {
                "tool_call_id": "tc1", "tool_name": "read_file", "arguments": {"path": "a.txt"},
            }),
            self._make_event("tool_result", {
                "tool_call_id": "tc1", "result": "file content", "is_error": False,
            }),
            self._make_event("llm_response", {
                "content": "The file says: file content", "tool_calls": None,
                "input_tokens": 10, "output_tokens": 8,
            }),
        ]
        msgs = reconstruct_messages(config, events)
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "assistant"])
        self.assertEqual(msgs[3]["content"], "file content")
        self.assertEqual(msgs[3]["tool_call_id"], "tc1")

    def test_reconstruct_no_system_prompt(self):
        from sessions.recovery import reconstruct_messages
        config = {"system_prompt": "", "provider": "deepseek"}
        events = [
            self._make_event("session_start", {"user_content": "hello"}),
        ]
        msgs = reconstruct_messages(config, events)
        # empty system prompt still produces system message (tool_loop_v1 always has one)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")

    def test_reconstruct_skips_child_fork_events(self):
        from sessions.recovery import reconstruct_messages
        config = {"system_prompt": "s", "provider": "deepseek"}
        events = [
            self._make_event("session_start", {"user_content": "do it"}),
            self._make_event("child_fork", {"child_session_id": "c1", "skill_name": "sk"}),
            self._make_event("child_join", {"child_session_id": "c1", "result": "done"}),
            self._make_event("llm_response", {
                "content": "finished", "tool_calls": None,
                "input_tokens": 5, "output_tokens": 3,
            }),
        ]
        msgs = reconstruct_messages(config, events)
        # child_fork/child_join are metadata — they don't produce messages array entries
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles, ["system", "user", "assistant"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -m pytest tests/test_sessions.py::TestMessageReconstruction -v 2>&1 | head -20
```

Expected: FAIL — `sessions.recovery` not found.

- [ ] **Step 3: Create `backend/sessions/recovery.py`** (reconstruction only; `recover_all` stub added next task)

```python
# sessions/recovery.py
import asyncio
import json
import logging
from sessions.store import (
    get_orphaned_sessions, get_events, update_session_status,
)

log = logging.getLogger(__name__)

IDEMPOTENT_TOOLS = frozenset({
    "read_file", "list_dir", "web_search", "think", "grep",
    "get_memory", "list_files",
})


def reconstruct_messages(config: dict, events: list[dict]) -> list[dict]:
    """Rebuild the messages array from the session event log.

    Handles: session_start, llm_response, tool_result.
    Skips:   tool_call (WAL marker only), child_fork, child_join.
    """
    messages: list[dict] = [{"role": "system", "content": config.get("system_prompt", "")}]

    for ev in events:
        etype = ev["event_type"]
        p = ev["payload"]

        if etype == "session_start":
            messages.append({"role": "user", "content": p["user_content"]})

        elif etype == "llm_response":
            msg: dict = {"role": "assistant", "content": p.get("content", "")}
            if p.get("tool_calls"):
                msg["tool_calls"] = p["tool_calls"]
            messages.append(msg)

        elif etype == "tool_result":
            messages.append({
                "role": "tool",
                "tool_call_id": p["tool_call_id"],
                "name": p.get("tool_name", ""),
                "content": p["result"],
            })
        # tool_call, child_fork, child_join → metadata only, not added to messages

    return messages


async def recover_all(dispatcher=None) -> None:
    """Placeholder — wired in Task 4.

    ``dispatcher`` is a sync callable used ONLY in tests (e.g. list.append).
    In production, pass nothing — recovery tasks are created internally.
    """
    pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -m pytest tests/test_sessions.py::TestMessageReconstruction -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/sessions/recovery.py backend/tests/test_sessions.py
git commit -m "feat: add reconstruct_messages — rebuild messages array from event log"
```

---

## Task 4: `recover_all` — orphan detection and resume dispatch

**Files:**
- Modify: `backend/sessions/recovery.py` (fill in `recover_all`)
- Test: `backend/tests/test_sessions.py` (add `TestRecoverAll` class)

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_sessions.py`:

```python
class TestRecoverAll(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._orig = _db_mod.DB_PATH
        _use_test_db()
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()
        await init_db()
        import aiosqlite
        from db.migrations import run_migrations
        async with aiosqlite.connect(_TEST_DB) as db:
            await run_migrations(db)

    async def asyncTearDown(self):
        _restore_db(self._orig)
        if Path(_TEST_DB).exists():
            Path(_TEST_DB).unlink()

    async def _create_orphan(self, sid, user_msg="hello", parent_id=None):
        from sessions.store import create_session, append_event
        await create_session(
            session_id=sid, bot_id=99, group_id=1,
            config={"system_prompt": "s", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message=user_msg,
            parent_id=parent_id,
        )
        await append_event(sid, "session_start", {"user_content": user_msg})

    async def test_no_orphans_calls_nothing(self):
        from sessions.recovery import recover_all
        called = []
        await recover_all(dispatcher=called.append)
        self.assertEqual(called, [])

    async def test_completed_session_not_recovered(self):
        from sessions.store import create_session, update_session_status
        from sessions.recovery import recover_all
        await create_session(
            session_id="done1", bot_id=1, group_id=1,
            config={"system_prompt": "", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="x",
        )
        await update_session_status("done1", "completed")
        called = []
        await recover_all(dispatcher=called.append)
        self.assertEqual(called, [])

    async def test_orphan_with_only_start_event_dispatched(self):
        from sessions.recovery import recover_all
        from sessions.store import get_session
        await self._create_orphan("orph1", "hello world")
        dispatched = []
        await recover_all(dispatcher=dispatched.append)
        self.assertEqual(len(dispatched), 1)
        payload = dispatched[0]
        self.assertEqual(payload["session_id"], "orph1")
        self.assertEqual(payload["bot_id"], 99)
        self.assertEqual(payload["group_id"], 1)
        self.assertIn("messages", payload)
        # session_start gave us system + user messages
        self.assertEqual(len(payload["messages"]), 2)
        # session should now be marked as 'recovering'
        row = await get_session("orph1")
        self.assertEqual(row["status"], "recovering")

    async def test_dangling_idempotent_tool_marked_for_retry(self):
        from sessions.store import create_session, append_event
        from sessions.recovery import recover_all
        await create_session(
            session_id="idem1", bot_id=1, group_id=1,
            config={"system_prompt": "s", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="search",
        )
        await append_event("idem1", "session_start", {"user_content": "search"})
        await append_event("idem1", "tool_call", {
            "tool_call_id": "t1", "tool_name": "web_search", "arguments": {"query": "x"},
        })
        # no tool_result — process crashed before tool returned
        dispatched = []
        await recover_all(dispatcher=dispatched.append)
        # idempotent tool → still dispatch (caller will re-execute from last committed messages)
        self.assertEqual(len(dispatched), 1)
        # dangling tool_call should NOT appear in reconstructed messages
        # (we roll back to before the tool_call, let re-execution handle it)
        msgs = dispatched[0]["messages"]
        roles = [m["role"] for m in msgs]
        self.assertNotIn("tool", roles)

    async def test_dangling_side_effect_tool_marks_needs_review(self):
        from sessions.store import create_session, append_event, get_session
        from sessions.recovery import recover_all
        await create_session(
            session_id="side1", bot_id=1, group_id=1,
            config={"system_prompt": "s", "provider": "deepseek",
                    "model_name": "deepseek-chat", "temperature": 0.7, "max_tokens": 4096},
            user_message="run shell",
        )
        await append_event("side1", "session_start", {"user_content": "run shell"})
        await append_event("side1", "tool_call", {
            "tool_call_id": "t2", "tool_name": "run_shell", "arguments": {"cmd": "rm -rf /"},
        })
        dispatched = []
        await recover_all(dispatcher=dispatched.append)
        # side-effectful → should NOT be dispatched
        self.assertEqual(len(dispatched), 0)
        row = await get_session("side1")
        self.assertEqual(row["status"], "needs_review")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -m pytest tests/test_sessions.py::TestRecoverAll -v 2>&1 | head -30
```

Expected: FAIL — `recover_all` does nothing yet.

- [ ] **Step 3: Implement `recover_all` in `backend/sessions/recovery.py`**

Replace the `recover_all` stub:

```python
async def recover_all(dispatcher=None) -> None:
    """Find all orphaned sessions and attempt to resume them.

    ``dispatcher`` is a sync callable used ONLY in tests (e.g. list.append
    to inspect the recovery payload without actually dispatching bots).
    In production, call with no arguments — recovery runs as asyncio tasks
    via ``_dispatch_recovery``.

    Recovery order: children (parent_id IS NOT NULL) before parents,
    both sorted by created_at ASC so oldest are retried first.
    """
    orphans = await get_orphaned_sessions()
    if not orphans:
        return

    # Separate children and parents so children are processed first.
    children = [s for s in orphans if s.get("parent_id")]
    parents  = [s for s in orphans if not s.get("parent_id")]

    for session in children + parents:
        await _recover_one(session, dispatcher)


async def _recover_one(session: dict, dispatcher) -> None:
    sid = session["id"]
    config = session["config"]
    events = await get_events(sid)

    # Detect dangling tool_call (written before execution, no matching tool_result)
    committed_results: set[str] = {
        e["payload"]["tool_call_id"]
        for e in events if e["event_type"] == "tool_result"
    }
    dangling = [
        e for e in events
        if e["event_type"] == "tool_call"
        and e["payload"]["tool_call_id"] not in committed_results
    ]

    if dangling:
        dangling_tool = dangling[0]["payload"]["tool_name"]
        if dangling_tool not in IDEMPOTENT_TOOLS:
            # Side-effectful tool — cannot safely re-run. Park for human review.
            log.warning(
                "session %s has dangling side-effectful tool '%s', marking needs_review",
                sid, dangling_tool,
            )
            await update_session_status(sid, "needs_review")
            return
        # Idempotent tool — roll back to events before the dangling tool_call
        # so the loop will re-execute it naturally.
        cutoff_id = dangling[0]["id"]
        events = [e for e in events if e["id"] < cutoff_id]

    # Reconstruct messages from the (possibly truncated) committed events
    messages = reconstruct_messages(config, events)

    await update_session_status(sid, "recovering")
    log.info("recovering session %s (%d events, %d messages)", sid, len(events), len(messages))

    payload = {
        "session_id": sid,
        "bot_id": session["bot_id"],
        "group_id": session["group_id"],
        "config": config,
        "messages": messages,
        "parent_id": session.get("parent_id"),
        "executor_id": session.get("executor_id", "tool_loop_v1"),
    }

    if dispatcher is not None:
        # Test injection: sync callable, just collect the payload
        dispatcher(payload)
    else:
        # Production: fire and forget as an asyncio task
        asyncio.create_task(_dispatch_recovery(payload))
```

Also add `"id"` to the event dict in `store.get_events` — it's already there from `SELECT *`, verify the row includes `id`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -m pytest tests/test_sessions.py::TestRecoverAll -v
```

Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/sessions/recovery.py backend/tests/test_sessions.py
git commit -m "feat: implement recover_all — detect orphaned sessions, reconstruct messages"
```

---

## Task 5: Wire sessions into `tool_loop_v1`

**Files:**
- Modify: `backend/executors/plugins/tool_loop_v1.py`

This is the largest change. We add 6 checkpoints in `run()`:

1. `session_start` — after building `messages` and before the main loop
2. `llm_response` — after each successful `call_ai_once` that returns text or tool_calls
3. `tool_call` — **before** each `tool_executor.execute` (WAL)
4. `tool_result` — after each tool returns
5. `child_fork` — before `_run_fork_skill`
6. `child_join` — after `_run_fork_skill` returns

No tests for this task (integration behavior; covered by the recovery tests above + manual verification). Commit after manual smoke test.

- [ ] **Step 1: Add import at top of `tool_loop_v1.py`**

After the existing imports, add:

```python
import sessions
```

- [ ] **Step 2: Add session creation in `run()`, after `messages` is built (line ~303)**

Find this block (around line 303–307):

```python
        messages = list(history) + [{"role": "user", "content": user_content}]
        tool_names = [t.name for t in self.manifest.tools]
        tool_schemas = tool_executor.get_schemas(tool_names)

        temp_id = str(uuid.uuid4())
```

Replace with:

```python
        messages = list(history) + [{"role": "user", "content": user_content}]
        tool_names = [t.name for t in self.manifest.tools]
        tool_schemas = tool_executor.get_schemas(tool_names)

        temp_id = str(uuid.uuid4())
        _session_id = str(uuid.uuid4())
        _session_config = {
            "system_prompt": system_prompt,
            "provider": provider,
            "model_name": model_name,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        await sessions.create_session(
            session_id=_session_id,
            bot_id=bot["id"],
            group_id=ctx.group_id,
            config=_session_config,
            user_message=ctx.user_message,
            executor_id=self.executor_id,
        )
        await sessions.append_event(_session_id, "session_start", {
            "user_content": user_content if isinstance(user_content, str)
                            else json.dumps(user_content),
        })
```

Also add `import json` at the top of the file if not already present (check: it is not currently imported).

- [ ] **Step 3: Add `llm_response` event after each `call_ai_once` in the main loop**

In the main tool loop, find the line after `result = await call_ai_once(...)` (the second one, after overflow recovery, around line 493). Add after the `_u = result.get("usage") or {}` lines:

```python
                    # Checkpoint: record what the LLM returned
                    await sessions.append_event(_session_id, "llm_response", {
                        "content": result.get("content", ""),
                        "tool_calls": result.get("assistant_message", {}).get("tool_calls"),
                        "input_tokens": _u.get("input_tokens", 0),
                        "output_tokens": _u.get("output_tokens", 0),
                    })
                    if _u.get("input_tokens") or _u.get("output_tokens"):
                        await sessions.add_tokens(
                            _session_id,
                            input_tokens=_u.get("input_tokens", 0),
                            output_tokens=_u.get("output_tokens", 0),
                        )
```

Insert this block right after the two lines:
```python
                    _u = result.get("usage") or {}
                    _total_input_tokens += _u.get("input_tokens", 0)
                    _total_output_tokens += _u.get("output_tokens", 0)
```

- [ ] **Step 4: Add `tool_call` (before) and `tool_result` (after) events in the serial execution path**

In the serial execution loop, find the block:

```python
                                tool_result = await tool_executor.execute(
                                    call["name"], call["arguments"], context=execution_ctx
                                )
```

Replace with:

```python
                                # WAL: write tool_call BEFORE executing
                                await sessions.append_event(_session_id, "tool_call", {
                                    "tool_call_id": call["id"],
                                    "tool_name": call["name"],
                                    "arguments": call.get("arguments", {}),
                                })
                                tool_result = await tool_executor.execute(
                                    call["name"], call["arguments"], context=execution_ctx
                                )
                                await sessions.append_event(_session_id, "tool_result", {
                                    "tool_call_id": call["id"],
                                    "tool_name": call["name"],
                                    "result": tool_result,
                                    "is_error": False,
                                })
```

Do the same for the parallel execution path (around line 516–518). Find the `asyncio.gather` block and wrap each call similarly. Since parallel calls run concurrently, write all `tool_call` events before the gather, then all `tool_result` events after:

```python
                            # WAL: write all tool_call events before parallel execution
                            for call in calls:
                                await sessions.append_event(_session_id, "tool_call", {
                                    "tool_call_id": call["id"],
                                    "tool_name": call["name"],
                                    "arguments": call.get("arguments", {}),
                                })
                            raw_results = await asyncio.gather(*[
                                tool_executor.execute(c["name"], c["arguments"], context=execution_ctx)
                                for c in calls
                            ])
                            for call, tool_result in zip(calls, raw_results):
                                await sessions.append_event(_session_id, "tool_result", {
                                    "tool_call_id": call["id"],
                                    "tool_name": call["name"],
                                    "result": tool_result,
                                    "is_error": False,
                                })
```

- [ ] **Step 5: Add `child_fork` and `child_join` events around `_run_fork_skill`**

Find the block (around line 590):

```python
                                        tool_result = await _run_fork_skill(
                                            fork_info.get("content", ""),
                                            fork_task,
                                            ...
                                        )
```

Replace with:

```python
                                        child_sid = str(uuid.uuid4())
                                        await sessions.append_event(_session_id, "child_fork", {
                                            "child_session_id": child_sid,
                                            "skill_name": fork_name,
                                        })
                                        tool_result = await _run_fork_skill(
                                            fork_info.get("content", ""),
                                            fork_task,
                                            provider, fork_model, temperature,
                                            tool_schemas=fork_schemas,
                                            usage_out=_fork_usage,
                                        )
                                        await sessions.append_event(_session_id, "child_join", {
                                            "child_session_id": child_sid,
                                            "skill_name": fork_name,
                                            "result": tool_result,
                                        })
```

- [ ] **Step 6: Mark session complete/failed at end of `run()`**

After the `return ExecutionResult(full_text=full_text, msg_id=msg_id)` line at the bottom (around line 738), and in the error/cancel paths, add:

In the normal completion path (before `return ExecutionResult`):

```python
        await sessions.update_session_status(_session_id, "completed")
        return ExecutionResult(full_text=full_text, msg_id=msg_id)
```

In the `except AIError` path:

```python
        except AIError as e:
            await sessions.update_session_status(_session_id, "failed")
            await ctx.broadcaster.broadcast(...)
            return ExecutionResult(full_text="", msg_id=None)
```

In the `except asyncio.CancelledError` path:

```python
        except asyncio.CancelledError:
            await sessions.update_session_status(_session_id, "failed")
            await ctx.broadcaster.broadcast(...)
            raise
```

- [ ] **Step 7: Verify the backend starts and a Bot can send a message**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
uvicorn main:app --reload 2>&1 | head -20
```

Expected: server starts with no import errors. Send a test message to a Bot in the UI and verify it responds normally. Check that `agent_sessions` and `session_events` tables have new rows:

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python3 -c "
import asyncio, aiosqlite
async def check():
    async with aiosqlite.connect('chat.db') as db:
        async with db.execute('SELECT id, status, bot_id FROM agent_sessions LIMIT 5') as c:
            print(await c.fetchall())
        async with db.execute('SELECT session_id, event_type FROM session_events LIMIT 10') as c:
            print(await c.fetchall())
asyncio.run(check())
"
```

- [ ] **Step 8: Commit**

```bash
git add backend/executors/plugins/tool_loop_v1.py
git commit -m "feat: wire session checkpoints into tool_loop_v1 (WAL-style event log)"
```

---

## Task 6: Wire `recover_all` into startup + public `__init__.py`

**Files:**
- Modify: `backend/sessions/__init__.py`
- Modify: `backend/sessions/recovery.py` (add real dispatcher)
- Modify: `backend/main.py`

- [ ] **Step 1: Add real dispatcher to `sessions/recovery.py`**

Append to `backend/sessions/recovery.py` after `recover_all`:

```python
async def _dispatch_recovery(payload: dict) -> None:
    """Re-dispatch a recovered session into the normal orchestrator flow.

    Lazy imports to avoid circular dependency at module load time.
    """
    from db import get_db, get_member, get_members, get_messages
    from core.orchestrator import dispatch_bots
    from ws_manager import manager as ws_manager

    bot_id = payload["bot_id"]
    group_id = payload["group_id"]

    async with get_db() as db:
        bot = await get_member(db, bot_id)
        members = await get_members(db, group_id)
        history = await get_messages(db, group_id, limit=50)

    if not bot:
        log.warning("recovery: bot %d not found, skipping session %s", bot_id, payload["session_id"])
        await update_session_status(payload["session_id"], "failed")
        return

    system_sender = {
        "id": 0, "name": "系统恢复", "type": "system", "avatar_color": "#6b7280",
    }

    # Notify group that recovery is happening
    await ws_manager.broadcast(group_id, {
        "type": "message",
        "content": f"[系统] 正在恢复 {bot['name']} 的未完成任务…",
        "member_id": 0,
        "sender_name": "系统",
    })

    asyncio.create_task(dispatch_bots(
        group_id=group_id,
        bots=[bot],
        user_message=f"[任务恢复] {payload['config'].get('user_message', '')}",
        sender=system_sender,
        history=history,
        all_members=members,
    ))
```

Also add `import asyncio` at the top of `recovery.py`.

- [ ] **Step 2: Update `sessions/__init__.py`** to expose the full public API:

```python
# sessions/__init__.py
from sessions.store import (
    create_session, append_event, get_session,
    get_events, update_session_status, get_orphaned_sessions, add_tokens,
)
from sessions.recovery import recover_all

__all__ = [
    "create_session", "append_event", "get_session",
    "get_events", "update_session_status", "get_orphaned_sessions", "add_tokens",
    "recover_all",
]
```

- [ ] **Step 3: Call `recover_all` in `main.py` lifespan**

In `main.py`, add the import at the top with other module imports:

```python
import sessions
```

In the `lifespan` function, after `await scheduler.start()` and before `yield`:

```python
    await scheduler.start()
    await sessions.recover_all()  # resume any crashed sessions (no dispatcher = production mode)
    yield
```

- [ ] **Step 4: Run all session tests to confirm nothing is broken**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -m pytest tests/test_sessions.py -v
```

Expected: all tests pass (migration + store + reconstruction + recover_all).

- [ ] **Step 5: Commit**

```bash
git add backend/sessions/__init__.py backend/sessions/recovery.py backend/main.py
git commit -m "feat: wire recover_all into startup lifespan — auto-resume crashed sessions"
```

---

## Task 7: Run full test suite and verify no regressions

- [ ] **Step 1: Run all backend tests**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all existing tests still pass, new session tests pass.

- [ ] **Step 2: Check for any import errors**

```bash
cd /Users/Nuke/claudeFolder/nuke-ai-collaborator/backend
python -c "import sessions; import sessions.store; import sessions.recovery; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit if any fixes were needed; otherwise just verify clean**

```bash
git status
# if clean:
echo "All tests pass, no regressions"
```

---

## Summary

| Task | Deliverable |
|------|------------|
| 1 | `migration_004` — two new tables |
| 2 | `sessions/store.py` — raw DB ops |
| 3 | `sessions/recovery.py:reconstruct_messages` — pure function |
| 4 | `sessions/recovery.py:recover_all` — orphan detection + dispatch |
| 5 | `tool_loop_v1.py` — 6 checkpoint hooks (WAL-style) |
| 6 | `main.py` lifespan + `_dispatch_recovery` — auto-resume on startup |
| 7 | Full regression check |

**Key design invariant:** `tool_call` event is always written **before** `tool_executor.execute` is called. This guarantees that on recovery, a dangling `tool_call` (no matching `tool_result`) unambiguously means "the tool was in-flight when we crashed" — never "we crashed before the tool ran."
