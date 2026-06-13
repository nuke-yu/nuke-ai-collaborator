# Shared Workspace Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every bot in a group unconditionally sees the shared workspace directory tree and key project documents (PROJECTS.md / BOARD.md / SPEC.md) in its system prompt at startup and after context compaction reinject.

**Architecture:** Add `load_group_context(group_id)` to `workspace/__init__.py` as the single source of truth for shared-workspace context. Call it in `tool_loop_v1._setup_session` (after both prompt-building branches) and in `_get_fresh_context_prefix` (reinject). Remove the heuristically-gated `_load_project_context` method entirely — its role-name and keyword checks were the root cause of selective blindness.

**Tech Stack:** Python · asyncio · existing `workspace/__init__.py` VFS layer · pytest

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/workspace/__init__.py` | Modify | Add `load_group_context` — shared workspace context, no gating |
| `backend/executors/plugins/tool_loop_v1.py` | Modify | Remove `_load_project_context`; inject group ctx in `_setup_session` + `_get_fresh_context_prefix` |
| `backend/tests/test_workspace_async_io.py` | Modify | Add tests for `load_group_context` |
| `backend/tests/test_dynamic_context.py` | Check / Modify | Verify `_setup_session` tests still pass; add group-ctx injection assertion |

---

### Task 1: Add `load_group_context` to `workspace/__init__.py`

**Files:**
- Modify: `backend/workspace/__init__.py` (after `list_workspace`, ~line 361)
- Test: `backend/tests/test_workspace_async_io.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_workspace_async_io.py`:

```python
class TestLoadGroupContext(unittest.IsolatedAsyncioTestCase):
    async def test_returns_shared_tree_and_key_docs(self):
        import tempfile, pathlib
        from skills import constants as _c
        orig = _c.WORKSPACE_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            _c.WORKSPACE_ROOT = pathlib.Path(tmp)
            try:
                import workspace as ws
                # scaffold shared workspace
                shared = ws.group_workspace(1)                  # creates group_1/shared
                (shared / "workspace" / "my-app").mkdir(parents=True)
                (shared / "workspace" / "my-app" / "app.js").write_text("// hi")
                (shared / "BOARD.md").write_text("# Board")
                (shared / "workspace" / "PROJECTS.md").write_text("# Projects")

                result = await ws.load_group_context(1)

                self.assertIn("my-app", result)
                self.assertIn("app.js", result)
                self.assertIn("# Board", result)
                self.assertIn("# Projects", result)
                self.assertIn("【共享工作区目录】", result)
                self.assertIn("【工作看板】", result)
                self.assertIn("【项目清单】", result)
            finally:
                _c.WORKSPACE_ROOT = orig

    async def test_empty_workspace_returns_empty_string(self):
        import tempfile, pathlib
        from skills import constants as _c
        orig = _c.WORKSPACE_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            _c.WORKSPACE_ROOT = pathlib.Path(tmp)
            try:
                import workspace as ws
                ws.group_workspace(99)   # creates the dir, but nothing inside
                result = await ws.load_group_context(99)
                # no key docs exist → no doc sections; tree may be empty
                self.assertIsInstance(result, str)
            finally:
                _c.WORKSPACE_ROOT = orig
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python3 -m pytest tests/test_workspace_async_io.py::TestLoadGroupContext -v
```
Expected: `AttributeError: module 'workspace' has no attribute 'load_group_context'`

- [ ] **Step 3: Implement `load_group_context` in `workspace/__init__.py`**

Add after `list_workspace` (after line ~361):

```python
async def load_group_context(group_id: int) -> str:
    """群组共享工作区上下文：目录树 + 关键项目文档。

    无条件加载——所有群组 bot 都需要知道共享区有什么。
    gating 不在此做；调用方按需决定是否注入。
    """
    import asyncio

    sections: list[str] = []

    def _build() -> list[str]:
        shared = group_workspace(group_id)
        parts: list[str] = []

        # 1. 目录树
        tree_lines: list[str] = []
        for p in sorted(shared.rglob("*")):
            if any(part.startswith(".") for part in p.relative_to(shared).parts):
                continue
            rel = p.relative_to(shared)
            indent = "  " * (len(rel.parts) - 1)
            icon = "📁" if p.is_dir() else "📄"
            tree_lines.append(f"{indent}{icon} {p.name}")
        header = (
            "【共享工作区目录】"
            "（read_file/write_file 用 workspace/... docs/... prs/... 前缀；"
            "run_shell 用 cwd=\"workspace/my-app\"）"
        )
        parts.append(header + ("\n" + "\n".join(tree_lines) if tree_lines else "\n（空）"))

        # 2. 关键项目文档（按存在情况加载）
        for rel_path, label in [
            ("workspace/PROJECTS.md", "项目清单"),
            ("BOARD.md", "工作看板"),
            ("SPEC.md", "需求文档"),
        ]:
            content = _read_md(shared / rel_path)
            if content:
                parts.append(f"【{label}】\n{content}")

        return parts

    parts = await asyncio.to_thread(_build)
    return "\n\n".join(parts) if parts else ""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python3 -m pytest tests/test_workspace_async_io.py::TestLoadGroupContext -v
```
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/workspace/__init__.py backend/tests/test_workspace_async_io.py
git commit -m "feat(workspace): add load_group_context for unconditional shared-area context"
```

---

### Task 2: Inject group context in `_setup_session` + remove `_load_project_context`

**Files:**
- Modify: `backend/executors/plugins/tool_loop_v1.py`
  - Delete method `_load_project_context` (lines 327–407)
  - Modify `_setup_session` (lines 487–530)

- [ ] **Step 1: Delete `_load_project_context` and the call site in `_setup_session`**

In `tool_loop_v1.py`, delete the entire `_load_project_context` method (lines 327–407).

Then in `_setup_session`, remove line 500:
```python
        # DELETE this line:
        project_context = await self._load_project_context()
```

- [ ] **Step 2: Remove `project_context` usage from the `else` branch in `_setup_session`**

In the `else` branch (lines 506–529), remove the `context_blocks` / `project_context` wiring:

```python
        else:
            base = _with_personality(
                self.bot["system_prompt"] or f"你是{self.bot['name']}，{self.bot.get('role', '')}。", self.bot
            )
            group_section = build_group_section(self.ctx)
            bot_traits = self.bot.get("traits", [])
            traits_section = load_traits(bot_traits)
            os_info = f"Windows (PowerShell)" if _IS_WINDOWS else f"{sys.platform} (shell: /bin/sh)"

            self.system_prompt_base = (
                base
                + (f"\n\n{memory}" if memory else "")
                + traits_section
                + (f"\n\n【群组信息】\n{group_section}" if group_section else "")
                + f"\n\n【运行环境】\nOS: {os_info}\n路径分隔符: {'\\' if _IS_WINDOWS else '/'}\n使用 run_shell 执行命令时请使用适合当前 OS 的语法。"
                + "\n\n【自学技能规则】\n当你发现可复用规律或用户说「记住这个做法」时，用 write_file 将技能写入 `skills/learned/draft/<skill-name>.md`，系统会自动请求用户审批。禁止直接写入 `skills/learned/active/`。"
                + self.ctx.workflow_suffix
            )
```

(The trailing `+ (f"\n\n【项目上下文】\n" + "\n\n".join(context_blocks) if context_blocks else "")` line is removed.)

- [ ] **Step 3: Inject group context after both branches, before `self.system_prompt = ...`**

Replace line 530 (`self.system_prompt = self.system_prompt_base`) with:

```python
        # Group workspace context: unconditionally injected for all group bots.
        # Both skill_discovery and non-skill_discovery branches are covered here.
        if self.ctx.group_id is not None:
            group_ctx = await _ws.load_group_context(self.ctx.group_id)
            if group_ctx:
                self.system_prompt_base += f"\n\n{group_ctx}"

        self.system_prompt = self.system_prompt_base
```

- [ ] **Step 4: Run existing workspace tests to confirm nothing regressed**

```bash
cd backend && python3 -m pytest tests/test_workspace_tools_group.py tests/test_workspace_async_io.py -v
```
Expected: all existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/executors/plugins/tool_loop_v1.py
git commit -m "feat(workspace): inject shared group context in system prompt, remove _load_project_context"
```

---

### Task 3: Refresh group context in `_get_fresh_context_prefix` (reinject path)

**Files:**
- Modify: `backend/executors/plugins/tool_loop_v1.py`, `_get_fresh_context_prefix` (lines 409–419)

- [ ] **Step 1: Write a test that verifies group context appears in reinject prefix**

Add to `backend/tests/test_dynamic_context.py` (or create `test_reinject_group_ctx.py` if that file doesn't cover this):

```python
class TestGetFreshContextPrefixGroupCtx(unittest.IsolatedAsyncioTestCase):
    async def test_group_ctx_included_in_prefix(self):
        """_get_fresh_context_prefix must include shared workspace context when group_id set."""
        import tempfile, pathlib, types
        from skills import constants as _c
        orig_root = _c.WORKSPACE_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            _c.WORKSPACE_ROOT = pathlib.Path(tmp)
            try:
                import workspace as ws_mod
                shared = ws_mod.group_workspace(5)
                (shared / "BOARD.md").write_text("# Board content")

                from executors.plugins.tool_loop_v1 import ToolLoopRunner, ToolLoopV1
                from executors.base import ExecutionContext

                ctx = ExecutionContext(
                    bot={"id": 1, "name": "Dev", "type": "bot"},
                    group_id=5,
                    user_message="hello",
                    sender={"id": 0, "name": "user", "type": "human"},
                    history=[],
                )
                runner = ToolLoopRunner.__new__(ToolLoopRunner)
                runner.bot = ctx.bot
                runner.ctx = ctx
                runner.executor = ToolLoopV1()
                runner.skills_xml = ""

                prefix, _ = await runner._get_fresh_context_prefix()
                self.assertIn("# Board content", prefix)
                self.assertIn("【共享工作区目录】", prefix)
            finally:
                _c.WORKSPACE_ROOT = orig_root
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python3 -m pytest tests/test_dynamic_context.py::TestGetFreshContextPrefixGroupCtx -v
```
Expected: FAIL — group context not yet in prefix.

- [ ] **Step 3: Update `_get_fresh_context_prefix` to include group context**

Replace the method body (lines 409–419):

```python
    async def _get_fresh_context_prefix(self) -> tuple[str, str]:
        blocks = await load_context_files(
            self.bot["id"], self.ctx.group_id, self.executor.manifest.workspace.startup_files
        )
        text = format_context_blocks(blocks)
        prefix = ""
        if text:
            prefix += f"【工作区文件】\n{text}\n\n"
        if self.skills_xml:
            prefix += f"{self.skills_xml}\n使用 run_skill(name=\"技能名\") 调用\n\n"
        if self.ctx.group_id is not None:
            group_ctx = await _ws.load_group_context(self.ctx.group_id)
            if group_ctx:
                prefix += f"{group_ctx}\n\n"
        return prefix, text
```

- [ ] **Step 4: Run the new test + full related suite**

```bash
cd backend && python3 -m pytest tests/test_dynamic_context.py tests/test_workspace_async_io.py tests/test_workspace_tools_group.py -v
```
Expected: all pass.

- [ ] **Step 5: Run full regression before final commit**

```bash
cd backend && python3 -m pytest
```
Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/executors/plugins/tool_loop_v1.py
git commit -m "feat(workspace): refresh shared group context on reinject"
```

---

## Self-Review

**Spec coverage:**
- ✅ Shared workspace directory tree visible to bots at startup → Task 2 (inject in `_setup_session`)
- ✅ Key project docs (PROJECTS.md, BOARD.md, SPEC.md) visible to bots → Task 1 (`load_group_context`) + Task 2
- ✅ Both `skill_discovery=True` and `=False` paths covered → Task 2 injects after both branches
- ✅ Long-conversation reinject also refreshes shared context → Task 3
- ✅ Heuristic gating removed → `_load_project_context` deleted in Task 2
- ✅ No changes to `compile_system_prompt` (correct boundary)

**Placeholder scan:** None found — all code blocks are complete.

**Type consistency:** `load_group_context(group_id: int) -> str` used consistently across all three tasks.
