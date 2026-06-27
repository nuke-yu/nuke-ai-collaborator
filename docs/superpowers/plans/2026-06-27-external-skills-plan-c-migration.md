# External Skills (Plan C — Migration) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `scripts/migrate_skill_assignment.py` — a non-silent, dry-run-first, idempotent migration that tightens legacy **blanket `run_skill` allow** permission rules into explicit **name-scoped** allow rules per bot, closing the hole where a blanket allow would silently auto-approve execution of newly-imported external skills (Plan B). Plus a release note.

**Architecture:** Read the central DB's `permission_rules`. A "blanket run_skill allow" is a row with `action='allow'`, empty `args_pattern` (matches any skill name → allow-all), and a `tool_pattern` that targets `run_skill` specifically. For each affected bot, enumerate the skills it can currently run (`list_skills_all(bot_id, group_id, role)`), synthesize one **name-scoped** allow rule per skill (`synthesize_args_pattern("run_skill", {"name": s})`, reusing the engine's own scoping so the result matches exactly what the engine checks), then delete the blanket rule. Pure planning (read-only) is split from apply (writes) for clean test boundaries. Default dry-run; `--apply` writes; backup is the operator's responsibility (aligns with `migrate_role_skills` / `migrate_workspace_layout`).

**Tech Stack:** Python 3.13 · aiosqlite/SQLite (central DB) · `permissions.db` / `permissions.patterns` · `skills.discovery.list_skills_all` · pytest (`unittest`-style, run under `python3 -m pytest`).

## Global Constraints

- **Python interpreter is `python3`.**
- **Test rhythm (backend/CLAUDE.md):** after each feature point write its unit test and run ONLY that test file. Run the full suite (`python3 -m pytest`) once before the final commit.
- **No AI co-author trailer in commits.** Author is `nuke`; message describes the change only. Never add `Co-Authored-By`.
- **Builds on Plan A + Plan B (already on this branch):** `permission_rules` central table; `permissions.db.{load_rules,save_rule,delete_rule}`; `permissions.patterns.synthesize_args_pattern`; engine `_NAME_SCOPED_TOOLS = {"run_skill": "name"}`; `skills.discovery.list_skills_all`. `bot_skills`/`external_skills` exist (Plan A/B).
- **Migration is a first-class, NON-silent action (spec §10):** prints a per-bot plan in dry-run; prints what changed on apply; ships a release note. Never mutate the DB without `--apply`.
- **Idempotent + safe re-run (spec §10 / project `migrate_*` convention):** re-running skips bots with no blanket rule and skips name-scoped rules already present; a bot with no blanket rule is untouched; "no affected bots" is a clean no-op exit 0.
- **DB:** `permission_rules` is central. Reads via `db.global_db()`; writes via `db.write_connect(db.DB_PATH)` (already encapsulated by `permissions.db`). Tests monkeypatch `db.DB_PATH` to a temp file.

### Design decision — `bot_skills` is NOT populated for current skills (deliberate deviation from spec §10's literal "+bot_skills 分配")

Spec §10 says expand the blanket into "显式 `bot_skills` 分配 + 必要的 name-scoped permission". This plan implements **only the name-scoped permission** expansion, by design:

- `assignment.filter_visible` gates **external-layer skills only**; system/group/role/learned skills are unconditionally visible. At migration time there are **zero** external skills, and every skill a blanket allow covered is non-external. So writing those into `bot_skills` has **no visibility effect**.
- Worse, it would be actively harmful: Plan B Task 10's `_reconcile_bot_skills` (the assignment panel's `PUT`) treats `bot_skills` as the **external-pool** assignment truth and deletes any row the panel didn't re-send. Polluting `bot_skills` with non-external skills would surface them in `get_member_skills`'s `assigned` list and get them silently wiped on the next panel save.
- The blanket allow was a **permission/HIL** fact (skip approval), not a capability/visibility fact. The behavior-preserving, hole-closing action is therefore entirely in `permission_rules`.

`bot_skills` stays external-skill-only. This is the one decision to confirm at plan review; everything else follows the spec verbatim.

---

### Task 1: blanket-rule detection + per-bot expansion plan (read-only)

**Files:**
- Create: `backend/scripts/migrate_skill_assignment.py` (detection + planning only)
- Test: `backend/tests/test_migrate_skill_assignment.py`

**Interfaces:**
- Consumes: `permissions.db.load_rules`, `permissions.patterns.synthesize_args_pattern`, `skills.discovery.list_skills_all`, `permissions.models.Rule`.
- Produces:
  - `is_blanket_run_skill_rule(rule: Rule) -> bool` — True iff `rule.action == "allow"` and `rule.args_pattern == ""` and `rule.tool_pattern in _RUN_SKILL_PATTERNS` (`{"run_skill", "run_skill*"}`). Pure wildcard `*`/`**` allow-all-tools rules are intentionally excluded (broad operator policy, not a skill-assignment artifact).
  - `async plan_for_bot(bot_id, group_id, role) -> dict` — returns `{"bot_id", "blanket_rule_ids": [int...], "add_patterns": [str...], "skipped_existing": [str...]}`. `add_patterns` = name-scoped patterns for each runnable skill that does NOT already have a name-scoped allow rule; `skipped_existing` = those already covered. Empty `blanket_rule_ids` ⇒ nothing to do.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_migrate_skill_assignment.py`:

```python
"""Plan C Task 1 — blanket run_skill rule detection + per-bot expansion plan."""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db
from db.schema_split import init_central_db


def _run(coro):
    return asyncio.run(coro)


class TestDetection(unittest.TestCase):
    def test_is_blanket_run_skill_rule(self):
        from scripts.migrate_skill_assignment import is_blanket_run_skill_rule
        from permissions.models import Rule
        self.assertTrue(is_blanket_run_skill_rule(Rule(tool_pattern="run_skill", args_pattern="", action="allow")))
        self.assertTrue(is_blanket_run_skill_rule(Rule(tool_pattern="run_skill*", args_pattern="", action="allow")))
        # name-scoped already → not blanket
        self.assertFalse(is_blanket_run_skill_rule(Rule(tool_pattern="run_skill", args_pattern="deploy", action="allow")))
        # deny → not in scope
        self.assertFalse(is_blanket_run_skill_rule(Rule(tool_pattern="run_skill", args_pattern="", action="deny")))
        # allow-all-tools wildcard → intentionally excluded
        self.assertFalse(is_blanket_run_skill_rule(Rule(tool_pattern="*", args_pattern="", action="allow")))


class TestPlanForBot(unittest.TestCase):
    def test_plan_expands_only_uncovered_skills(self):
        from scripts import migrate_skill_assignment as M
        from permissions import db as pdb

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def fake_list(bot_id, group_id=None, role=None):
            return [{"name": "deploy"}, {"name": "lint"}]

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type, role) VALUES (7,1,'dev','bot','developer')")
                await conn.commit()
            # one blanket rule + a pre-existing name-scoped allow for 'lint'
            await pdb.save_rule(7, "run_skill", "", "allow")
            await pdb.save_rule(7, "run_skill", "lint", "allow")
            with patch.object(M, "list_skills_all", new=fake_list):
                return await M.plan_for_bot(7, 1, "developer")

        try:
            _db.DB_PATH = path
            plan = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        self.assertEqual(len(plan["blanket_rule_ids"]), 1)
        self.assertEqual(plan["add_patterns"], ["deploy"])     # 'lint' already covered
        self.assertEqual(plan["skipped_existing"], ["lint"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_migrate_skill_assignment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.migrate_skill_assignment'`.

- [ ] **Step 3: Create `backend/scripts/migrate_skill_assignment.py` (detection + planning)**

```python
# backend/scripts/migrate_skill_assignment.py
"""一次性迁移：把旧的 blanket `run_skill` allow 权限规则收紧为按技能名的
name-scoped allow 规则。沿用 migrate_role_skills / migrate_workspace_layout 的纪律。

背景（spec §10）：Plan A 把 `synthesize_args_pattern` 从 blanket 改成 name-scoped。
旧的「always allow 某技能」其实在引擎里被存成 args_pattern='' = 放行**所有**技能名。
Plan B 上线 external skill 导入后，这种 blanket allow 会**静默自动批准**新导入的
（未受信）外部技能执行。本迁移把每个 bot 的 blanket run_skill allow 展开为它**当时
实际可运行**的各技能的显式 name-scoped allow，再删掉 blanket 规则——保留既有技能的
免审批，同时堵住「自动放行未来/导入技能」的洞。

bot_skills 不在本迁移内填充：filter_visible 只 gate 外部层技能，迁移时无外部技能，
且 Plan B 的分配面板 reconcile 会把非外部行清掉（见 plan 设计说明）。

安全约定：
- **跑前停机 + 备份中央 DB**（脚本不替你备份）。
- 默认 dry-run：只打印计划，不动盘。加 --apply 才执行。
- 幂等：无 blanket 规则的 bot 跳过；已存在的 name-scoped 规则跳过；可重复运行。

用法：
    python3 -m scripts.migrate_skill_assignment            # dry-run
    python3 -m scripts.migrate_skill_assignment --apply    # 执行
"""
from __future__ import annotations
import sys

import db as _db
from permissions import db as pdb
from permissions.models import Rule
from permissions.patterns import synthesize_args_pattern
from skills.discovery import list_skills_all

# tool_pattern 值视为「针对 run_skill 的 blanket」。纯通配 '*'/'**'（放行全部工具）
# 是 operator 有意的宽策略，不在本技能迁移范围内，故排除。
_RUN_SKILL_PATTERNS = {"run_skill", "run_skill*"}


def is_blanket_run_skill_rule(rule: Rule) -> bool:
    return (
        rule.action == "allow"
        and rule.args_pattern == ""
        and rule.tool_pattern in _RUN_SKILL_PATTERNS
    )


async def plan_for_bot(bot_id: int, group_id: int | None, role: str | None) -> dict:
    """只读：算出该 bot 的展开计划，不写盘。"""
    rules = await pdb.load_rules(bot_id)
    blanket_ids = [r.id for r in rules if is_blanket_run_skill_rule(r)]
    plan = {"bot_id": bot_id, "blanket_rule_ids": blanket_ids,
            "add_patterns": [], "skipped_existing": []}
    if not blanket_ids:
        return plan

    # 已存在的 name-scoped allow（args_pattern 非空）→ 不重复加。
    existing = {r.args_pattern for r in rules
                if r.action == "allow" and r.tool_pattern in _RUN_SKILL_PATTERNS
                and r.args_pattern}

    skills = await list_skills_all(bot_id, group_id=group_id, role=role)
    seen: set[str] = set()
    for s in skills:
        pat = synthesize_args_pattern("run_skill", {"name": s["name"]})
        if not pat or pat in seen:
            continue
        seen.add(pat)
        if pat in existing:
            plan["skipped_existing"].append(pat)
        else:
            plan["add_patterns"].append(pat)
    return plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_migrate_skill_assignment.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_skill_assignment.py backend/tests/test_migrate_skill_assignment.py
git commit -m "feat(migrate): detect blanket run_skill allow rules + per-bot expansion plan"
```

---

### Task 2: apply the expansion (idempotent writes)

**Files:**
- Modify: `backend/scripts/migrate_skill_assignment.py` (add `apply_for_bot` + `migrate`)
- Test: `backend/tests/test_migrate_skill_assignment.py` (append `TestApply`)

**Interfaces:**
- Consumes: `plan_for_bot` (Task 1), `permissions.db.{save_rule,delete_rule}`, `db.global_db` (to enumerate bots).
- Produces:
  - `async apply_for_bot(bot_id, group_id, role) -> dict` — runs `plan_for_bot`; for each `add_patterns` entry `save_rule(bot_id, "run_skill", pattern, "allow")`; then `delete_rule(id)` for each blanket id; returns the plan dict augmented with `{"added": int, "deleted": int}`. Idempotent: a second run finds no blanket rule and writes nothing.
  - `async _load_bots() -> list[tuple[int, int, str|None]]` — `(id, group_id, role)` for every `members.type='bot'`.
  - `async migrate(apply: bool) -> dict` — enumerate bots, build each plan; on `apply` call `apply_for_bot`; return `{"bots": [plan...], "apply": apply, "total_added": int, "total_deleted": int}`. Only bots with a blanket rule appear in `bots`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_migrate_skill_assignment.py` (before `if __name__`):

```python
class TestApply(unittest.TestCase):
    def test_apply_expands_then_idempotent(self):
        from scripts import migrate_skill_assignment as M
        from permissions import db as pdb

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def fake_list(bot_id, group_id=None, role=None):
            return [{"name": "deploy"}, {"name": "lint"}]

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type, role) VALUES (7,1,'dev','bot','developer')")
                await conn.commit()
            await pdb.save_rule(7, "run_skill", "", "allow")   # blanket
            with patch.object(M, "list_skills_all", new=fake_list):
                first = await M.migrate(apply=True)
                rules_after = await pdb.load_rules(7)
                # second run is a clean no-op
                second = await M.migrate(apply=True)
            return first, rules_after, second

        try:
            _db.DB_PATH = path
            first, rules_after, second = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        # blanket gone; two name-scoped allows added
        patterns = sorted(r.args_pattern for r in rules_after if r.action == "allow")
        self.assertEqual(patterns, ["deploy", "lint"])
        self.assertFalse(any(r.args_pattern == "" for r in rules_after))
        self.assertEqual(first["total_added"], 2)
        self.assertEqual(first["total_deleted"], 1)
        # idempotent: nothing left to migrate
        self.assertEqual(second["bots"], [])
        self.assertEqual(second["total_added"], 0)


class TestDryRunNoWrite(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        from scripts import migrate_skill_assignment as M
        from permissions import db as pdb

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def fake_list(bot_id, group_id=None, role=None):
            return [{"name": "deploy"}]

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type, role) VALUES (7,1,'dev','bot','developer')")
                await conn.commit()
            await pdb.save_rule(7, "run_skill", "", "allow")
            with patch.object(M, "list_skills_all", new=fake_list):
                res = await M.migrate(apply=False)
            rules_after = await pdb.load_rules(7)
            return res, rules_after

        try:
            _db.DB_PATH = path
            res, rules_after = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        # plan computed but DB untouched (blanket still there, no new rules)
        self.assertEqual(len(res["bots"]), 1)
        self.assertEqual(res["bots"][0]["add_patterns"], ["deploy"])
        self.assertEqual(res["total_added"], 0)
        self.assertTrue(any(r.args_pattern == "" for r in rules_after))
        self.assertEqual(len([r for r in rules_after if r.args_pattern == "deploy"]), 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_migrate_skill_assignment.py::TestApply tests/test_migrate_skill_assignment.py::TestDryRunNoWrite -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'migrate'`.

- [ ] **Step 3: Add apply + migrate to `backend/scripts/migrate_skill_assignment.py`**

Append after `plan_for_bot`:

```python
async def apply_for_bot(bot_id: int, group_id: int | None, role: str | None) -> dict:
    """写盘：按 plan 加 name-scoped allow，再删 blanket。幂等。"""
    plan = await plan_for_bot(bot_id, group_id, role)
    if not plan["blanket_rule_ids"]:
        return {**plan, "added": 0, "deleted": 0}
    for pat in plan["add_patterns"]:
        await pdb.save_rule(bot_id, "run_skill", pat, "allow")
    for rid in plan["blanket_rule_ids"]:
        await pdb.delete_rule(rid)
    return {**plan, "added": len(plan["add_patterns"]),
            "deleted": len(plan["blanket_rule_ids"])}


async def _load_bots() -> list[tuple[int, int, str | None]]:
    async with _db.global_db() as db:
        async with db.execute(
            "SELECT id, group_id, role FROM members WHERE type='bot'"
        ) as cur:
            return [(r[0], r[1], r[2]) for r in await cur.fetchall()]


async def migrate(apply: bool) -> dict:
    bots = await _load_bots()
    out_bots: list[dict] = []
    total_added = total_deleted = 0
    for bot_id, group_id, role in bots:
        plan = await plan_for_bot(bot_id, group_id, role)
        if not plan["blanket_rule_ids"]:
            continue
        if apply:
            res = await apply_for_bot(bot_id, group_id, role)
            total_added += res["added"]
            total_deleted += res["deleted"]
            out_bots.append(res)
        else:
            out_bots.append(plan)
    return {"bots": out_bots, "apply": apply,
            "total_added": total_added, "total_deleted": total_deleted}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_migrate_skill_assignment.py -v`
Expected: PASS (all four cases).

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_skill_assignment.py backend/tests/test_migrate_skill_assignment.py
git commit -m "feat(migrate): apply name-scoped expansion + idempotent re-run"
```

---

### Task 3: CLI entrypoint (dry-run/apply/report) + release note

**Files:**
- Modify: `backend/scripts/migrate_skill_assignment.py` (add `main()` + `__main__`)
- Create: `docs/superpowers/release-notes/2026-06-27-skill-permission-tightening.md`
- Test: `backend/tests/test_migrate_skill_assignment.py` (append `TestMainCLI`)

**Interfaces:**
- Consumes: `migrate` (Task 2).
- Produces: `def main(argv=None) -> int` — `--apply` flag (default dry-run); prints mode + per-bot plan + totals; returns 0. `asyncio.run`s `migrate`. Mirrors `migrate_role_skills.main`'s print discipline and backup reminder.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_migrate_skill_assignment.py` (before `if __name__`):

```python
class TestMainCLI(unittest.TestCase):
    def test_main_dry_run_returns_zero_and_no_write(self):
        from scripts import migrate_skill_assignment as M
        from permissions import db as pdb

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def fake_list(bot_id, group_id=None, role=None):
            return [{"name": "deploy"}]

        async def seed():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type, role) VALUES (7,1,'dev','bot','developer')")
                await conn.commit()
            await pdb.save_rule(7, "run_skill", "", "allow")

        try:
            _db.DB_PATH = path
            _run(seed())
            with patch.object(M, "list_skills_all", new=fake_list):
                rc = M.main([])           # dry-run
            # blanket still present (dry-run wrote nothing)
            rules = _run(pdb.load_rules(7))
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        self.assertEqual(rc, 0)
        self.assertTrue(any(r.args_pattern == "" for r in rules))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_migrate_skill_assignment.py::TestMainCLI -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'main'`.

- [ ] **Step 3: Add `main()` to `backend/scripts/migrate_skill_assignment.py`**

Append:

```python
import asyncio


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv

    print(f"[迁移] 模式: {'APPLY（写盘）' if apply else 'DRY-RUN（只打印）'}")
    if apply:
        print("[迁移] 确认：已停机且已备份中央 DB ？(Ctrl-C 取消)")

    result = asyncio.run(migrate(apply=apply))

    if not result["bots"]:
        print("[迁移] 没有需要收紧的 blanket run_skill allow 规则。无操作。")
        return 0

    for p in result["bots"]:
        verb = "已加" if apply else "将加"
        print(f"  bot {p['bot_id']}: {verb} name-scoped allow {p['add_patterns']}"
              f"；blanket 规则 {p['blanket_rule_ids']}"
              f"（已覆盖跳过: {p['skipped_existing']}）")
    if apply:
        print(f"[迁移] 完成：新增 {result['total_added']} 条 name-scoped allow，"
              f"删除 {result['total_deleted']} 条 blanket。")
    else:
        print("\n[迁移] dry-run 完成。确认无误后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_migrate_skill_assignment.py::TestMainCLI -v`
Expected: PASS

- [ ] **Step 5: Write the release note**

Create `docs/superpowers/release-notes/2026-06-27-skill-permission-tightening.md`:

```markdown
# Release Note — Skill 权限收紧（blanket run_skill allow → name-scoped）

**日期：** 2026-06-27
**影响：** 任何曾经对某 bot「always allow run_skill」的群组。

## 变了什么

`synthesize_args_pattern` 从 blanket 改为 name-scoped（Plan A）。旧的「always allow
某技能」在引擎里其实被存成 `args_pattern=''` —— 放行**该 bot 的所有技能名**。Plan B
上线 external skill 的 git 导入后，这种 blanket allow 会**静默自动批准**新导入的（未受信）
外部技能执行。这是必须堵的洞。

## 必须执行的部署步骤

部署 Plan A/B 后、开放 external 导入前，对**中央 DB**跑一次迁移：

```bash
# 1) 停机 + 备份中央 DB
# 2) dry-run 看计划
python3 -m scripts.migrate_skill_assignment
# 3) 确认无误后执行
python3 -m scripts.migrate_skill_assignment --apply
```

迁移会把每个 bot 的 blanket `run_skill` allow 展开为它**当时实际可运行**的各技能的
显式 name-scoped allow，再删掉 blanket 规则：既有技能的免审批保留，未来/导入技能不再
被自动放行。

## 回滚

脚本默认 dry-run、不静默、幂等。回滚 = 从步骤 1 的备份恢复中央 DB。重复运行安全：
没有 blanket 规则的 bot 会被跳过。

## 不在本迁移内

- `bot_skills` 不填充非外部技能（filter_visible 只 gate 外部层；详见 Plan C 计划设计说明）。
- external 导入/分配的运营本身由 Plan B 的 API/UI 负责，与本迁移无关。
```

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/migrate_skill_assignment.py backend/tests/test_migrate_skill_assignment.py docs/superpowers/release-notes/2026-06-27-skill-permission-tightening.md
git commit -m "feat(migrate): CLI entrypoint + skill-permission-tightening release note"
```

---

### Task 4: Full-suite regression + final verification

**Files:** none (verification only).

- [ ] **Step 1: Run the migration test + permissions families**

Run: `cd backend && python3 -m pytest tests/test_migrate_skill_assignment.py tests/test_permissions.py tests/test_permission_patterns.py tests/test_permission_routes_authz.py tests/test_bot_skills.py -v`
Expected: PASS

- [ ] **Step 2: Run the full suite (pre-commit regression gate)**

Run: `cd backend && python3 -m pytest`
Expected: PASS (no new failures).

- [ ] **Step 3: Confirm clean tree**

Run: `git status`
Expected: all Plan C changes committed across Tasks 1–3.

---

## Self-Review

**Spec coverage (§ references):**
- §10 建表 → already idempotent in Plan A (`migration_024` / `_CENTRAL_DDL`); not re-done here.
- §10 权限收紧迁移（blanket→name-scoped 展开、不静默、按每 bot 实际可用技能）→ Tasks 1–3.
- §10 dry-run + 备份 + release note + 回滚 → Task 3 (`main` dry-run default + backup print; release note doc; rollback = restore backup).
- §11 Plan C（`migrate_skill_assignment` + release note + 灰度）→ Tasks 1–3. (灰度/rollout is an operational runbook step captured in the release note's "必须执行的部署步骤", not code.)

**Deliberate deviation (flagged for review):** `bot_skills` is NOT populated for current (non-external) skills — see the "Design decision" section. The behavior-preserving + hole-closing work lives entirely in `permission_rules`; populating `bot_skills` with non-external skills is a no-op for visibility and would be wiped by Plan B Task 10's reconcile.

**Placeholder scan:** every code step ships complete code; every test step has runnable assertions; every run step has an exact command + expected result. No TBD/TODO in deliverables.

**Type/name consistency:** `is_blanket_run_skill_rule(Rule)->bool`, `plan_for_bot(bot_id,group_id,role)->dict` (keys `blanket_rule_ids`/`add_patterns`/`skipped_existing`), `apply_for_bot(...)->dict` (adds `added`/`deleted`), `migrate(apply)->dict` (keys `bots`/`apply`/`total_added`/`total_deleted`), `main(argv)->int` — all consistent across tasks and tests. `synthesize_args_pattern("run_skill", {"name": s})` matches the engine's `_NAME_SCOPED_TOOLS` field exactly, so generated rules match what `engine._matches` checks.

**Out of scope (deferred):** Frontend UI (spec §7.2) — separate plan. `external_skills.update`/`pin`/`audit` (spec §3.4 / §12) — v1 reserved-only.
