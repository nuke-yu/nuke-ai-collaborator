# C-WS: Group-Domain Data over WebSocket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all group-domain reads/writes (message history, load-more, search, reactions, pins, edit, withdraw) off the broken HTTP endpoints (which query the empty central DB from the supervisor) onto the existing async WS channel, where the owning worker reads/writes the group DB and replies via the bus → upstream → fanout path.

**Architecture:** The supervisor stays a pure router — it forwards a new `query`/`mutate` WS frame downstream to the group's worker (`send_to_worker`), and the worker (which already binds the group's private DB) runs the DB op and publishes the result/update as a bus event that flows back up as a `broadcast` and fans out to the requesting browser. Reads use a request/reply correlation by `req_id` (a small WS-RPC helper on the frontend resolves a Promise when the matching `query_result` arrives); writes are fire-and-forget (the UI already updates from the existing `reaction_updated` / `pins_updated` / `message_edited` / `message_deleted` broadcasts). No new IPC reply type and no supervisor blocking are introduced — `bus.broadcast(group_id, payload)` already auto-forwards upstream.

**Tech Stack:** Python (asyncio, aiosqlite, FastAPI/WebSocket), custom IPC tunnel (`runtime/ipc`), per-process `EventBus` (`bus/engine.py`), React (frontend), pytest (backend tests).

---

## Background / Invariant Being Restored

- **Ownership invariant:** each process touches only the DB it owns. Supervisor ↔ central DB (`db/chat.db`: users/groups/members/templates/permissions/unread). Worker ↔ its per-group DBs (`workspaces/group_{id}/chat.db`: messages/reactions/pins/workflow_state/…). Neither reaches into the other's DB.
- **The bug:** HTTP endpoints in `api/messages.py` run in the supervisor process and call `get_db()`, which — with no group bound in the HTTP request context — falls back to the central DB. Group-domain tables (`messages`, `message_reactions`, `pinned_messages`) are empty there, so `GET /api/groups/{id}/messages` returns 0 rows while the group DB has the real history. Reads return empty; writes hit the wrong DB.
- **Why WS (C-WS) and not "bind group DB in HTTP" (B):** B makes the supervisor read/write group DBs (breaks ownership, cross-process writes). RPC-over-IPC (C-RPC) makes the supervisor block on a worker (against the fire-and-forget grain). C-WS reuses the existing async channels: worker does the DB op, `bus.broadcast` carries the result up, supervisor only forwards.

## Key Existing Code (read before starting)

- `runtime/ipc/protocol.py` — `DOWNSTREAM`/`UPSTREAM` type sets + `envelope(msg_type, *, group_id, trace_id=None, **fields)`.
- `runtime/worker.py:127` `Worker._handle(msg)` — dispatches downstream frames by `type`; the `USER_MESSAGE`/`CONFIRM`/`START_WORKFLOW` branches show the `lifecycle.hydrate(gid)` → `db.bind_db(db_path)` pattern.
- `runtime/dispatch.py` — worker-side handlers (`dispatch_user_message`, `dispatch_start_workflow`, `dispatch_wake_trigger`); uses `from bus import bus`.
- `bus/engine.py:68` `EventBus.broadcast(group_id, payload)` — stamps `group_id`+`trace_id` and dispatches; the worker's `_pump_upstream` (`worker.py:105`) forwards every bus event upstream as a `broadcast` frame.
- `main.py:209-259` — the WS endpoint; non-special frames go to the default `USER_MESSAGE` branch. Note it injects `member_id=member_id` from the URL.
- `db/queries.py` signatures: `get_messages(db, group_id, limit=50, before_id=None, after_time=None, after_id=None)`, `get_reactions_for_group(db, group_id)`, `get_reactions_for_message(db, message_id)`, `get_pinned_messages(db, group_id)`, `pin_message(db, group_id, message_id)`, `unpin_message(db, group_id, message_id)`, `toggle_reaction(db, message_id, member_id, emoji)`, `get_message_meta(db, msg_id)`, `update_message(db, msg_id, content, ...)`, `soft_delete_message(db, msg_id)`.
- Frontend: `frontend/src/api.js` (HTTP wrappers), `frontend/src/hooks/useWebSocket.js` (`send`/`sendRaw`), `frontend/src/components/ChatWindow.jsx` (`fetchMessages`/`loadMore`/`handleReconnect`/`handleWsMessage`), `frontend/src/components/MessageBubble.jsx:221,230` (edit/delete `fetch`).

## Out of Scope (separate issues — do NOT touch here)

- `GET /api/members/{id}/unread` (`api/messages.py:107`) — aggregates across *all* a member's groups (multi-group); unread is actually maintained in the central `unread_counts` table via `on_unread_delta`. Different problem.
- Reply targeting: `query_result` is broadcast to the whole group and filtered by globally-unique `req_id` on the client (acceptable for this trusted internal tool). Per-member targeting is a possible future tightening, not part of this plan.

## File Structure

- `backend/runtime/ipc/protocol.py` — **modify**: add `QUERY`, `MUTATE` downstream types.
- `backend/runtime/query_dispatch.py` — **create**: `dispatch_query(msg)` and `dispatch_mutate(msg)` worker-side handlers (group-DB reads/writes → bus events). Kept separate from `dispatch.py` so message/workflow dispatch stays focused.
- `backend/runtime/worker.py` — **modify**: route `QUERY`/`MUTATE` frames in `_handle`.
- `backend/main.py` — **modify**: forward incoming `query`/`mutate` WS frames to `send_to_worker`.
- `backend/api/messages.py` — **modify**: delete the now-replaced group-domain HTTP endpoints.
- `backend/tests/test_query_dispatch.py` — **create**: unit tests for `dispatch_query`/`dispatch_mutate` against a temp group DB.
- `frontend/src/wsrpc.js` — **create**: socket-agnostic WS request/reply correlation (`req_id` → Promise) + fire-and-forget `send`.
- `frontend/src/hooks/useWebSocket.js` — **modify**: register the socket with `wsrpc`, route `query_result` frames into `wsrpc`.
- `frontend/src/api.js` — **modify**: `fetchMessages`/`searchMessages`/`fetchReactions`/`fetchPins`/`toggleReaction`/`pinMessage`/`unpinMessage` go through `wsrpc`.
- `frontend/src/components/MessageBubble.jsx` — **modify**: edit/withdraw go through `wsrpc` mutate frames.

---

## Phase A — Backend: worker query/mutate plumbing

### Task 1: Add `QUERY` and `MUTATE` downstream IPC types

**Files:**
- Modify: `backend/runtime/ipc/protocol.py:8-17`
- Test: `backend/tests/test_query_dispatch.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_query_dispatch.py`:

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestIpcTypes(unittest.TestCase):
    def test_query_and_mutate_are_downstream(self):
        from runtime.ipc import protocol
        self.assertEqual(protocol.QUERY, "query")
        self.assertEqual(protocol.MUTATE, "mutate")
        self.assertIn(protocol.QUERY, protocol.DOWNSTREAM)
        self.assertIn(protocol.MUTATE, protocol.DOWNSTREAM)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_query_dispatch.py::TestIpcTypes -v`
Expected: FAIL with `AttributeError: module 'runtime.ipc.protocol' has no attribute 'QUERY'`

- [ ] **Step 3: Add the types**

In `backend/runtime/ipc/protocol.py`, after the `RELEASE_LEASE` line (line 14) and before the `DOWNSTREAM = frozenset({...})`:

```python
QUERY = "query"                          # 读 group 域数据，worker 查群库后经 bus 回 query_result
MUTATE = "mutate"                        # 写 group 域数据（反应/置顶/编辑/撤回），worker 写群库并广播更新
```

Then add both to the `DOWNSTREAM` set:

```python
DOWNSTREAM = frozenset({USER_MESSAGE, ABORT, PERMISSION_RESPONSE, CONFIRM,
                        START_WORKFLOW, WAKE_TRIGGER, RELEASE_LEASE, QUERY, MUTATE})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_query_dispatch.py::TestIpcTypes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/runtime/ipc/protocol.py backend/tests/test_query_dispatch.py
git commit -m "IPC：新增 query/mutate 下行类型（group 域读写经 WS）"
```

---

### Task 2: `dispatch_query` — messages kind (history / load-more / catch-up)

**Files:**
- Create: `backend/runtime/query_dispatch.py`
- Test: `backend/tests/test_query_dispatch.py`

`dispatch_query(msg)` runs against the currently-bound group DB (caller binds it, exactly like the worker does for `USER_MESSAGE`) and publishes a `query_result` bus event. The result shape mirrors the old HTTP endpoint: `{"messages": [...], "has_more": bool}`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_query_dispatch.py`:

```python
import tempfile
from unittest.mock import AsyncMock, patch


class QueryDispatchBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import db
        self.path = tempfile.mktemp(suffix="_group.db")
        await db.init_group_db(self.path)
        # seed 3 messages directly in the group DB
        async with db.write_connect(self.path) as conn:
            for i in range(1, 4):
                await conn.execute(
                    "INSERT INTO messages (id, group_id, member_id, content, sender_name, sender_type, sender_avatar) "
                    "VALUES (?, 1, 5, ?, 'Nuke', 'human', '#fff')",
                    (i, f"msg{i}"),
                )
            await conn.commit()

    async def asyncTearDown(self):
        import db
        await db.aclose_writer()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    async def _run(self, msg):
        """Bind the seeded group DB and run dispatch_query with bus.broadcast captured."""
        import db
        from runtime import query_dispatch
        sent = []
        with db.bind_db(self.path):
            with patch.object(query_dispatch.bus, "broadcast",
                              new=AsyncMock(side_effect=lambda gid, p: sent.append((gid, p)))):
                await query_dispatch.dispatch_query(msg)
        return sent


class TestDispatchQueryMessages(QueryDispatchBase):
    async def test_messages_returns_history_and_has_more(self):
        sent = await self._run({"type": "query", "group_id": 1, "req_id": "c1-1",
                                "query": "messages", "limit": 2})
        self.assertEqual(len(sent), 1)
        gid, payload = sent[0]
        self.assertEqual(gid, 1)
        self.assertEqual(payload["type"], "query_result")
        self.assertEqual(payload["req_id"], "c1-1")
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["data"]["messages"]), 2)
        self.assertTrue(payload["data"]["has_more"])      # 2 == limit → maybe more

    async def test_messages_before_id_paginates(self):
        sent = await self._run({"type": "query", "group_id": 1, "req_id": "c1-2",
                                "query": "messages", "before_id": 2, "limit": 50})
        ids = [m["id"] for m in sent[0][1]["data"]["messages"]]
        self.assertEqual(ids, [1])
        self.assertFalse(sent[0][1]["data"]["has_more"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_query_dispatch.py::TestDispatchQueryMessages -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'runtime.query_dispatch'`

- [ ] **Step 3: Create `backend/runtime/query_dispatch.py`**

```python
"""Worker-side handlers for group-domain reads (query) and writes (mutate).

These run inside the worker that owns the group's private DB — the caller
(Worker._handle) has already bound it via db.bind_db, exactly like USER_MESSAGE.
A read publishes a `query_result` bus event (correlated by req_id); a write
publishes the same update event the old HTTP endpoint broadcast. Both flow up
through Worker._pump_upstream as `broadcast` frames and fan out to browsers —
the supervisor never touches the group DB.
"""
import logging

import db
from bus import bus

log = logging.getLogger(__name__)


async def dispatch_query(msg: dict) -> None:
    gid = msg["group_id"]
    req_id = msg.get("req_id")
    kind = msg.get("query")
    try:
        data = await _run_query(gid, kind, msg)
        result = {"type": "query_result", "req_id": req_id, "ok": True, "data": data}
    except Exception as e:
        log.exception("query_dispatch: query=%s group=%s failed", kind, gid)
        result = {"type": "query_result", "req_id": req_id, "ok": False, "error": str(e)}
    await bus.broadcast(gid, result)


async def _run_query(gid: int, kind: str, msg: dict):
    if kind == "messages":
        limit = int(msg.get("limit") or 50)
        async with db.get_db() as conn:
            msgs = await db.get_messages(
                conn, gid, limit=limit,
                before_id=msg.get("before_id"), after_id=msg.get("after_id"),
            )
        return {"messages": msgs, "has_more": len(msgs) == limit}
    raise ValueError(f"unknown query kind: {kind!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_query_dispatch.py::TestDispatchQueryMessages -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add backend/runtime/query_dispatch.py backend/tests/test_query_dispatch.py
git commit -m "worker：dispatch_query 支持 messages（历史/分页经群库）"
```

---

### Task 3: `dispatch_query` — search / reactions / pins kinds

**Files:**
- Modify: `backend/runtime/query_dispatch.py` (`_run_query`)
- Test: `backend/tests/test_query_dispatch.py`

Note: the old search SQL JOINed the central `members` table, which does NOT exist in a self-contained group DB. The group-DB version must use the denormalized `sender_name` / `sender_type` / `sender_avatar` columns instead (the same ones `get_messages` reads).

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_query_dispatch.py`:

```python
class TestDispatchQueryOther(QueryDispatchBase):
    async def test_search_matches_content_without_members_join(self):
        sent = await self._run({"type": "query", "group_id": 1, "req_id": "c1-3",
                                "query": "search", "q": "msg2", "limit": 30})
        rows = sent[0][1]["data"]
        self.assertEqual([r["id"] for r in rows], [2])
        self.assertEqual(rows[0]["sender_name"], "Nuke")
        self.assertEqual(rows[0]["avatar_color"], "#fff")

    async def test_search_blank_returns_empty(self):
        sent = await self._run({"type": "query", "group_id": 1, "req_id": "c1-4",
                                "query": "search", "q": "   "})
        self.assertEqual(sent[0][1]["data"], [])

    async def test_reactions_and_pins_empty_by_default(self):
        s1 = await self._run({"type": "query", "group_id": 1, "req_id": "c1-5", "query": "reactions"})
        self.assertEqual(s1[0][1]["data"], {})
        s2 = await self._run({"type": "query", "group_id": 1, "req_id": "c1-6", "query": "pins"})
        self.assertEqual(s2[0][1]["data"], [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_query_dispatch.py::TestDispatchQueryOther -v`
Expected: FAIL — `_run_query` raises `unknown query kind: 'search'`

- [ ] **Step 3: Extend `_run_query`**

In `backend/runtime/query_dispatch.py`, replace the body of `_run_query` (keep the `messages` branch, add the rest):

```python
async def _run_query(gid: int, kind: str, msg: dict):
    if kind == "messages":
        limit = int(msg.get("limit") or 50)
        async with db.get_db() as conn:
            msgs = await db.get_messages(
                conn, gid, limit=limit,
                before_id=msg.get("before_id"), after_id=msg.get("after_id"),
            )
        return {"messages": msgs, "has_more": len(msgs) == limit}

    if kind == "search":
        q = (msg.get("q") or "").strip()
        if not q:
            return []
        limit = int(msg.get("limit") or 30)
        async with db.get_db() as conn:
            # group DB is self-contained: read denormalized sender_* (no members JOIN)
            cur = await conn.execute(
                "SELECT id, group_id, member_id, content, created_at, "
                "       sender_name, sender_type, sender_avatar "
                "FROM messages WHERE group_id = ? AND content LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (gid, f"%{q}%", limit),
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            created = r[4]
            if created and "Z" not in created and "+" not in created:
                created = created.replace(" ", "T") + "Z"
            out.append({"id": r[0], "group_id": r[1], "member_id": r[2], "content": r[3],
                        "created_at": created, "sender_name": r[5], "sender_type": r[6],
                        "avatar_color": r[7]})
        return out

    if kind == "reactions":
        async with db.get_db() as conn:
            return await db.get_reactions_for_group(conn, gid)

    if kind == "pins":
        async with db.get_db() as conn:
            return await db.get_pinned_messages(conn, gid)

    raise ValueError(f"unknown query kind: {kind!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_query_dispatch.py -v`
Expected: PASS (all query tests)

- [ ] **Step 5: Commit**

```bash
git add backend/runtime/query_dispatch.py backend/tests/test_query_dispatch.py
git commit -m "worker：dispatch_query 支持 search/reactions/pins（search 去 members JOIN）"
```

---

### Task 4: `dispatch_mutate` — reactions / pins / edit / withdraw (writes)

**Files:**
- Modify: `backend/runtime/query_dispatch.py`
- Test: `backend/tests/test_query_dispatch.py`

Writes are fire-and-forget: the worker writes the group DB and publishes the *same* update event the old HTTP endpoint broadcast (`reaction_updated` / `pins_updated` / `message_edited` / `message_deleted`), which the frontend already handles. `member_id` is injected by `main.py` from the authenticated WS connection (Task 5), so the author check is trustworthy.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_query_dispatch.py`:

```python
class TestDispatchMutate(QueryDispatchBase):
    async def _run_mutate(self, msg):
        import db
        from runtime import query_dispatch
        sent = []
        with db.bind_db(self.path):
            with patch.object(query_dispatch.bus, "broadcast",
                              new=AsyncMock(side_effect=lambda gid, p: sent.append((gid, p)))):
                await query_dispatch.dispatch_mutate(msg)
        return sent

    async def test_toggle_reaction_persists_and_broadcasts(self):
        sent = await self._run_mutate({"type": "mutate", "group_id": 1, "action": "toggle_reaction",
                                       "msg_id": 1, "member_id": 5, "emoji": "👍"})
        self.assertEqual(sent[0][1]["type"], "reaction_updated")
        self.assertEqual(sent[0][1]["message_id"], 1)
        self.assertIn("👍", sent[0][1]["reactions"])

    async def test_edit_rejects_non_author(self):
        sent = await self._run_mutate({"type": "mutate", "group_id": 1, "action": "edit",
                                       "msg_id": 1, "member_id": 999, "content": "hacked"})
        # not the author (5) → no broadcast, content unchanged
        self.assertEqual(sent, [])
        import db
        async with db.connect(self.path) as conn:
            cur = await conn.execute("SELECT content FROM messages WHERE id=1")
            self.assertEqual((await cur.fetchone())[0], "msg1")

    async def test_edit_by_author_broadcasts(self):
        sent = await self._run_mutate({"type": "mutate", "group_id": 1, "action": "edit",
                                       "msg_id": 1, "member_id": 5, "content": "fixed"})
        self.assertEqual(sent[0][1], {"type": "message_edited", "id": 1, "content": "fixed"})

    async def test_withdraw_by_author_broadcasts(self):
        sent = await self._run_mutate({"type": "mutate", "group_id": 1, "action": "withdraw",
                                       "msg_id": 2, "member_id": 5})
        self.assertEqual(sent[0][1], {"type": "message_deleted", "id": 2})

    async def test_pin_and_unpin_broadcast_pins_updated(self):
        s1 = await self._run_mutate({"type": "mutate", "group_id": 1, "action": "pin", "msg_id": 1})
        self.assertEqual(s1[0][1]["type"], "pins_updated")
        s2 = await self._run_mutate({"type": "mutate", "group_id": 1, "action": "unpin", "msg_id": 1})
        self.assertEqual(s2[0][1]["type"], "pins_updated")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_query_dispatch.py::TestDispatchMutate -v`
Expected: FAIL — `dispatch_mutate` not defined (`AttributeError`)

- [ ] **Step 3: Add `dispatch_mutate`**

Append to `backend/runtime/query_dispatch.py`:

```python
async def dispatch_mutate(msg: dict) -> None:
    gid = msg["group_id"]
    action = msg.get("action")
    member_id = msg.get("member_id")
    msg_id = msg.get("msg_id")
    try:
        event = await _run_mutate(gid, action, member_id, msg_id, msg)
    except Exception:
        log.exception("query_dispatch: mutate=%s group=%s failed", action, gid)
        return
    if event is not None:
        await bus.broadcast(gid, event)


async def _run_mutate(gid: int, action: str, member_id, msg_id, msg: dict):
    if action == "toggle_reaction":
        async with db.write_connect() as conn:
            meta = await db.get_message_meta(conn, msg_id)
            if not meta:
                return None
            await db.toggle_reaction(conn, msg_id, member_id, msg.get("emoji"))
            reactions = await db.get_reactions_for_message(conn, msg_id)
        return {"type": "reaction_updated", "message_id": msg_id, "reactions": reactions}

    if action == "pin":
        async with db.write_connect() as conn:
            await db.pin_message(conn, gid, msg_id)
            pins = await db.get_pinned_messages(conn, gid)
        return {"type": "pins_updated", "pins": pins}

    if action == "unpin":
        async with db.write_connect() as conn:
            await db.unpin_message(conn, gid, msg_id)
            pins = await db.get_pinned_messages(conn, gid)
        return {"type": "pins_updated", "pins": pins}

    if action == "edit":
        async with db.write_connect() as conn:
            meta = await db.get_message_meta(conn, msg_id)
            if not meta or meta["member_id"] != member_id:
                return None  # only the author may edit
            await db.update_message(conn, msg_id, msg.get("content"))
        return {"type": "message_edited", "id": msg_id, "content": msg.get("content")}

    if action == "withdraw":
        async with db.write_connect() as conn:
            meta = await db.get_message_meta(conn, msg_id)
            if not meta or meta["member_id"] != member_id:
                return None  # only the author may withdraw
            await db.soft_delete_message(conn, msg_id)
        return {"type": "message_deleted", "id": msg_id}

    raise ValueError(f"unknown mutate action: {action!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_query_dispatch.py -v`
Expected: PASS (all query + mutate tests)

- [ ] **Step 5: Commit**

```bash
git add backend/runtime/query_dispatch.py backend/tests/test_query_dispatch.py
git commit -m "worker：dispatch_mutate 支持 反应/置顶/编辑/撤回（写群库+广播更新）"
```

---

### Task 5: Route `QUERY`/`MUTATE` frames in the worker `_handle`

**Files:**
- Modify: `backend/runtime/worker.py:127-181`

Mirror the `USER_MESSAGE` branch: hydrate + bind the group DB, then call the handler.

- [ ] **Step 1: Add the branches**

In `backend/runtime/worker.py`, inside `_handle`, after the `START_WORKFLOW` branch (ends at line 154) and before `PERMISSION_RESPONSE`, insert:

```python
            elif t == ipc.protocol.QUERY:
                from runtime.lifecycle import manager as lifecycle
                db_path = await lifecycle.hydrate(gid)
                with db.bind_db(db_path):
                    from runtime.query_dispatch import dispatch_query
                    await dispatch_query(msg)
            elif t == ipc.protocol.MUTATE:
                from runtime.lifecycle import manager as lifecycle
                db_path = await lifecycle.hydrate(gid)
                with db.bind_db(db_path):
                    from runtime.query_dispatch import dispatch_mutate
                    await dispatch_mutate(msg)
```

- [ ] **Step 2: Verify nothing is broken (no new unit test — this is glue covered end-to-end in Task 12)**

Run: `cd backend && python3 -m pytest tests/test_query_dispatch.py -q`
Expected: PASS (unchanged — confirms the imports resolve)

- [ ] **Step 3: Commit**

```bash
git add backend/runtime/worker.py
git commit -m "worker：_handle 路由 query/mutate 到群库绑定的 dispatch"
```

---

### Task 6: Forward `query`/`mutate` WS frames in the supervisor (main.py)

**Files:**
- Modify: `backend/main.py:230-259` (the WS frame handling block)

The supervisor only forwards — it injects the authenticated `member_id` from the URL and uses the URL `group_id`, never trusting those from the payload (avoids the `envelope()` kwarg collision and spoofing).

- [ ] **Step 1: Add the branches**

In `backend/main.py`, inside the `while True` receive loop, after the `start_workflow` branch (ends ~line 241) and before `permission_response`, insert:

```python
                if t in ("query", "mutate"):
                    # supervisor only routes; member_id comes from the authed URL,
                    # not the client payload. Strip reserved envelope keys.
                    fields = {k: v for k, v in payload.items()
                              if k not in ("type", "group_id", "trace_id", "member_id")}
                    mtype = ipc.protocol.QUERY if t == "query" else ipc.protocol.MUTATE
                    await sup_mod.supervisor.send_to_worker(group_id, ipc.protocol.envelope(
                        mtype, group_id=group_id, trace_id=tid, member_id=member_id, **fields
                    ))
                    continue
```

- [ ] **Step 2: Smoke-check the backend boots and routes (manual)**

```bash
cd backend && pkill -KILL -f "uvicorn main:app"; pkill -KILL -f "runtime.entry --role worker"; sleep 2
nohup uvicorn main:app --host 127.0.0.1 --port 8000 > /tmp/nuke_backend.log 2>&1 &
sleep 4 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/system/status
```
Expected: `200`, and `/tmp/nuke_backend.log` shows 4 workers connected, no traceback.

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "supervisor：WS 转发 query/mutate 给 worker（member_id 取自鉴权 URL）"
```

---

## Phase B — Frontend: WS-RPC + migrate calls

### Task 7: Create the WS-RPC helper (`frontend/src/wsrpc.js`)

**Files:**
- Create: `frontend/src/wsrpc.js`

Socket-agnostic so `api.js` (plain modules, not React) can use it. `req_id` is globally unique (per-tab random prefix + counter) so one tab never resolves another tab's broadcast `query_result`.

- [ ] **Step 1: Create the module**

```javascript
// WS request/reply correlation. The supervisor broadcasts `query_result` frames
// to the whole group; we resolve only the pending request whose req_id matches
// (req_id carries a per-tab prefix, so another tab's results are simply ignored).
const _tab = Math.random().toString(36).slice(2, 8)
let _seq = 0
let _socket = null
const _pending = new Map() // req_id -> { resolve, reject, timer }

export function setSocket(ws) {
  _socket = ws
}

// Returns true if the frame was a query_result we consumed (caller should stop).
export function handleFrame(data) {
  if (data && data.type === 'query_result' && _pending.has(data.req_id)) {
    const { resolve, reject, timer } = _pending.get(data.req_id)
    clearTimeout(timer)
    _pending.delete(data.req_id)
    data.ok ? resolve(data.data) : reject(new Error(data.error || 'query failed'))
    return true
  }
  return false
}

export function request(payload, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    if (!_socket || _socket.readyState !== WebSocket.OPEN) {
      reject(new Error('socket not open'))
      return
    }
    const req_id = `${_tab}-${++_seq}`
    const timer = setTimeout(() => {
      _pending.delete(req_id)
      reject(new Error('query timeout'))
    }, timeoutMs)
    _pending.set(req_id, { resolve, reject, timer })
    _socket.send(JSON.stringify({ type: 'query', req_id, ...payload }))
  })
}

export function send(payload) {
  if (_socket && _socket.readyState === WebSocket.OPEN) {
    _socket.send(JSON.stringify(payload))
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/wsrpc.js
git commit -m "frontend：新增 wsrpc（WS 请求/响应按 req_id 关联 + 即发即忘 send）"
```

---

### Task 8: Wire `wsrpc` into `useWebSocket`

**Files:**
- Modify: `frontend/src/hooks/useWebSocket.js:25-47`

Register the socket on open; route `query_result` frames into `wsrpc` (don't pass them to the chat `onMessage` handler).

- [ ] **Step 1: Import and register**

At the top of `frontend/src/hooks/useWebSocket.js`, add:

```javascript
import * as wsrpc from '../wsrpc'
```

In `socket.onopen`, add `wsrpc.setSocket(socket)` as the first line:

```javascript
    socket.onopen = () => {
      wsrpc.setSocket(socket)
      if (reconnecting) {
        onReconnectRef.current?.()
      }
      setConnected(true)
      setReconnecting(false)
    }
```

In `socket.onmessage`, after the `auth_error` block and before `onMessageRef.current(data)`, add:

```javascript
      if (wsrpc.handleFrame(data)) return
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/hooks/useWebSocket.js
git commit -m "frontend：useWebSocket 注册 socket 到 wsrpc 并消费 query_result"
```

---

### Task 9: Migrate read calls in `api.js` to `wsrpc`

**Files:**
- Modify: `frontend/src/api.js:25-75`

Keep the exported function signatures identical so callers (`ChatWindow`) don't change. `fetchMessages` now also forwards `afterId` (the old HTTP wrapper silently dropped it — `handleReconnect` relied on it).

- [ ] **Step 1: Replace the four read wrappers**

In `frontend/src/api.js`, add the import at the top:

```javascript
import * as wsrpc from './wsrpc'
```

Replace `fetchReactions`, `searchMessages`, `fetchMessages`, `fetchPins` with:

```javascript
export async function fetchReactions(groupId) {
  return wsrpc.request({ query: 'reactions', group_id: groupId })
}

export async function searchMessages(groupId, q) {
  return wsrpc.request({ query: 'search', group_id: groupId, q })
}

export async function fetchMessages(groupId, { beforeId, afterId } = {}) {
  return wsrpc.request({
    query: 'messages', group_id: groupId, limit: 50,
    ...(beforeId ? { before_id: beforeId } : {}),
    ...(afterId ? { after_id: afterId } : {}),
  }) // resolves to { messages, has_more }
}

export async function fetchPins(groupId) {
  return wsrpc.request({ query: 'pins', group_id: groupId })
}
```

- [ ] **Step 2: Manual verification (real app)**

Restart backend (Task 6 Step 2), reload the frontend, open group 1. Expected: full chat history renders (not empty), scrolling up loads older messages, search returns matches, reactions/pins display. Confirm `/tmp/nuke_backend.log` shows no `m.meta` / `no such table` errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.js
git commit -m "frontend：messages/search/reactions/pins 读取改走 wsrpc（修历史为空）"
```

---

### Task 10: Migrate write calls (reactions / pins) in `api.js`

**Files:**
- Modify: `frontend/src/api.js:30-36,69-75`

Writes are fire-and-forget mutate frames; `member_id` is supplied by the server from the WS connection, so the client no longer sends it.

- [ ] **Step 1: Replace the write wrappers**

```javascript
export async function toggleReaction(msgId, memberId, emoji) {
  // memberId kept in the signature for caller compatibility but ignored:
  // the server uses the authenticated connection's member_id.
  wsrpc.send({ type: 'mutate', action: 'toggle_reaction', msg_id: msgId, emoji })
}

export async function pinMessage(groupId, msgId) {
  wsrpc.send({ type: 'mutate', action: 'pin', group_id: groupId, msg_id: msgId })
}

export async function unpinMessage(groupId, msgId) {
  wsrpc.send({ type: 'mutate', action: 'unpin', group_id: groupId, msg_id: msgId })
}
```

- [ ] **Step 2: Manual verification**

In the app: react to a message (emoji appears for all clients), pin/unpin a message (pin bar updates). Confirm reactions/pins persist after reload (now stored in the group DB).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.js
git commit -m "frontend：表情/置顶写入改走 wsrpc mutate（写到群库）"
```

---

### Task 11: Migrate edit/withdraw in `MessageBubble.jsx`

**Files:**
- Modify: `frontend/src/components/MessageBubble.jsx:213-231`

- [ ] **Step 1: Replace the two `fetch` calls**

Add the import near the top of `frontend/src/components/MessageBubble.jsx`:

```javascript
import * as wsrpc from '../wsrpc'
```

Replace the edit save (currently `await fetch(\`/api/messages/${msg.id}\`, { method: 'PUT', ... })` around line 221) with:

```javascript
    wsrpc.send({ type: 'mutate', action: 'edit', msg_id: msg.id, content: trimmed })
    setEditing(false)
```

Replace the withdraw (currently `await fetch(\`/api/messages/${msg.id}?member_id=...\`, { method: 'DELETE' })` around line 230) with:

```javascript
    wsrpc.send({ type: 'mutate', action: 'withdraw', msg_id: msg.id })
```

> The `currentMemberId` previously passed for the author check is now enforced server-side from the WS connection — drop it from these call sites.

- [ ] **Step 2: Manual verification**

Edit one of your own messages (updates for all clients, persists after reload); withdraw one (shows as deleted, persists). Editing another member's message must not be possible (server rejects; no broadcast).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MessageBubble.jsx
git commit -m "frontend：消息编辑/撤回改走 wsrpc mutate（作者校验移到服务端）"
```

---

## Phase C — Cleanup

### Task 12: End-to-end verification on the running app

**Files:** none (verification only)

- [ ] **Step 1: Clean restart + drive**

```bash
cd backend
pkill -KILL -f "uvicorn main:app"; pkill -KILL -f "runtime.entry --role worker"; sleep 2
nohup uvicorn main:app --host 127.0.0.1 --port 8000 > /tmp/nuke_backend.log 2>&1 &
sleep 4
```

- [ ] **Step 2: Confirm via the browser**

Open group 1. Verify, in order: (1) full history loads on open; (2) scroll-up loads older; (3) search works; (4) reactions add/remove and survive reload; (5) pin/unpin survive reload; (6) edit/withdraw your own message survives reload. Tail `/tmp/nuke_backend.log` — expect zero `no such column` / `no such table` / ASGI tracebacks.

- [ ] **Step 3: Confirm data lands in the group DB (not central)**

```bash
cd backend
echo "central (should stay 0):"; sqlite3 db/chat.db "SELECT COUNT(*) FROM message_reactions;"
echo "group_1 (should grow after reacting):"; sqlite3 workspaces/group_1/chat.db "SELECT COUNT(*) FROM message_reactions;"
```
Expected: reactions/pins counts increase in `workspaces/group_1/chat.db`, stay 0 in `db/chat.db`.

---

### Task 13: Remove the replaced group-domain HTTP endpoints

**Files:**
- Modify: `backend/api/messages.py` (delete endpoints), keep `/api/upload` and `/api/members/{id}/unread` (out of scope).

Only delete after Task 12 passes — these are now dead and broken (they query the empty central DB).

- [ ] **Step 1: Delete the endpoints**

In `backend/api/messages.py`, delete these route functions and their decorators:
- `get_pins` (`@router.get("/api/groups/{group_id}/pins")`, lines ~41-44)
- `pin_msg` (lines ~47-53)
- `unpin_msg` (lines ~56-62)
- `edit_message` (`@router.put("/api/messages/{msg_id}")`, lines ~65-73)
- `withdraw_message` (`@router.delete("/api/messages/{msg_id}")`, lines ~76-84)
- `get_group_reactions` (lines ~87-90)
- `toggle_reaction_endpoint` (lines ~93-104)
- `search_group_messages` (lines ~121-144)
- `get_group_messages` (lines ~147-151)

Keep: `upload_file` and `get_unread_counts`. Then prune now-unused imports on line 5-9 (`get_messages, get_message_meta, update_message, soft_delete_message, toggle_reaction, get_reactions_for_message, get_reactions_for_group, pin_message, unpin_message, get_pinned_messages`, `manager`, `EditMessageRequest`, `ReactionRequest`) — keep `write_connect`/`get_db` only if still referenced by the remaining two endpoints (`get_unread_counts` uses `get_db`).

- [ ] **Step 2: Verify backend still boots and full suite passes**

```bash
cd backend && python3 -m pytest -q
```
Expected: PASS (no test references the deleted endpoints; if one does, update it to the new WS path or remove it).

```bash
pkill -KILL -f "uvicorn main:app"; pkill -KILL -f "runtime.entry --role worker"; sleep 2
nohup uvicorn main:app --host 127.0.0.1 --port 8000 > /tmp/nuke_backend.log 2>&1 &
sleep 4 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/system/status
```
Expected: `200`, no import errors in the log.

- [ ] **Step 3: Commit**

```bash
git add backend/api/messages.py
git commit -m "清理：删除已被 WS 取代的 group 域 HTTP 端点（messages/search/pins/reactions/edit/withdraw）"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** read path (history/load-more/reconnect catch-up = Task 2/9; search = Task 3/9; reactions-get = Task 3/9; pins-get = Task 3/9); write path (reactions = Task 4/10; pins = Task 4/10; edit/withdraw = Task 4/11); plumbing (IPC = Task 1; worker route = Task 5; supervisor route = Task 6; frontend RPC = Task 7/8); cleanup (Task 13). Unread + reply-targeting explicitly out of scope.
- **Name consistency:** backend types `protocol.QUERY`/`protocol.MUTATE`; worker handlers `dispatch_query`/`dispatch_mutate`; bus event `type: "query_result"` with `{req_id, ok, data|error}`; mutate frame `{type:"mutate", action, msg_id, group_id?, emoji?/content?}`; frontend `wsrpc.request`/`wsrpc.send`/`wsrpc.setSocket`/`wsrpc.handleFrame`.
- **Reserved-key collision:** `main.py` strips `type`/`group_id`/`trace_id`/`member_id` from the client payload before `**fields` into `envelope()` — do not regress this (it caused the earlier `envelope() got multiple values for 'member_id'`).
- **Search:** must NOT JOIN `members` (absent in group DBs); uses denormalized `sender_*` columns.
