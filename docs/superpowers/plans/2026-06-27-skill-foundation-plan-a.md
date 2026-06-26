# Skill Foundation (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the product-grade foundation that every skill benefits from — a first-class `bot_skills`/`external_skills` data model, capability-vs-permission separation, a name-scoped `run_skill` permission fix, and the §7.5 execution-layer upgrades (real sub-agent forks, compaction survival, inline framing, `${SKILL_DIR}` portability, companion cap, `[1m]` window suffix).

**Architecture:** Two new central-DB tables (`bot_skills` = capability truth source, `external_skills` = import registry/provenance) plus a `skills/assignment.py` module that does `bot_skills` CRUD and a post-cache `filter_visible(bot_id, skills)` visibility wrapper. The permission engine gains a name-scoped match path for `run_skill` (fixing the recursive-args "blanket" bug). The skill loader/executor are enhanced in place: inline bodies are wrapped in `<skill_instructions>`, `context: fork` skills run as real attenuated multi-turn sub-agents, invoked inline bodies survive compaction via a reinject block, companion listings are capped, `${SKILL_DIR}` is normalized to forward slashes, and a `[1m]` model suffix is stripped.

**Tech Stack:** Python 3.13 · FastAPI · aiosqlite/SQLite · pytest (tests are `unittest`-style, run under `python3 -m pytest`).

## Global Constraints

- **Python interpreter is `python3`** (not `python3.11`/`python3.13`).
- **Test rhythm (backend/CLAUDE.md):** after each feature point write its unit test and run ONLY that test file. Run the full suite (`python3 -m pytest`) once before the final commit.
- **No AI co-author trailer in commits.** Commit author is `nuke`; message describes the change only. Never add `Co-Authored-By`.
- **`bot_skills` and `external_skills` are CENTRAL-DB tables** (alongside `members`/`permission_rules`). Global external skills are cross-group by definition; group-scoped ones carry a `group_id` discriminator.
- **`external_skills.group_id` uses `0` (not NULL) for global scope** so the `UNIQUE(scope_kind, group_id, name)` constraint actually fires (SQLite treats multiple NULLs as distinct).
- **Capability ≠ permission.** `bot_skills` answers "does this bot have the skill / is it enabled"; `permission_rules` answers "when called, allow/ask/deny". Never collapse the two.
- **Sub-agent security is non-negotiable:** every fork/sub-run goes through `permissions.derive_subagent_ruleset()` and runs at `spawn_depth + 1`; the engine denies `ask` when `spawn_depth > 0` (fail-safe).
- **`run_skill` execution stays in `tool_executor.execute()`** (its before-hooks fire); do NOT register a SkillToolProvider into the ToolRouter.
- DB write path is `db.write_connect(db.DB_PATH)`; central reads use `db.global_db()` — mirror `permissions/db.py` exactly.

---

### Task 1: Central DB tables `bot_skills` + `external_skills`

**Files:**
- Modify: `backend/db/schema_split.py` (add 2 DDL strings to `_CENTRAL_DDL`; add 2 names to `CENTRAL_TABLES`)
- Modify: `backend/db/migrations.py` (append `migration_024`, register it in `MIGRATIONS`)
- Test: `backend/tests/test_bot_skills.py`

**Interfaces:**
- Produces: tables `bot_skills(id, bot_id, skill_name, pool, enabled, assigned_by, assigned_at, UNIQUE(bot_id, skill_name))` and `external_skills(id, name, scope_kind, group_id, source_url, ref, commit_sha, version, platforms, high_privilege, imported_by, imported_at, status, UNIQUE(scope_kind, group_id, name))` in the central DB. Created on fresh central DBs via `_CENTRAL_DDL` and on legacy/replayed DBs via `migration_024`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_bot_skills.py`:

```python
"""Plan A — Task 1/2/3: bot_skills + external_skills tables and assignment module."""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db
from db.schema_split import init_central_db


def _run(coro):
    return asyncio.run(coro)


class TestSkillTables(unittest.TestCase):
    def test_tables_and_columns_created(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        async def go():
            await init_central_db(path)
            async with _db.connect(path) as conn:
                cur = await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                tables = {r[0] for r in await cur.fetchall()}
                cur = await conn.execute("PRAGMA table_info(bot_skills)")
                bs_cols = {r[1] for r in await cur.fetchall()}
                cur = await conn.execute("PRAGMA table_info(external_skills)")
                ex_cols = {r[1] for r in await cur.fetchall()}
            return tables, bs_cols, ex_cols

        try:
            tables, bs_cols, ex_cols = _run(go())
        finally:
            os.unlink(path)

        self.assertIn("bot_skills", tables)
        self.assertIn("external_skills", tables)
        self.assertEqual(
            bs_cols,
            {"id", "bot_id", "skill_name", "pool", "enabled", "assigned_by", "assigned_at"},
        )
        self.assertTrue(
            {"id", "name", "scope_kind", "group_id", "source_url", "ref",
             "commit_sha", "version", "platforms", "high_privilege",
             "imported_by", "imported_at", "status"}.issubset(ex_cols)
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_bot_skills.py::TestSkillTables -v`
Expected: FAIL — `bot_skills`/`external_skills` not in `tables`.

- [ ] **Step 3: Add the DDL to `_CENTRAL_DDL` and the table names to `CENTRAL_TABLES`**

In `backend/db/schema_split.py`, add to the `CENTRAL_TABLES` frozenset:

```python
CENTRAL_TABLES = frozenset({
    "users", "groups", "members", "role_templates", "permission_rules", "cron_jobs",
    "unread_counts", "bot_skills", "external_skills",
})
```

Append these two DDL strings to the end of the `_CENTRAL_DDL` list (after the `unread_counts` entry, before the closing `]`):

```python
    # bot_skills: per-bot capability/assignment truth source (Plan A). enabled
    # toggles visibility WITHOUT removing the assignment; assigned_by/at audit it.
    # Separate from permission_rules (which only gates HIL at call time).
    """CREATE TABLE IF NOT EXISTS bot_skills (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id      INTEGER NOT NULL,
        skill_name  TEXT    NOT NULL,
        pool        TEXT    NOT NULL DEFAULT 'external_global',
        enabled     INTEGER NOT NULL DEFAULT 1,
        assigned_by INTEGER,
        assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(bot_id, skill_name),
        FOREIGN KEY (bot_id) REFERENCES members(id)
    )""",
    # external_skills: import registry — provenance + lifecycle truth source.
    # group_id uses 0 (NOT NULL) for global scope so UNIQUE actually fires
    # (SQLite treats multiple NULLs as distinct).
    """CREATE TABLE IF NOT EXISTS external_skills (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL,
        scope_kind  TEXT    NOT NULL DEFAULT 'global',
        group_id    INTEGER NOT NULL DEFAULT 0,
        source_url  TEXT,
        ref         TEXT,
        commit_sha  TEXT,
        version     TEXT,
        platforms   TEXT,
        high_privilege TEXT,
        imported_by INTEGER,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status      TEXT    NOT NULL DEFAULT 'active',
        UNIQUE(scope_kind, group_id, name)
    )""",
```

- [ ] **Step 4: Add `migration_024` so legacy/replayed DBs catch up**

In `backend/db/migrations.py`, add this function immediately after `migration_023` and before the `MIGRATIONS` list:

```python
async def migration_024(db):
    """Plan A: create bot_skills (capability) + external_skills (import registry).

    Both are central-domain tables. CREATE IF NOT EXISTS is idempotent and
    harmless if it also runs on a group DB (the tables stay empty there).

    Rollback:
        DROP TABLE IF EXISTS bot_skills;
        DROP TABLE IF EXISTS external_skills;
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_skills (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id      INTEGER NOT NULL,
            skill_name  TEXT    NOT NULL,
            pool        TEXT    NOT NULL DEFAULT 'external_global',
            enabled     INTEGER NOT NULL DEFAULT 1,
            assigned_by INTEGER,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bot_id, skill_name),
            FOREIGN KEY (bot_id) REFERENCES members(id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS external_skills (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            scope_kind  TEXT    NOT NULL DEFAULT 'global',
            group_id    INTEGER NOT NULL DEFAULT 0,
            source_url  TEXT,
            ref         TEXT,
            commit_sha  TEXT,
            version     TEXT,
            platforms   TEXT,
            high_privilege TEXT,
            imported_by INTEGER,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status      TEXT    NOT NULL DEFAULT 'active',
            UNIQUE(scope_kind, group_id, name)
        )
    """)
    await db.commit()
```

Then add `migration_024,` to the end of the `MIGRATIONS` list (after `migration_023,`).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_bot_skills.py::TestSkillTables -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/db/schema_split.py backend/db/migrations.py backend/tests/test_bot_skills.py
git commit -m "feat(skills): add bot_skills + external_skills central tables"
```

---

### Task 2: `assignment.py` — `bot_skills` CRUD

**Files:**
- Create: `backend/skills/assignment.py`
- Test: `backend/tests/test_bot_skills.py` (append `TestAssignmentCRUD`)

**Interfaces:**
- Consumes: `db.global_db()`, `db.write_connect(db.DB_PATH)`, central `bot_skills` table (Task 1).
- Produces:
  - `async set_assignment(bot_id: int, skill_name: str, pool: str, enabled: bool = True, assigned_by: int | None = None) -> None` — upsert one assignment.
  - `async remove_assignment(bot_id: int, skill_name: str) -> None`
  - `async list_assignments(bot_id: int) -> list[dict]` — `[{"skill_name","pool","enabled"(bool),"assigned_by"}, ...]`
  - `async enabled_skill_names(bot_id: int) -> set[str]`
  - constant `EXTERNAL_LAYERS = {"external_global", "external_group"}`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_bot_skills.py` (before the `if __name__` block), and add the import `import db.context as _ctx` is NOT needed; use monkeypatch of `db.DB_PATH`:

```python
class TestAssignmentCRUD(unittest.TestCase):
    def _fresh_db(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        return path

    def test_upsert_list_remove_and_enabled_set(self):
        from skills import assignment
        path = self._fresh_db()
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            # bot_skills.bot_id has an FK to members → create a bot row first.
            async with _db.write_connect(path) as conn:
                await conn.execute(
                    "INSERT INTO members (id, group_id, name, type) "
                    "VALUES (1, 1, 'dev', 'bot')"
                )
                await conn.commit()

            await assignment.set_assignment(1, "deploy", "external_global",
                                            enabled=True, assigned_by=42)
            await assignment.set_assignment(1, "lint", "external_group",
                                            enabled=False)
            rows = await assignment.list_assignments(1)
            enabled = await assignment.enabled_skill_names(1)

            # Upsert: flip 'lint' to enabled, change nothing else.
            await assignment.set_assignment(1, "lint", "external_group", enabled=True)
            enabled_after = await assignment.enabled_skill_names(1)

            await assignment.remove_assignment(1, "deploy")
            rows_after = await assignment.list_assignments(1)
            return rows, enabled, enabled_after, rows_after

        try:
            _db.DB_PATH = path
            rows, enabled, enabled_after, rows_after = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        by_name = {r["skill_name"]: r for r in rows}
        self.assertEqual(by_name["deploy"]["pool"], "external_global")
        self.assertTrue(by_name["deploy"]["enabled"])
        self.assertFalse(by_name["lint"]["enabled"])
        self.assertEqual(enabled, {"deploy"})
        self.assertEqual(enabled_after, {"deploy", "lint"})
        self.assertEqual({r["skill_name"] for r in rows_after}, {"lint"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_bot_skills.py::TestAssignmentCRUD -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.assignment'`.

- [ ] **Step 3: Create `backend/skills/assignment.py`**

```python
"""bot_skills capability/assignment store (Plan A).

The single truth source for "which external skills does this bot have, and is
each enabled". DELIBERATELY separate from permission_rules: this answers
capability/visibility; permission_rules answers call-time HIL (allow/ask/deny).

Central-DB table (see db/schema_split.py). Reads via db.global_db(); writes via
db.write_connect(db.DB_PATH) — mirrors permissions/db.py.
"""
import db as _db

# Skill layers whose visibility is gated by bot_skills. Non-external layers
# (system/group/role/learned) are always visible and never filtered here.
EXTERNAL_LAYERS = {"external_global", "external_group"}


async def set_assignment(bot_id: int, skill_name: str, pool: str,
                         enabled: bool = True, assigned_by: int | None = None) -> None:
    """Insert or update one assignment (UNIQUE(bot_id, skill_name))."""
    async with _db.write_connect(_db.DB_PATH) as db:
        await db.execute(
            """INSERT INTO bot_skills (bot_id, skill_name, pool, enabled, assigned_by)
               VALUES (?,?,?,?,?)
               ON CONFLICT(bot_id, skill_name) DO UPDATE SET
                   pool=excluded.pool,
                   enabled=excluded.enabled,
                   assigned_by=excluded.assigned_by""",
            (bot_id, skill_name, pool, 1 if enabled else 0, assigned_by),
        )
        await db.commit()


async def remove_assignment(bot_id: int, skill_name: str) -> None:
    async with _db.write_connect(_db.DB_PATH) as db:
        await db.execute(
            "DELETE FROM bot_skills WHERE bot_id=? AND skill_name=?",
            (bot_id, skill_name),
        )
        await db.commit()


async def list_assignments(bot_id: int) -> list[dict]:
    async with _db.global_db() as db:
        async with db.execute(
            "SELECT skill_name, pool, enabled, assigned_by "
            "FROM bot_skills WHERE bot_id=? ORDER BY skill_name",
            (bot_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {"skill_name": r[0], "pool": r[1], "enabled": bool(r[2]), "assigned_by": r[3]}
        for r in rows
    ]


async def enabled_skill_names(bot_id: int) -> set[str]:
    async with _db.global_db() as db:
        async with db.execute(
            "SELECT skill_name FROM bot_skills WHERE bot_id=? AND enabled=1",
            (bot_id,),
        ) as cur:
            return {r[0] for r in await cur.fetchall()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_bot_skills.py::TestAssignmentCRUD -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/assignment.py backend/tests/test_bot_skills.py
git commit -m "feat(skills): bot_skills assignment CRUD module"
```

---

### Task 3: `assignment.py` — `filter_visible` post-cache visibility wrapper

**Files:**
- Modify: `backend/skills/assignment.py` (add `filter_visible`)
- Test: `backend/tests/test_bot_skills.py` (append `TestFilterVisible`)

**Interfaces:**
- Consumes: `enabled_skill_names(bot_id)`, `EXTERNAL_LAYERS` (Task 2).
- Produces: `async filter_visible(bot_id: int, skills: list[dict]) -> list[dict]` — drops entries whose `layer` is in `EXTERNAL_LAYERS` and whose `name` is not in the bot's enabled set; passes all other entries through unchanged. Lazily hits the DB only if at least one external-layer entry is present (so the common Plan-A case — no external skills yet — costs zero queries).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_bot_skills.py`:

```python
class TestFilterVisible(unittest.TestCase):
    def test_external_filtered_by_enabled_others_passthrough(self):
        from skills import assignment
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        skills = [
            {"name": "write-spec", "layer": "system"},
            {"name": "code-review", "layer": "role"},
            {"name": "deploy", "layer": "external_global"},
            {"name": "lint", "layer": "external_group"},
            {"name": "secret", "layer": "external_global"},
        ]

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute(
                    "INSERT INTO members (id, group_id, name, type) VALUES (1,1,'dev','bot')"
                )
                await conn.commit()
            await assignment.set_assignment(1, "deploy", "external_global", enabled=True)
            await assignment.set_assignment(1, "lint", "external_group", enabled=True)
            # 'secret' is NOT assigned → must be filtered out.
            return await assignment.filter_visible(1, skills)

        try:
            _db.DB_PATH = path
            visible = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        names = {s["name"] for s in visible}
        self.assertEqual(names, {"write-spec", "code-review", "deploy", "lint"})

    def test_no_external_layers_does_no_db_work(self):
        from skills import assignment
        skills = [{"name": "x", "layer": "system"}, {"name": "y", "layer": "learned"}]
        # No external entries → must not touch the DB (DB_PATH points nowhere here).
        out = _run(assignment.filter_visible(999, skills))
        self.assertEqual(out, skills)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_bot_skills.py::TestFilterVisible -v`
Expected: FAIL — `AttributeError: module 'skills.assignment' has no attribute 'filter_visible'`.

- [ ] **Step 3: Add `filter_visible` to `backend/skills/assignment.py`**

Append:

```python
async def filter_visible(bot_id: int, skills: list[dict]) -> list[dict]:
    """Drop external-layer skills not enabled for this bot; pass others through.

    Runs OUTSIDE the mtime-signature scan cache (the cache returns ALL external
    skills; visibility is a per-bot DB fact that must not be cached by file
    signature). Only queries the DB when an external-layer entry is present.
    """
    enabled: set[str] | None = None
    out: list[dict] = []
    for s in skills:
        if s.get("layer") in EXTERNAL_LAYERS:
            if enabled is None:
                enabled = await enabled_skill_names(bot_id)
            if s.get("name") not in enabled:
                continue
        out.append(s)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_bot_skills.py::TestFilterVisible -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/assignment.py backend/tests/test_bot_skills.py
git commit -m "feat(skills): filter_visible post-cache visibility wrapper"
```

---

### Task 4: name-scoped `run_skill` in `synthesize_args_pattern`

**Files:**
- Modify: `backend/permissions/patterns.py:71-85` (`synthesize_args_pattern`)
- Test: `backend/tests/test_permission_patterns.py` (append cases to `TestSynthesizeArgsPattern`)

**Interfaces:**
- Consumes: existing `_escape_glob` (patterns.py).
- Produces: `synthesize_args_pattern("run_skill", {"name": "build", "args": "..."})` returns `"build"` (glob-escaped), not `""`. Other tools unchanged.

- [ ] **Step 1: Write the failing test**

Append two methods inside `TestSynthesizeArgsPattern` in `backend/tests/test_permission_patterns.py`:

```python
    def test_run_skill_scoped_to_name(self):
        self.assertEqual(
            synthesize_args_pattern("run_skill", {"name": "build", "args": "deploy prod"}),
            "build",
        )

    def test_run_skill_empty_name_blanket(self):
        self.assertEqual(synthesize_args_pattern("run_skill", {"args": "x"}), "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_permission_patterns.py::TestSynthesizeArgsPattern -v`
Expected: FAIL — `test_run_skill_scoped_to_name` gets `""`, expected `"build"`.

- [ ] **Step 3: Add the `run_skill` branch**

In `backend/permissions/patterns.py`, inside `synthesize_args_pattern`, add the branch before the final `return ""`:

```python
    if tool_name == "run_skill":
        # Scope to the skill NAME only. The freeform `args` task string must not
        # widen the rule (recursive-args matching would let an "always allow
        # deploy" rule fire on run_skill(name="build", args="deploy ...")).
        name = arguments.get("name") or ""
        return _escape_glob(str(name)) if name else ""
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_permission_patterns.py::TestSynthesizeArgsPattern -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/permissions/patterns.py backend/tests/test_permission_patterns.py
git commit -m "fix(permissions): synthesize name-scoped run_skill args_pattern"
```

---

### Task 5: name-scoped `run_skill` matching in `engine._matches`

**Files:**
- Modify: `backend/permissions/engine.py:83-89` (`_matches`)
- Test: `backend/tests/test_permission_patterns.py` (append `TestRunSkillNameScopedMatch`)

**Interfaces:**
- Consumes: `fnmatch` (already imported in engine.py).
- Produces: `_matches` for `run_skill` matches `args_pattern` against ONLY the `name` argument, never recursively over the `args` task string. All other tools keep the existing recursive `_match_args_pattern` behavior.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_permission_patterns.py` (after `TestScopedRuleMatching`):

```python
class TestRunSkillNameScopedMatch(unittest.TestCase):
    """A run_skill rule scoped to skill 'deploy' must match ONLY by name, never
    by the freeform args string (the recursive-args blanket bug)."""

    def test_matches_by_name(self):
        rule = Rule(tool_pattern="run_skill", args_pattern="deploy", action="allow")
        self.assertTrue(engine._matches(rule, "run_skill", {"name": "deploy", "args": ""}))

    def test_does_not_match_via_args_string(self):
        rule = Rule(tool_pattern="run_skill", args_pattern="deploy", action="allow")
        self.assertFalse(
            engine._matches(rule, "run_skill", {"name": "build", "args": "deploy prod"})
        )

    def test_empty_args_pattern_still_matches_any_skill(self):
        rule = Rule(tool_pattern="run_skill", args_pattern="", action="allow")
        self.assertTrue(engine._matches(rule, "run_skill", {"name": "anything", "args": ""}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_permission_patterns.py::TestRunSkillNameScopedMatch -v`
Expected: FAIL — `test_does_not_match_via_args_string` is True (recursive match hits `args`).

- [ ] **Step 3: Add the name-scoped path to `_matches`**

In `backend/permissions/engine.py`, just above `def _matches`, add the map:

```python
# Tools whose permission args_pattern matches a SINGLE identifying field, not a
# recursive search across every argument value. run_skill's `args` is freeform
# task text; recursive matching would let a name-scoped rule fire on the wrong
# skill (e.g. rule "deploy" firing on run_skill(name="build", args="deploy ...")).
_NAME_SCOPED_TOOLS = {"run_skill": "name"}
```

Then replace the body of `_matches`:

```python
def _matches(rule: Rule, tool_name: str, arguments: dict) -> bool:
    if not _match_tool_pattern(tool_name, rule.tool_pattern):
        return False
    if rule.args_pattern:
        field = _NAME_SCOPED_TOOLS.get(tool_name)
        if field is not None:
            return fnmatch.fnmatch(str(arguments.get(field, "")), rule.args_pattern)
        args_json = json.dumps(arguments, sort_keys=True, default=str)
        return _match_args_pattern(rule.args_pattern, args_json)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_permission_patterns.py -v`
Expected: PASS (all classes in the file, including the pre-existing shell ones).

- [ ] **Step 5: Commit**

```bash
git add backend/permissions/engine.py backend/tests/test_permission_patterns.py
git commit -m "fix(permissions): match run_skill rules by name field only"
```

---

### Task 6: inline `<skill_instructions>` framing in `run_skill`

**Files:**
- Modify: `backend/skills/loader.py:86-169` (`run_skill` — wrap the inline return)
- Test: `backend/tests/test_skill_loader_planA.py` (new)

**Interfaces:**
- Consumes: existing `run_skill` plumbing.
- Produces: an INLINE `run_skill` result is wrapped as `<skill_instructions>\n{body}\n\n现在请按以上技能描述的步骤开始执行。\n</skill_instructions>`. The `context: fork` path (returns `"__SKILL_FORK__"`) is NOT wrapped. The fork's stored `content` is NOT wrapped.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_skill_loader_planA.py`:

```python
"""Plan A — loader enhancements: inline framing, companion cap, SKILL_DIR norm."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(coro):
    return asyncio.run(coro)


class TestInlineFraming(unittest.IsolatedAsyncioTestCase):
    async def test_inline_body_wrapped_in_skill_instructions(self):
        from unittest.mock import patch
        from skills import loader

        skill_dir = Path("/tmp/nuke_skill_x")
        entry = {
            "name": "demo", "type": "md", "path": skill_dir / "SKILL.md",
            "description": "d", "context": "inline",
        }

        async def fake_list(*a, **k):
            return [entry]

        with patch.object(loader, "list_skills_all", new=fake_list), \
             patch("pathlib.Path.exists", lambda self: True), \
             patch("pathlib.Path.read_text", lambda self, encoding="utf-8": "BODY-TEXT"):
            out = await loader.run_skill(1, "demo", "", ctx={"group_id": 1})

        self.assertTrue(out.startswith("<skill_instructions>"))
        self.assertIn("BODY-TEXT", out)
        self.assertTrue(out.rstrip().endswith("</skill_instructions>"))
```

> Note: `run_skill` lists companions by iterating `skill_dir`; the test's `path.name == "SKILL.md"` triggers that branch. To keep the test isolated from the filesystem, the patched `Path.exists` returns True but the companion `iterdir` would fail — so this skill entry uses `SKILL.md`. Guard against that in Step 3 by wrapping the companion `iterdir` in the existing `if path.name == "SKILL.md"` block, which already runs; patch `Path.iterdir` to return `[]` as well. Update the `with` to also patch iterdir:
>
> ```python
>              patch("pathlib.Path.iterdir", lambda self: iter([])), \
> ```
> Add that line inside the `with` block.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_loader_planA.py::TestInlineFraming -v`
Expected: FAIL — output does not start with `<skill_instructions>`.

- [ ] **Step 3: Wrap the inline return in `run_skill`**

In `backend/skills/loader.py`, the function currently ends with `return content` (line ~169). Replace that final `return content` with:

```python
    # Inline framing: tell the model this is an instruction set to execute now.
    # The fork path returned "__SKILL_FORK__" earlier, so it is never wrapped.
    return (
        "<skill_instructions>\n"
        f"{content}\n"
        "\n现在请按以上技能描述的步骤开始执行。\n"
        "</skill_instructions>"
    )
```

(Do not change the `return "__SKILL_FORK__"` line inside the `context == "fork"` branch.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_loader_planA.py::TestInlineFraming -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/loader.py backend/tests/test_skill_loader_planA.py
git commit -m "feat(skills): frame inline run_skill body in <skill_instructions>"
```

---

### Task 7: companion file-listing cap in `run_skill`

**Files:**
- Modify: `backend/skills/loader.py:136-147` (companion listing)
- Test: `backend/tests/test_skill_loader_planA.py` (append `TestCompanionCap`)

**Interfaces:**
- Consumes: existing companion block.
- Produces: at most `_MAX_COMPANION_FILES = 10` companion paths listed; when more exist, an overflow note `  …还有 N 个文件（已省略）` is appended instead of the full list.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_skill_loader_planA.py`:

```python
class TestCompanionCap(unittest.IsolatedAsyncioTestCase):
    async def test_companion_listing_capped(self):
        from unittest.mock import patch
        from skills import loader

        skill_dir = Path("/tmp/nuke_skill_y")
        companions = [skill_dir / f"f{i}.py" for i in range(25)]
        entry = {"name": "big", "type": "md", "path": skill_dir / "SKILL.md",
                 "description": "d", "context": "inline"}

        async def fake_list(*a, **k):
            return [entry]

        def fake_iterdir(self):
            return iter(companions)

        with patch.object(loader, "list_skills_all", new=fake_list), \
             patch("pathlib.Path.exists", lambda self: True), \
             patch("pathlib.Path.read_text", lambda self, encoding="utf-8": "BODY"), \
             patch("pathlib.Path.iterdir", new=fake_iterdir):
            out = await loader.run_skill(1, "big", "", ctx={"group_id": 1})

        # Only 10 listed; an overflow note for the remaining 15.
        self.assertEqual(out.count("/tmp/nuke_skill_y/f"), 10)
        self.assertIn("还有 15 个文件", out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_loader_planA.py::TestCompanionCap -v`
Expected: FAIL — all 25 listed, no overflow note.

- [ ] **Step 3: Cap the companion list**

In `backend/skills/loader.py`, add a module constant near the top (after `_MAX_ALWAYS_TOTAL_CHARS`):

```python
_MAX_COMPANION_FILES = 10           # cap directory-skill companion listing
```

Replace the companion block (the `if path.name == "SKILL.md":` body) with:

```python
    # Companion files (directory skills only)
    if path.name == "SKILL.md":
        companions = sorted(
            f for f in skill_dir.iterdir()
            if f.name != "SKILL.md" and not f.name.startswith('.')
        )
        if companions:
            shown = companions[:_MAX_COMPANION_FILES]
            lines = [f"  {f}" for f in shown]
            overflow = len(companions) - len(shown)
            if overflow > 0:
                lines.append(f"  …还有 {overflow} 个文件（已省略）")
            file_list = "\n".join(lines)
            content += (
                f"\n\n<skill_files>\n{file_list}\n</skill_files>"
                "\nRelative paths in this skill are relative to the base directory above."
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_loader_planA.py::TestCompanionCap -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/loader.py backend/tests/test_skill_loader_planA.py
git commit -m "feat(skills): cap companion file listing at 10 with overflow note"
```

---

### Task 8: `${SKILL_DIR}` forward-slash normalization (Windows portability)

**Files:**
- Modify: `backend/skills/processor.py:98-114` (`process_skill_content` — normalize the dir string)
- Modify: `backend/skills/loader.py:133` (base-dir header uses the normalized string)
- Test: `backend/tests/test_skill_loader_planA.py` (append `TestSkillDirNormalization`)

**Interfaces:**
- Consumes: `process_skill_content(content, skill_dir, ...)`.
- Produces: every `${SKILL_DIR}` substitution and the `Base directory for this skill:` header use a forward-slash path even when `skill_dir` is a Windows path (backslashes → `/`). Behavior on POSIX paths is unchanged.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_skill_loader_planA.py`:

```python
class TestSkillDirNormalization(unittest.IsolatedAsyncioTestCase):
    async def test_skill_dir_backslashes_normalized(self):
        from skills.processor import process_skill_content
        out = await process_skill_content(
            "see ${SKILL_DIR}/scripts/run.ps1",
            "C:\\workspaces\\group_1\\skills\\demo",
        )
        self.assertEqual(out, "see C:/workspaces/group_1/skills/demo/scripts/run.ps1")
        self.assertNotIn("\\", out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_loader_planA.py::TestSkillDirNormalization -v`
Expected: FAIL — backslashes survive (`str(skill_dir)` is unmodified).

- [ ] **Step 3: Normalize in `process_skill_content` and the loader header**

In `backend/skills/processor.py`, change the `${SKILL_DIR}` substitution line inside `process_skill_content`:

```python
    content = substitute_arguments(content, args)
    # Normalize to forward slashes so Windows skill dirs (backslashes) work in
    # cross-platform skills the same as POSIX ones.
    content = content.replace("${SKILL_DIR}", str(skill_dir).replace("\\", "/"))
    if template_vars:
        content = render_sandboxed(content, template_vars)
    return content
```

In `backend/skills/loader.py`, change the base-dir header line (currently `content = f"Base directory for this skill: {skill_dir}\n\n{raw}"`) to use the normalized form:

```python
    skill_dir_norm = str(skill_dir).replace("\\", "/")
    content = f"Base directory for this skill: {skill_dir_norm}\n\n{raw}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_loader_planA.py -v`
Expected: PASS (all loader Plan-A classes).

- [ ] **Step 5: Commit**

```bash
git add backend/skills/processor.py backend/skills/loader.py backend/tests/test_skill_loader_planA.py
git commit -m "feat(skills): normalize \${SKILL_DIR} to forward slashes for Windows"
```

---

### Task 9: `[1m]` context-window suffix stripping

**Files:**
- Modify: `backend/skills/metadata.py` (add `strip_context_window_suffix`)
- Modify: `backend/skills/loader.py:155-167` (apply when storing `skill_model` and fork `model`)
- Modify: `backend/executors/plugins/tool_loop_v1.py:232` (defensive strip at consumption)
- Test: `backend/tests/test_skill_frontmatter.py` (append `TestModelWindowSuffix`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `metadata.strip_context_window_suffix(model: str) -> str` — drops a trailing `[1m]` long-context marker and surrounding whitespace; returns other strings unchanged. The loader stores already-stripped model ids into `ctx["skill_model"]` and `ctx["skill_fork"]["model"]`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_skill_frontmatter.py` (top-level class; mirror that file's existing style):

```python
class TestModelWindowSuffix(unittest.TestCase):
    def test_strips_1m_suffix(self):
        from skills.metadata import strip_context_window_suffix
        self.assertEqual(strip_context_window_suffix("claude-opus-4-8[1m]"), "claude-opus-4-8")
        self.assertEqual(strip_context_window_suffix("claude-opus-4-8 [1m]"), "claude-opus-4-8")

    def test_leaves_plain_model_untouched(self):
        from skills.metadata import strip_context_window_suffix
        self.assertEqual(strip_context_window_suffix("deepseek-chat"), "deepseek-chat")
        self.assertEqual(strip_context_window_suffix(""), "")
```

> If `test_skill_frontmatter.py` does not already `import unittest`, add it at the top.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_frontmatter.py::TestModelWindowSuffix -v`
Expected: FAIL — `ImportError: cannot import name 'strip_context_window_suffix'`.

- [ ] **Step 3: Add the helper and wire it in**

In `backend/skills/metadata.py`, add (after `_is_safe_name`):

```python
def strip_context_window_suffix(model: str) -> str:
    """Drop a trailing [1m] long-context-window marker from a skill's model id.

    Authors may write `model: claude-opus-4-8[1m]` to request the 1M-token
    window; we strip the marker so the bare id resolves normally (the window
    request itself is not yet wired into providers).
    """
    m = (model or "").strip()
    if m.endswith("[1m]"):
        return m[:-4].strip()
    return m
```

In `backend/skills/loader.py`, in the `if ctx is not None:` block, change the fork and model side-effects to strip:

```python
        if skill_entry.get("context") == "fork":
            ctx["skill_fork"] = {
                "name": name,
                "content": content,
                "args": args,
                "allowed_tools": skill_entry.get("allowed_tools", []),
                "model": strip_context_window_suffix(skill_entry.get("model", "")),
            }
            return "__SKILL_FORK__"
        if skill_entry.get("allowed_tools"):
            ctx["skill_allowed_tools"] = skill_entry["allowed_tools"]
        if skill_entry.get("model"):
            ctx["skill_model"] = strip_context_window_suffix(skill_entry["model"])
```

Add the import at the top of `loader.py` (it already imports from `.metadata`):

```python
from .metadata import skill_path, parse_skill_meta, strip_context_window_suffix
```

In `backend/executors/plugins/tool_loop_v1.py`, line ~232, make consumption defensive (in case a model id reaches here unstripped):

```python
                    from skills.metadata import strip_context_window_suffix
                    _iter_model = strip_context_window_suffix(
                        self.execution_ctx.pop("skill_model", None) or self.model_name
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_frontmatter.py::TestModelWindowSuffix -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/metadata.py backend/skills/loader.py backend/executors/plugins/tool_loop_v1.py backend/tests/test_skill_frontmatter.py
git commit -m "feat(skills): strip [1m] context-window suffix from skill model id"
```

---

### Task 10: fork skills run as real attenuated sub-agents

**Files:**
- Modify: `backend/executors/plugins/tool_loop_v1_helpers.py:168-193` (`_run_fork_skill` — multi-turn + attenuation)
- Modify: `backend/executors/plugins/tool_loop_v1.py:163-169` (pass attenuation kwargs)
- Test: `backend/tests/test_skill_fork_subagent.py` (new); existing `backend/tests/test_fork_skill_usage.py` must still pass.

**Interfaces:**
- Consumes: `permissions.derive_subagent_ruleset`, `config.SPAWN_MAX_DEPTH`, `executors.tool_dispatch.dispatch_tool`, `AIService.call`.
- Produces: new keyword-only params on `_run_fork_skill`:
  `_run_fork_skill(skill_content, task, provider, model, temperature, ai_service, tool_schemas=None, *, parent_ruleset=None, spawn_depth=0, group_id=None, bot_id=None, broadcaster=None, max_iter=8) -> str`.
  - With no `tool_schemas`: single call; if the model requests tools, returns a "未声明 allowed_tools" notice (never silently executes).
  - With `tool_schemas`: real multi-turn loop, tools dispatched through `dispatch_tool` with a child context carrying `spawn_depth + 1` and `derive_subagent_ruleset(parent_ruleset)`.
  - At `spawn_depth >= SPAWN_MAX_DEPTH`: refuses with a depth notice.
  - Positional call shape `(content, task, provider, model, temp, ai_service)` is preserved (back-compat). Passing `usage_out=` still raises `TypeError`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_skill_fork_subagent.py`:

```python
"""Plan A §7.5.2 — fork skills run as real attenuated multi-turn sub-agents."""
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.tool_loop_v1 import _run_fork_skill


class _StubAI:
    """Returns queued results from .call(); records nothing else."""
    def __init__(self, results):
        self._results = list(results)

    async def call(self, *a, **k):
        return self._results.pop(0)


class TestForkSubagent(unittest.IsolatedAsyncioTestCase):
    async def test_multi_turn_dispatches_tools_with_attenuated_child_ctx(self):
        ai = _StubAI([
            {"type": "tool_calls",
             "assistant_message": {"role": "assistant", "content": "", "tool_calls": []},
             "calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "x"}}]},
            {"type": "text", "content": "fork final"},
        ])
        captured = {}

        async def fake_dispatch(name, args, ctx):
            captured["name"] = name
            captured["spawn_depth"] = ctx.get("spawn_depth")
            captured["has_ruleset_key"] = "ruleset" in ctx
            return ("file-bytes", False)

        with patch("executors.tool_dispatch.dispatch_tool", new=fake_dispatch):
            out = await _run_fork_skill(
                "skill body", "do it", "deepseek", "deepseek-chat", 0.7, ai,
                tool_schemas=[{"function": {"name": "read_file"}}],
                parent_ruleset=None, spawn_depth=0, group_id=1, bot_id=7,
                broadcaster=None,
            )

        self.assertEqual(out, "fork final")
        self.assertEqual(captured["name"], "read_file")
        self.assertEqual(captured["spawn_depth"], 1)   # child runs one level deeper
        self.assertTrue(captured["has_ruleset_key"])   # attenuated ruleset threaded

    async def test_no_tools_requested_returns_notice_not_silent_exec(self):
        ai = _StubAI([
            {"type": "tool_calls",
             "assistant_message": {"role": "assistant", "content": ""},
             "calls": [{"id": "c1", "name": "run_shell", "arguments": {"cmd": "rm -rf /"}}]},
        ])
        out = await _run_fork_skill(
            "body", "task", "deepseek", "deepseek-chat", 0.7, ai,
            tool_schemas=None,
        )
        self.assertIn("allowed_tools", out)
        self.assertIn("run_shell", out)

    async def test_depth_cap_refuses(self):
        ai = _StubAI([{"type": "text", "content": "should not run"}])
        out = await _run_fork_skill(
            "body", "task", "deepseek", "deepseek-chat", 0.7, ai,
            spawn_depth=999,
        )
        self.assertIn("最大深度", out)

    async def test_rejects_legacy_usage_out_kwarg(self):
        ai = _StubAI([{"type": "text", "content": "x"}])
        with self.assertRaises(TypeError):
            await _run_fork_skill("b", "t", "deepseek", "deepseek-chat", 0.7, ai,
                                  usage_out=[])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_fork_subagent.py -v`
Expected: FAIL — current `_run_fork_skill` is single-shot, returns the "(fork 不支持多轮工具循环)" placeholder, and has no `spawn_depth` param.

- [ ] **Step 3: Rewrite `_run_fork_skill`**

In `backend/executors/plugins/tool_loop_v1_helpers.py`, replace the whole `_run_fork_skill` function (lines ~168-193) with:

```python
async def _run_fork_skill(
    skill_content: str,
    task: str,
    provider: str,
    model: str,
    temperature: float,
    ai_service: AIService,
    tool_schemas: list | None = None,
    *,
    parent_ruleset=None,
    spawn_depth: int = 0,
    group_id: int | None = None,
    bot_id: int | None = None,
    broadcaster=None,
    max_iter: int = 8,
) -> str:
    """Execute a fork skill as a real, attenuated sub-agent.

    Security contract (§7.5.2):
      - refuses past SPAWN_MAX_DEPTH (prevents skill→skill explosion);
      - child tools run at spawn_depth+1 through the permission pipeline with a
        ruleset attenuated by derive_subagent_ruleset (bypass not propagated,
        blanket high-risk allows dropped; the engine denies `ask` at depth>0);
      - tool gating: no declared tool_schemas → single call, and if the model
        still requests tools we return a notice rather than executing anything.
    Tokens roll into the parent ai_service.usage (canonical accumulator).
    """
    if spawn_depth >= config.SPAWN_MAX_DEPTH:
        return f"[fork skill 已达最大深度 {config.SPAWN_MAX_DEPTH}，拒绝执行]"

    child_ctx = {
        "bot_id": bot_id,
        "group_id": group_id,
        "spawn_depth": spawn_depth + 1,
        "ruleset": permissions.derive_subagent_ruleset(parent_ruleset),
        "broadcaster": broadcaster,
    }
    messages = [{"role": "user", "content": task or "请执行此技能。"}]

    from executors.tool_dispatch import dispatch_tool
    for _ in range(max_iter):
        try:
            result = await ai_service.call(
                skill_content, messages, model, provider, temperature, 4096,
                tools=tool_schemas or None, auto_compact=False,
            )
        except Exception as e:
            return f"[fork skill 执行错误] {e}"

        if result["type"] == "text":
            return result["content"]
        if result["type"] != "tool_calls":
            return f"[fork skill 返回了非文本类型: {result['type']}]"

        calls = result.get("calls", [])
        if not tool_schemas:
            names = ", ".join(c["name"] for c in calls)
            return f"[fork skill 请求工具 {names} 但未声明 allowed_tools，已拒绝]"

        messages.append(result["assistant_message"])
        for call in calls:
            out, _is_err = await dispatch_tool(
                call["name"], call.get("arguments", {}), child_ctx
            )
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "name": call["name"],
                "content": out,
            })

    return "[fork skill 达到最大迭代次数，未完成]"
```

`permissions` and `config` are already imported at the top of `tool_loop_v1_helpers.py`.

- [ ] **Step 4: Pass attenuation kwargs from the caller**

In `backend/executors/plugins/tool_loop_v1.py`, in `_handle_run_skill_result`, change the `_run_fork_skill(...)` call (lines ~163-169) to:

```python
            tool_result = await _run_fork_skill(
                fork_info.get("content", ""),
                fork_task,
                self.provider, fork_model, self.temperature,
                self.ai_service,
                tool_schemas=fork_schemas,
                parent_ruleset=self.ruleset,
                spawn_depth=self.ctx.spawn_depth,
                group_id=self.ctx.group_id,
                bot_id=self.bot["id"],
                broadcaster=self.ctx.interaction,
            )
```

- [ ] **Step 5: Run tests to verify they pass (new + back-compat)**

Run: `cd backend && python3 -m pytest tests/test_skill_fork_subagent.py tests/test_fork_skill_usage.py -v`
Expected: PASS for both files (back-compat: positional call returns text; `usage_out=` raises TypeError).

- [ ] **Step 6: Commit**

```bash
git add backend/executors/plugins/tool_loop_v1_helpers.py backend/executors/plugins/tool_loop_v1.py backend/tests/test_skill_fork_subagent.py
git commit -m "feat(skills): run fork skills as attenuated multi-turn sub-agents"
```

---

### Task 11: invoked inline skills survive compaction

**Files:**
- Modify: `backend/executors/plugins/tool_loop_v1_helpers.py` (add `build_invoked_skills_block`; include it in `build_reinject`)
- Modify: `backend/executors/plugins/tool_loop_v1.py` (init `self.invoked_skills`; register in the run_skill branch — see note)
- Modify: `backend/executors/plugins/tool_loop_v1_helpers.py:615-617` (register invoked inline body in `execute_serial_tools`)
- Test: `backend/tests/test_skill_compaction_survival.py` (new)

**Interfaces:**
- Consumes: `runner.invoked_skills: dict[str, str]`.
- Produces:
  - `build_invoked_skills_block(invoked_skills: dict[str, str], budget: int = 6000) -> str` — renders the most-recent invoked inline skill bodies as `<active_skill name="...">...</active_skill>` blocks within a char budget; returns `""` when empty.
  - `build_reinject(runner)` output includes that block, so after micro-compact/auto-compact clears the run_skill tool message, the skill body is re-injected (reinject_fn fires on pre-run and overflow recovery).
  - `execute_serial_tools` records inline skill bodies (those starting with `<skill_instructions>`) into `runner.invoked_skills[name]`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_skill_compaction_survival.py`:

```python
"""Plan A §7.5.3 — invoked inline skill bodies survive compaction via reinject."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.tool_loop_v1_helpers import build_invoked_skills_block


class TestInvokedSkillsBlock(unittest.TestCase):
    def test_empty_returns_blank(self):
        self.assertEqual(build_invoked_skills_block({}), "")

    def test_renders_active_skill_blocks(self):
        block = build_invoked_skills_block({"deploy": "<skill_instructions>STEP-A</skill_instructions>"})
        self.assertIn('<active_skill name="deploy">', block)
        self.assertIn("STEP-A", block)
        self.assertTrue(block.rstrip().endswith("</active_skill>"))

    def test_budget_truncates_oldest_first(self):
        inv = {f"s{i}": "X" * 5000 for i in range(5)}  # 25k of bodies
        block = build_invoked_skills_block(inv, budget=6000)
        # Only the most-recent entries fit the 6000-char budget.
        self.assertLessEqual(len(block), 6000 + 200)  # +small framing overhead
        self.assertIn('name="s4"', block)             # newest kept
        self.assertNotIn('name="s0"', block)          # oldest dropped


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_compaction_survival.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_invoked_skills_block'`.

- [ ] **Step 3: Add `build_invoked_skills_block` and wire into `build_reinject`**

In `backend/executors/plugins/tool_loop_v1_helpers.py`, add this function (near `build_reinject`, e.g. just above it):

```python
def build_invoked_skills_block(invoked_skills: dict, budget: int = 6000) -> str:
    """Render invoked inline skill bodies for reinjection after compaction.

    Newest-first within a char budget so a long-running task keeps its active
    skill instructions even after the run_skill tool message is micro-compacted.
    """
    if not invoked_skills:
        return ""
    parts: list[str] = []
    remaining = budget
    for name, body in reversed(list(invoked_skills.items())):
        if remaining <= 0:
            break
        snippet = body[:remaining]
        parts.append(f'<active_skill name="{name}">\n{snippet}\n</active_skill>')
        remaining -= len(snippet)
    return "\n\n".join(parts)
```

Then change `build_reinject` to include the block. Its current body is:

```python
async def build_reinject(runner) -> str:
    fresh_prefix, _ = await runner._get_fresh_context_prefix()
    ft_xml = compact.build_file_tracker_xml(runner.file_tracker)
    file_contents = compact.build_file_contents_for_reinject(
        runner.file_tracker, workspace_dir=str(_bot_ws(runner.bot["id"], runner.ctx.group_id))
    )
    parts = [p for p in [fresh_prefix, ft_xml, file_contents] if p]
    return "\n\n".join(parts)
```

Replace the `parts = ...` line with:

```python
    invoked = build_invoked_skills_block(getattr(runner, "invoked_skills", {}))
    parts = [p for p in [fresh_prefix, invoked, ft_xml, file_contents] if p]
```

- [ ] **Step 4: Initialize and populate `runner.invoked_skills`**

In `backend/executors/plugins/tool_loop_v1.py`, in `ToolLoopRunner.__init__`, add near the other state fields (after `self.file_tracker = {}`):

```python
        self.invoked_skills = {}   # name -> inline skill body, for compaction survival
```

In `backend/executors/plugins/tool_loop_v1_helpers.py`, in `execute_serial_tools`, the run_skill branch currently is:

```python
        if call["name"] == "run_skill":
            tool_result = await runner._handle_run_skill_result(tool_result)
```

Replace it with:

```python
        if call["name"] == "run_skill":
            tool_result = await runner._handle_run_skill_result(tool_result)
            # Pin inline skill bodies so they survive micro/auto-compaction
            # (run_skill is in _MICROCOMPACT_TOOLS; its tool message gets cleared).
            _sname = call.get("arguments", {}).get("name")
            if _sname and isinstance(tool_result, str) and tool_result.startswith("<skill_instructions>"):
                runner.invoked_skills[_sname] = tool_result
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_compaction_survival.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/executors/plugins/tool_loop_v1_helpers.py backend/executors/plugins/tool_loop_v1.py backend/tests/test_skill_compaction_survival.py
git commit -m "feat(skills): reinject invoked inline skill bodies to survive compaction"
```

---

### Task 12: Full-suite regression + final commit

**Files:** none (verification only).

- [ ] **Step 1: Run the skill + permissions + executor test families**

Run: `cd backend && python3 -m pytest tests/test_bot_skills.py tests/test_permission_patterns.py tests/test_permissions.py tests/test_subagent_perms.py tests/test_skill_loader_planA.py tests/test_skill_frontmatter.py tests/test_skill_fork_subagent.py tests/test_fork_skill_usage.py tests/test_skill_compaction_survival.py tests/test_compact.py -v`
Expected: PASS

- [ ] **Step 2: Run the full suite (pre-commit regression gate)**

Run: `cd backend && python3 -m pytest`
Expected: PASS (no new failures introduced by Plan A). Investigate and fix any regression before proceeding.

- [ ] **Step 3: Confirm clean tree**

Run: `git status`
Expected: all Plan A changes already committed in Tasks 1–11; nothing uncommitted except (optionally) this plan doc.

---

## Self-Review

**Spec coverage (§11 Plan A scope):**
- `bot_skills`/`external_skills` 建表 → Task 1.
- 分配/可见性分离 (`bot_skills` CRUD + `available(bot)`) → Tasks 2, 3.
- name-scoped 权限 (修 blanket bug) → Tasks 4 (synth) + 5 (engine match).
- §7.5 执行层: fork→子 agent + `derive_subagent_ruleset` + `spawn_depth` → Task 10; compaction 保活 → Task 11; inline 包装 → Task 6; `[1m]` → Task 9.
- companion 上限 → Task 7.
- `${SKILL_DIR}` 归一化 → Task 8.
- All Plan A rows of spec §8 ("计划 = A") are covered. (`backend/db/schema*.py`, `assignment.py`, `permissions/patterns.py`, `permissions/engine.py`, `tool_loop_v1*.py`, `loader.py` — all touched.)

**Out of scope (correctly deferred to Plan B/C):** `layout.external_*_dir`, `ExternalPoolSource`, `discovery`/`composer` external-layer wiring, `importer.py`/`registry.py`, frontmatter `shell`/`platforms`/`version` parsing, import/assignment APIs + UI (Plan B); `migrate_skill_assignment.py` (Plan C). `filter_visible` is the seam Plan B plugs the external layers into — it is fully built and tested here even though no external layer emits entries yet.

**Placeholder scan:** every code step contains complete code; every test step contains runnable assertions; every run step has an exact command + expected result. No TBD/TODO/"handle edge cases".

**Type/name consistency:** `set_assignment`/`remove_assignment`/`list_assignments`/`enabled_skill_names`/`filter_visible`/`EXTERNAL_LAYERS` are used identically across Tasks 2–3 and the tests. `strip_context_window_suffix` is defined in Task 9 and imported in loader + tool_loop_v1. `build_invoked_skills_block`/`runner.invoked_skills` are consistent across Task 11. `_run_fork_skill`'s new signature matches both the caller (Task 10 Step 4) and all tests (Task 10 Step 1 + existing `test_fork_skill_usage.py`).

**Known cross-DB note:** `migration_024`'s `CREATE TABLE IF NOT EXISTS` also runs against group DBs via `ensure_group_db_ready`→`run_migrations`; the tables stay empty there and are harmless. The authoritative copies live in the central DB (`CENTRAL_TABLES` + `_CENTRAL_DDL`), which is where `assignment.py` reads/writes.
