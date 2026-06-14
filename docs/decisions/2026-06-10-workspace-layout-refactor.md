# 工作区布局改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 bot 工作区从扁平的 `workspaces/bot_{id}` 收归到 `workspaces/group_{gid}/bots/bot_{id}`，建立单一布局真相源、让 group_id 显式贯穿、删除埋在路径函数里的 DB 反查，并打通 Dev→QA 在群组共享区的代码交接。

**Architecture:** 新建 `workspace/layout.py` 作为唯一路径真相源（纯函数、无 I/O、只吃显式 id）。所有路径计算委托给它，消灭 `workspace.bot_workspace` 与 `skills.constants.bot_ws` 的重复定义。group_id 本就被 context 携带（`ctx.group_id` / `context["group_id"]` / `list_skills_all(..., group_id=)`），改造是把它**显式传进路径函数**并**移除** `_get_effective_ws` 里的 `SELECT group_id FROM members` 反查——是删 hack，不是加逻辑。

**Tech Stack:** Python 3（`python3`）· pytest · pathlib · aiosqlite

**关联设计：** [docs/WORKSPACE-LAYOUT-DESIGN.md](../../WORKSPACE-LAYOUT-DESIGN.md)

---

## 相位依赖说明（重要）

布局收口（layout.py）与 group_id 贯穿是**耦合**的：`bot_dir` 一旦改成 `(gid, bot_id)` 签名，所有调用点必须同步给出 gid。因此分三相，每相独立可测：

- **Phase 1（纯重构，零行为变化）**：建 `layout.py`，`bot_dir(bot_id)` 仍返回**当前扁平路径** `workspaces/bot_{id}`。把 `bot_workspace`/`bot_ws` 收口委托给它。现有测试全绿——磁盘布局未动。先消灭重复定义（设计 §8.0① 自己点名的「读不到技能必崩」风险）。
- **Phase 2（布局切换 + 贯穿 + 迁移）**：`bot_dir(gid, bot_id)` 改**嵌套路径**，group_id 加形参贯穿全链，删 DB 反查，deliverables→workspace/docs 前缀，watcher 正则，一次性迁移脚本。
- **Phase 3（打通交接，含待定决策）**：shell 沙箱放行群组共享区 + **承重墙锁（决策 #1，见下）** + bot 指令面（决策 #3，见下）。

### ⚠️ Phase 3 开工前需确认的两个决策

- **#1 共享工作树并发**：设计 D5 假设「同一时刻仅一个 bot 操作工作树」，但 `apply_step` 用 `bg.spawn_group` 并发派发 `step.next_units`，且真人可直接 @ 多 bot、bot 可 spawn 子 agent——共享 `shared/workspace/<repo>/` 无锁。**已确认调度为固定分片**：`supervisor.py` 把 group→worker `pinned`（CELL-15 `assigned_worker_id` 表），`runtime/lifecycle.py` 的 `GroupLock` lease 锁保证两个 worker 不可能同拥一群组。故一个群组的全部 bot + 子 agent 都在**同一进程**内跑 → **本计划采纳：Task 13 用进程内 `asyncio.Lock`（按 group_id keyed）即可，无需 DB 级跨进程锁**。若调度改为动态轮转，Task 13 才需升级为 `group_locks`。
- **#3 bot 指令面**：路径重定向让 `workspace/`、`docs/` 前缀落共享区，但 bot **默认仍写私有区**，除非其指令面（`AGENT.md` / `BOOTSTRAP.md` 模板）告诉它约定。Task 14 更新模板。若不做，管道通但交接仍断。

---

## File Structure

| 文件 | 责任 | 相位 |
| :-- | :-- | :-- |
| `backend/workspace/layout.py` | **新建**。唯一布局真相源，纯函数：`group_dir / bot_dir / group_shared_dir / group_runs_dir` | P1 建，P2 改 bot_dir |
| `backend/workspace/__init__.py` | VFS。`bot_workspace`/`group_workspace` 委托 layout；`_get_effective_ws` 删 DB 反查、加 group_id 形参；VFS 函数加 group_id 形参 | P1+P2 |
| `backend/skills/constants.py` | `bot_ws` 委托 layout（消灭重复定义） | P1+P2 |
| `backend/skills/discovery.py` | `_skills_dir_for_layer`/`_scan_signature`/`list_skills` 的 `bot_ws(bot_id)` → 带 group_id | P2 |
| `backend/skills/loader.py` | 同上 | P2 |
| `backend/skills/lifecycle.py` | 同上 | P2 |
| `backend/skills/watcher.py` | `_BOT_RE` 改 `^group_(\d+)/bots/bot_(\d+)/skills/` | P2 |
| `backend/executors/plugins/workspace_tools.py` | 工具包装传 group_id；`_resolve_shell_cwd` 放行共享区 | P2+P3 |
| `backend/api/workspace.py` | HTTP handler 从 `bot["group_id"]` 取 gid 下传 | P2 |
| `backend/scripts/migrate_workspace_layout.py` | **新建**。一次性迁移：`bot_{id}` → `group_{gid}/bots/bot_{id}` | P2 |
| `backend/workspace/templates.py` | `AGENT_TEMPLATE`/`BOOTSTRAP_TEMPLATE` 补共享区写入约定 | P3 |

---

# Phase 1 — 单一布局真相源（零行为变化）

### Task 1: 新建 `workspace/layout.py`（扁平兼容，纯函数）

**Files:**
- Create: `backend/workspace/layout.py`
- Test: `backend/tests/test_layout.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_layout.py
from pathlib import Path
from skills.constants import WORKSPACE_ROOT
from workspace import layout


def test_bot_dir_flat_current_path():
    # Phase 1: bot_dir 仍返回当前扁平路径，零行为变化
    assert layout.bot_dir(7) == WORKSPACE_ROOT / "bot_7"


def test_group_dir_and_shared():
    assert layout.group_dir(3) == WORKSPACE_ROOT / "group_3"
    assert layout.group_shared_dir(3) == WORKSPACE_ROOT / "group_3" / "shared"
    assert layout.group_runs_dir(3) == WORKSPACE_ROOT / "group_3" / "runs"


def test_layout_is_pure_no_mkdir(tmp_path, monkeypatch):
    # 纯函数：调用不得在磁盘上创建任何目录
    monkeypatch.setattr(layout, "WORKSPACE_ROOT", tmp_path)
    _ = layout.bot_dir(1)
    _ = layout.group_shared_dir(1)
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'workspace.layout'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/workspace/layout.py
"""单一布局真相源（Single Layout Truth）。

纯函数：无 I/O、不 mkdir、只吃显式 id。所有工作区路径由此一处计算，
消灭 workspace.bot_workspace 与 skills.constants.bot_ws 的重复定义。

Phase 1：bot_dir 返回当前扁平路径（workspaces/bot_{id}），零行为变化。
Phase 2：改为嵌套 workspaces/group_{gid}/bots/bot_{id} 并要求 group_id。
"""
from pathlib import Path
from skills.constants import WORKSPACE_ROOT


def group_dir(gid: int) -> Path:
    return WORKSPACE_ROOT / f"group_{gid}"


def group_shared_dir(gid: int) -> Path:
    return group_dir(gid) / "shared"


def group_runs_dir(gid: int) -> Path:
    return group_dir(gid) / "runs"


def bot_dir(bot_id: int) -> Path:
    # Phase 1: 扁平兼容。Phase 2 改签名为 bot_dir(gid, bot_id) → 嵌套。
    return WORKSPACE_ROOT / f"bot_{bot_id}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_layout.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/workspace/layout.py backend/tests/test_layout.py
git commit -m "feat(workspace): 新建 layout.py 单一布局真相源（扁平兼容，零行为变化）"
```

---

### Task 2: `bot_workspace` / `group_workspace` / `bot_ws` 委托 layout

**Files:**
- Modify: `backend/workspace/__init__.py:55-66`（`bot_workspace`、`group_workspace`）
- Modify: `backend/skills/constants.py:12-18`（`bot_ws`、`group_ws`）
- Test: `backend/tests/test_layout.py`（追加）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_layout.py 追加
def test_bot_workspace_delegates_to_layout():
    import workspace
    from workspace import layout
    # bot_workspace 仍 mkdir（保留副作用），但路径与 layout.bot_dir 一致
    assert workspace.bot_workspace(7).resolve() == layout.bot_dir(7).resolve()


def test_skills_bot_ws_delegates_to_layout():
    from skills.constants import bot_ws
    from workspace import layout
    assert bot_ws(7) == layout.bot_dir(7)


def test_group_workspace_delegates_to_layout():
    import workspace
    from workspace import layout
    assert workspace.group_workspace(3).resolve() == layout.group_shared_dir(3).resolve()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_layout.py -v`
Expected: FAIL — `test_skills_bot_ws_delegates_to_layout`（当前 `bot_ws` 自己拼路径，逻辑虽同但需收口）。其余两条可能已 PASS。

- [ ] **Step 3: Write minimal implementation**

`backend/skills/constants.py`：`bot_ws`/`group_ws` 改为委托。注意 layout 反过来 import constants 的 `WORKSPACE_ROOT`，为避免循环 import，在函数体内 import：

```python
def bot_ws(bot_id: int) -> Path:
    """Return bot workspace path (no mkdir — caller is responsible)."""
    from workspace import layout
    return layout.bot_dir(bot_id)


def group_ws(group_id: int) -> Path:
    from workspace import layout
    return layout.group_shared_dir(group_id)
```

`backend/workspace/__init__.py`：`bot_workspace` 保留 mkdir 副作用但路径取自 layout；`group_workspace` 同理：

```python
def bot_workspace(bot_id: int) -> Path:
    from workspace import layout
    path = layout.bot_dir(bot_id)
    path.mkdir(parents=True, exist_ok=True)
    for sub in _SUBDIRS:
        (path / sub).mkdir(exist_ok=True)
    return path


def group_workspace(group_id: int) -> Path:
    from workspace import layout
    path = layout.group_shared_dir(group_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_layout.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Phase 1 回归——跑所有碰工作区/技能的现有测试**

Run: `cd backend && python3 -m pytest tests/test_layout.py tests/test_workspace_make_dir.py tests/test_workspace_redirect.py tests/test_skill_selfwrite.py tests/test_member_routes.py -v`
Expected: 全 PASS（布局未变，纯收口）。若 `test_workspace_redirect` 失败，说明委托改动了行为——停下排查，Phase 1 必须零行为变化。

- [ ] **Step 6: Commit**

```bash
git add backend/workspace/__init__.py backend/skills/constants.py backend/tests/test_layout.py
git commit -m "refactor(workspace): bot_workspace/bot_ws/group_workspace 收口委托 layout（零行为变化）"
```

---

# Phase 2 — 布局切换 + group_id 贯穿 + 迁移

> 本相完成后磁盘布局从 `bot_{id}` 变为 `group_{gid}/bots/bot_{id}`。务必在 Phase 1 全绿后再开工。每个 Task 后跑相关测试。

### Task 3: layout.bot_dir 改嵌套签名 `(gid, bot_id)`

**Files:**
- Modify: `backend/workspace/layout.py`
- Test: `backend/tests/test_layout.py`

- [ ] **Step 1: 改测试为嵌套路径预期**

```python
def test_bot_dir_nested_under_group():
    from workspace import layout
    from skills.constants import WORKSPACE_ROOT
    assert layout.bot_dir(3, 7) == WORKSPACE_ROOT / "group_3" / "bots" / "bot_7"
```

（删除/替换 Task 1 里的 `test_bot_dir_flat_current_path`）

- [ ] **Step 2: Run，确认旧扁平测试失败 / 新测试失败**

Run: `cd backend && python3 -m pytest tests/test_layout.py::test_bot_dir_nested_under_group -v`
Expected: FAIL — `bot_dir() takes 1 positional argument but 2 were given`

- [ ] **Step 3: 改实现**

```python
def bot_dir(gid: int, bot_id: int) -> Path:
    return group_dir(gid) / "bots" / f"bot_{bot_id}"
```

- [ ] **Step 4: Run**

Run: `cd backend && python3 -m pytest tests/test_layout.py::test_bot_dir_nested_under_group -v`
Expected: PASS

> 此步后 `bot_workspace(bot_id)`、`bot_ws(bot_id)`、所有 `_get_effective_ws` 调用会因签名不符而**全线报错**——这是预期的，Task 4–8 逐一补 group_id。先不 commit，作为 Task 4 的起点；或临时给 `bot_dir` 的 gid 加默认值 `None` 走旧路径以保持可运行，待 Task 4–8 完成后移除默认值。**推荐后者**，便于分步提交。

- [ ] **Step 5（推荐）：bot_dir 兼容垫片**

```python
def bot_dir(gid: int | None, bot_id: int) -> Path:
    if gid is None:
        # 过渡垫片：Task 4-8 完成后删除，强制显式 gid
        return WORKSPACE_ROOT / f"bot_{bot_id}"
    return group_dir(gid) / "bots" / f"bot_{bot_id}"
```

Commit：

```bash
git add backend/workspace/layout.py backend/tests/test_layout.py
git commit -m "feat(workspace): layout.bot_dir 改嵌套签名 (gid, bot_id)，gid=None 走过渡垫片"
```

---

### Task 4: `_get_effective_ws` 删 DB 反查 + 加 group_id 形参 + 改共享前缀

**Files:**
- Modify: `backend/workspace/__init__.py:82-94`（`_get_effective_ws`）、`79`（`_SHARED_FILES` 旁加共享前缀集）
- Test: `backend/tests/test_workspace_redirect.py`

设计 §8.1.2：移除 `deliverables/` 前缀，新增 `workspace/`、`docs/` 共享前缀。

- [ ] **Step 1: 改写 redirect 测试**

```python
# backend/tests/test_workspace_redirect.py 核心断言改为：
# 1. group_id 由入参传入，_get_effective_ws 不再查 DB
# 2. workspace/ 与 docs/ 前缀重定向到群组共享区
# 3. deliverables/ 不再特殊（落私有区）
def test_shared_prefixes_redirect_to_group():
    from workspace import _get_effective_ws, layout
    for shared in ("BOARD.md", "SPEC.md", "workspace/repo1/main.py", "docs/qa-report.md"):
        r = _get_effective_ws(bot_id=7, path_str=shared, group_id=3)
        assert str(r).startswith(str(layout.group_shared_dir(3)))


def test_private_path_stays_private():
    from workspace import _get_effective_ws, layout
    r = _get_effective_ws(bot_id=7, path_str="notes.md", group_id=3)
    assert r == layout.bot_dir(3, 7)


def test_no_db_query_in_path_resolution(monkeypatch):
    # 反查已删：即便 DB 不可用，路径解析也不该崩
    import workspace
    monkeypatch.setattr(workspace, "connect_sync", None, raising=False)
    r = workspace._get_effective_ws(bot_id=7, path_str="BOARD.md", group_id=3)
    assert r is not None
```

- [ ] **Step 2: Run，确认失败**

Run: `cd backend && python3 -m pytest tests/test_workspace_redirect.py -v`
Expected: FAIL — 旧 `_get_effective_ws(bot_id, path_str)` 无 group_id 形参

- [ ] **Step 3: 改实现**

```python
_SHARED_FILES = {"BOARD.md", "SPEC.md", "API_CONTRACT.md", "RETRO_LATEST.md"}
_SHARED_PREFIXES = ("workspace/", "docs/", "prs/", "skills/")


def _get_effective_ws(bot_id: int, path_str: str, group_id: int) -> Path:
    """群组文件重定向。group_id 由调用方显式传入（不再查 DB）。

    共享文件名 / 共享前缀 → 群组 shared 区；其余 → bot 私有区。
    """
    if path_str in _SHARED_FILES or path_str.startswith(_SHARED_PREFIXES):
        return group_workspace(group_id)
    return bot_workspace(bot_id, group_id)
```

> 注：`skills/` 列入共享前缀需复核——bot 私有技能写在 `bots/bot_{id}/skills/`，群组技能在 `shared/skills/`。当前 `_commit_text` 用 `_LEARNED_ACTIVE`/`_LEARNED_DRAFT` 判定私有技能草稿，**私有 `skills/` 不应进共享前缀**。落地时移除 `skills/`，仅保留 `workspace/`、`docs/`、`prs/`。执行者按实际语义定。

- [ ] **Step 4: Run**

Run: `cd backend && python3 -m pytest tests/test_workspace_redirect.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/workspace/__init__.py backend/tests/test_workspace_redirect.py
git commit -m "refactor(workspace): _get_effective_ws 删 DB 反查、group_id 显式入参、改共享前缀"
```

---

### Task 5: VFS 函数加 group_id 形参（read/write/edit/make_dir/delete/list）+ bot_workspace 加 gid

**Files:**
- Modify: `backend/workspace/__init__.py`（`bot_workspace`、`read_file`、`write_file`、`make_dir`、`delete_path`、`edit_file`、`list_workspace`、`list_workspace_tree`、`list_file_history`、`read_file_history_version`、`append_log`、`init_bot_workspace`）
- Test: `backend/tests/test_workspace_make_dir.py`

关键约束（设计 §二.5）：`bot_id=0` + 显式 group_id 的调用（`rd_manager.py`、`integrations/git.py`）必须落共享区，**永不**解析到 `bots/bot_0/`。`write_file` 已有 `group_id` 形参优先逻辑，保留并强化。

- [ ] **Step 1: 写测试钉住 bot_id=0 + group_id 不入私有**

```python
def test_bot0_with_group_writes_shared_not_private(tmp_workspace):
    import asyncio, workspace
    from workspace import layout
    asyncio.run(workspace.write_file(0, "BOARD.md", "x", group_id=3))
    assert (layout.group_shared_dir(3) / "BOARD.md").exists()
    assert not (layout.group_dir(3) / "bots" / "bot_0").exists()


def test_make_dir_requires_group_id(tmp_workspace):
    import workspace
    r = workspace.make_dir(7, "newdir", group_id=3)
    assert "已创建" in r
```

- [ ] **Step 2: Run，确认失败**

Run: `cd backend && python3 -m pytest tests/test_workspace_make_dir.py -v`
Expected: FAIL — `make_dir() got an unexpected keyword argument 'group_id'`

- [ ] **Step 3: 改实现**

`bot_workspace` 加 gid 形参（过渡期 `gid: int | None = None`，转给 `layout.bot_dir(gid, bot_id)`）。每个 VFS 函数签名加 `group_id: int`（同步 / 异步均加），内部把 `_get_effective_ws(bot_id, path)` 调用改为 `_get_effective_ws(bot_id, path, group_id)`，`bot_workspace(bot_id)` 改 `bot_workspace(bot_id, group_id)`。示例（`make_dir`）：

```python
def make_dir(bot_id: int, path: str, group_id: int) -> str:
    ws = _get_effective_ws(bot_id, path, group_id)
    p = _safe_path(ws, path)
    ...
```

逐个函数照此改。`list_workspace` / `list_workspace_tree` / `append_log` / `init_bot_workspace` 内部的 `bot_workspace(bot_id)` → `bot_workspace(bot_id, group_id)`。

- [ ] **Step 4: Run**

Run: `cd backend && python3 -m pytest tests/test_workspace_make_dir.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/workspace/__init__.py backend/tests/test_workspace_make_dir.py
git commit -m "feat(workspace): VFS 函数 group_id 显式贯穿；钉住 bot_id=0 写共享不入私有"
```

---

### Task 6: 工具包装层 + shell cwd 传 group_id（`workspace_tools.py`）

**Files:**
- Modify: `backend/executors/plugins/workspace_tools.py`（`_handle_read_file`/`_handle_write_file`/`_handle_edit_file`/`_handle_list_workspace` 等；`_resolve_shell_cwd`）
- Test: `backend/tests/test_process_sandbox.py`（已有 mock，复核签名）

`context` 已携带 `bot_id`，同样携带 `group_id`（见 `ExecutionContext`）。

- [ ] **Step 1: 写测试——工具包装从 context 取 group_id 下传**

```python
def test_handle_write_file_threads_group_id(monkeypatch):
    import asyncio
    from executors.plugins import workspace_tools as wt
    captured = {}
    async def fake_write(bot_id, path, content, group_id=None):
        captured["gid"] = group_id; return "ok"
    monkeypatch.setattr(wt._ws, "write_file", fake_write)
    asyncio.run(wt._handle_write_file("a.py", "x", context={"bot_id": 7, "group_id": 3}))
    assert captured["gid"] == 3
```

- [ ] **Step 2: Run，确认失败**

Run: `cd backend && python3 -m pytest tests/test_process_sandbox.py tests/test_workspace_tools_group.py -v`
Expected: FAIL

- [ ] **Step 3: 改实现**

每个 `_handle_*` 从 `context` 取 `group_id` 下传：

```python
async def _handle_write_file(path: str, content: str, context: dict = None) -> str:
    ctx = context or {}
    bot_id = ctx.get("bot_id")
    group_id = ctx.get("group_id")
    return await _ws.write_file(bot_id, path, content, group_id=group_id) if bot_id else "[错误] 缺少 bot_id"
```

`read_file`/`edit_file`/`list_workspace`/`make_dir`/`delete_path` 同理。`_resolve_shell_cwd(cwd, bot_id)` → `_resolve_shell_cwd(cwd, bot_id, group_id)`，内部 `_ws.bot_workspace(bot_id)` → `_ws.bot_workspace(bot_id, group_id)`（共享区放行留到 Phase 3 Task 13）。

- [ ] **Step 4: Run**

Run: `cd backend && python3 -m pytest tests/test_process_sandbox.py tests/test_dft_065_port_interception.py tests/test_port_allocator.py -v`
Expected: PASS（注意这些测试 mock 了 `_resolve_shell_cwd`，改签名后复核 mock 的 `return_value` 仍兼容）

- [ ] **Step 5: Commit**

```bash
git add backend/executors/plugins/workspace_tools.py backend/tests/
git commit -m "feat(tools): 工具包装与 shell cwd 从 context 贯穿 group_id"
```

---

### Task 7: skills 层贯穿 group_id（discovery / loader / lifecycle）

**Files:**
- Modify: `backend/skills/discovery.py`（`_skills_dir_for_layer`、`_scan_signature`、`list_skills`）、`backend/skills/loader.py`、`backend/skills/lifecycle.py`
- Test: `backend/tests/test_skill_selfwrite.py` + 新 `tests/test_skills_group_path.py`

⚠️ 设计 §8.0① 点名的高危点：skills 路径线穿错 → bot 静默读不到自己技能就崩。`list_skills_all` / `load_always_skills` 调用方**已携带 group_id**。

- [ ] **Step 1: 写测试——私有技能落 group_{gid}/bots/bot_{id}/skills**

```python
def test_personal_skill_dir_under_group(tmp_workspace):
    from skills.discovery import _skills_dir_for_layer
    from workspace import layout
    d = _skills_dir_for_layer("learned", bot_id=7, group_id=3, role=None)
    assert d == layout.bot_dir(3, 7) / "skills" / "learned" / "active"
```

- [ ] **Step 2: Run，确认失败**

Run: `cd backend && python3 -m pytest tests/test_skills_group_path.py -v`
Expected: FAIL — `bot_ws(bot_id)` 仍走扁平 / 缺 group_id

- [ ] **Step 3: 改实现**

`discovery.py` 的 `_skills_dir_for_layer`/`_scan_signature`/`list_skills` 把 `bot_ws(bot_id)` → `layout.bot_dir(group_id, bot_id)`（这些函数已有 group_id 形参）。`loader.py` 的 `_skill_dir` 加 group_id 形参。`lifecycle.py` 4 处同理。统一改用 `from workspace import layout` 而非 `bot_ws`。

- [ ] **Step 4: Run**

Run: `cd backend && python3 -m pytest tests/test_skills_group_path.py tests/test_skill_selfwrite.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/ backend/tests/test_skills_group_path.py
git commit -m "feat(skills): discovery/loader/lifecycle 贯穿 group_id，私有技能路径收归群组下"
```

---

### Task 8: watcher 正则改嵌套路径

**Files:**
- Modify: `backend/skills/watcher.py:25`（`_BOT_RE`）、`41-43`（`_parse_path` 提取 group_id）
- Test: `backend/tests/test_skill_watcher.py`（若无则新建）

- [ ] **Step 1: 写测试**

```python
def test_bot_skill_path_parses_group_and_member():
    from skills.watcher import _parse_path
    from skills.constants import WORKSPACE_ROOT
    abs_p = str(WORKSPACE_ROOT / "group_3" / "bots" / "bot_7" / "skills" / "foo.md")
    info = _parse_path(abs_p)
    assert info == {"source": "bot", "member_id": 7, "group_id": 3}
```

- [ ] **Step 2: Run，确认失败**

Run: `cd backend && python3 -m pytest tests/test_skill_watcher.py -v`
Expected: FAIL

- [ ] **Step 3: 改实现**

```python
_BOT_RE = re.compile(r"^group_(\d+)[/\\]bots[/\\]bot_(\d+)[/\\]skills[/\\]")
...
m = _BOT_RE.match(rel)
if m:
    return {"source": "bot", "member_id": int(m.group(2)), "group_id": int(m.group(1))}
```

- [ ] **Step 4: Run**

Run: `cd backend && python3 -m pytest tests/test_skill_watcher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/watcher.py backend/tests/test_skill_watcher.py
git commit -m "fix(skills): watcher _BOT_RE 匹配嵌套 group/bots/bot 路径并提取 group_id"
```

---

### Task 9: API 边界解析一次（`api/workspace.py`）

**Files:**
- Modify: `backend/api/workspace.py`（`get_workspace_file`、`put_workspace_file`、`dir`/`delete` 等所有调用 VFS 的 handler）
- Test: `backend/tests/test_member_routes.py`

设计 §8.0③：handler 本就 `bot = await get_member(db, member_id)`，`bot["group_id"]` 直接下传，零新增查询。

- [ ] **Step 1: 写/改测试——handler 用 bot["group_id"] 下传**

```python
def test_get_workspace_file_passes_group_id(monkeypatch):
    # get_member 返回的 bot 带 group_id；read_file 应收到它
    ...（沿用 test_member_routes 既有 fixture，断言 read_file 收到 group_id=bot["group_id"]）
```

- [ ] **Step 2: Run，确认失败**

Run: `cd backend && python3 -m pytest tests/test_member_routes.py -v`
Expected: FAIL

- [ ] **Step 3: 改实现**

```python
content = await read_file(member_id, path, group_id=bot["group_id"])
...
result = await write_file(member_id, path, content, group_id=bot["group_id"])
```

所有 handler 照改。`api/workspace.py:157-159` 的 `_skills_bot_ws(member_id)` 同样需 group_id（用 `bot["group_id"]`）。

- [ ] **Step 4: Run**

Run: `cd backend && python3 -m pytest tests/test_member_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/workspace.py backend/tests/test_member_routes.py
git commit -m "feat(api): workspace handler 在边界解析 group_id 并显式下传（零新增查询）"
```

---

### Task 10: 移除 bot_dir 过渡垫片 + 收尾 deliverables

**Files:**
- Modify: `backend/workspace/layout.py`（删 `gid is None` 垫片）、`backend/workspace/__init__.py`（`init_group_workspace` 删 `deliverables/` mkdir，加 `docs/`、`workspace/`）

- [ ] **Step 1: 删垫片，强制显式 gid**

```python
def bot_dir(gid: int, bot_id: int) -> Path:
    return group_dir(gid) / "bots" / f"bot_{bot_id}"
```

- [ ] **Step 2: `init_group_workspace` 建新共享子目录**

```python
async def init_group_workspace(group_id: int, group_name: str = ""):
    ws = group_workspace(group_id)
    (ws / "docs").mkdir(exist_ok=True)
    (ws / "workspace").mkdir(exist_ok=True)
    (ws / "skills").mkdir(exist_ok=True)
    (ws.parent / "runs").mkdir(exist_ok=True)
    ...（删除原 deliverables mkdir）
```

- [ ] **Step 3: 全套回归**

Run: `cd backend && python3 -m pytest -x -q`
Expected: 全 PASS。任一 `bot_dir() missing 1 required positional argument: 'gid'` 报错 = 还有调用点没传 gid，回到对应 Task 补齐。

- [ ] **Step 4: Commit**

```bash
git add backend/workspace/
git commit -m "refactor(workspace): 移除 bot_dir 过渡垫片；共享区改建 docs/workspace 子目录"
```

---

### Task 11: 一次性迁移脚本

**Files:**
- Create: `backend/scripts/migrate_workspace_layout.py`
- Test: `backend/tests/test_migrate_layout.py`

设计 §8.2：遍历 DB 中 bot（必有 group），`workspaces/bot_{id}` → `workspaces/group_{gid}/bots/bot_{id}`；无 DB 记录的 `bot_*` = 脏数据，**dry-run 打印后需确认才删**。

- [ ] **Step 1: 写测试——迁移移动有主目录、列出孤儿**

```python
def test_migrate_moves_known_bot(tmp_workspace, fake_members):
    # bot 7 属 group 3：workspaces/bot_7 → workspaces/group_3/bots/bot_7
    ...
def test_migrate_lists_orphans_without_deleting_by_default():
    # workspaces/bot_999 无 DB 记录：dry-run 列出、不删
    ...
def test_migrate_is_idempotent():
    # 再跑一次不报错、不重复移动
    ...
```

- [ ] **Step 2: Run，确认失败**

Run: `cd backend && python3 -m pytest tests/test_migrate_layout.py -v`
Expected: FAIL

- [ ] **Step 3: 写脚本**

要点：① 跑前提示「确认系统已停机 + 已备份 workspaces/」；② 幂等（目标已存在则跳过）；③ 默认 `--dry-run`，孤儿目录仅打印，`--delete-orphans` 才删；④ 收尾校验：每个 DB bot ⟺ 恰好一个新目录、旧位置零残留 `bot_*`。

- [ ] **Step 4: Run**

Run: `cd backend && python3 -m pytest tests/test_migrate_layout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_workspace_layout.py backend/tests/test_migrate_layout.py
git commit -m "feat(scripts): 工作区布局迁移脚本（幂等 + dry-run 孤儿保护 + 收尾校验）"
```

---

# Phase 3 — 打通交接（含待定决策 #1 / #3）

> **开工前先确认 #1、#3 决策**（见顶部）。下列 Task 按「默认采纳」写，决策若变则相应重写。

### Task 12: shell 沙箱放行群组共享区

**Files:**
- Modify: `backend/executors/plugins/workspace_tools.py`（`_resolve_shell_cwd`、`_check_shell_command_paths`）
- Test: `backend/tests/test_process_sandbox.py`

设计 §8.1.3：除 `bot_dir(gid, bot_id)` 外额外放行 `group_shared_dir(gid)`，否则 Dev/QA 无法在共享区 build/跑测/git。需确认 `git clone/branch/commit/push` + 网络不被高危命令 guard 误杀。

- [ ] **Step 1: 写测试——cwd 落共享 workspace/repo 放行；越界仍拒**

```python
def test_shell_cwd_allows_group_shared_workspace():
    from executors.plugins.workspace_tools import _resolve_shell_cwd
    from workspace import layout
    target = layout.group_shared_dir(3) / "workspace" / "repo1"
    path, err = _resolve_shell_cwd(str(target), bot_id=7, group_id=3)
    assert err == "" and path is not None

def test_shell_cwd_rejects_other_group():
    from executors.plugins.workspace_tools import _resolve_shell_cwd
    from workspace import layout
    other = layout.group_shared_dir(99) / "workspace"
    _, err = _resolve_shell_cwd(str(other), bot_id=7, group_id=3)
    assert err != ""
```

- [ ] **Step 2-4:** 实现「双根放行」（bot 私有 + 本群组共享），跑测试至 PASS。
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(tools): shell 沙箱放行本群组共享区，打通 Dev/QA 共享工作树"
```

---

### Task 13: 共享工作树并发锁（决策 #1，默认采纳）

**Files:**
- Modify: `backend/executors/plugins/workspace_tools.py`（run_shell 入口）
- Reference: `backend/core/orchestration/locks.py`（`group_locks` 既有基础设施）
- Test: `backend/tests/test_worktree_lock.py`

承重墙：当 `run_shell` 的 cwd 落在 `group_shared_dir(gid)/workspace/` 下，取 per-group 互斥，防两个 bot/子 agent 并发撞 `.git/index`。

**调度粒度已确认（固定分片）**：`supervisor.py` group→worker `pinned`（CELL-15 `assigned_worker_id`），`GroupLock` lease 锁保证一群组同一时刻仅一个 worker 拥有。故同群组的并发 run_shell 必在同一进程 → **进程内 `asyncio.Lock` 足够，不上 DB 级 `group_locks`**。

- [ ] **Step 1-4:** 写测试（两个并发 run_shell 落同一 group 共享 workspace → 串行化）→ 实现按 group_id keyed 的进程内 `asyncio.Lock`（worker 进程级单例 registry，类比 `_get_path_lock` 的 per-loop 注册表）→ PASS。
  - 复用模式：`workspace/__init__.py` 已有 `_get_path_lock`（per-loop 路径锁注册表）可作蓝本，新增一个 per-group worktree 锁注册表。
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(tools): 共享工作树 per-group 互斥锁，消除并发 git/build 竞态"
```

---

### Task 14: bot 指令面——告诉 bot 往哪写（决策 #3，默认采纳）

**Files:**
- Modify: `backend/workspace/templates.py`（`AGENT_TEMPLATE` / `BOOTSTRAP_TEMPLATE`）
- Test: 渲染断言（`tests/test_templates.py` 若有）

补约定：代码写 `workspace/<repo>/...`、共享文档写 `docs/...`、私有草稿留根；交接产物才进共享。

- [ ] **Step 1-4:** 模板补段落 → 渲染测试断言含关键约定串 → PASS。
- [ ] **Step 5: Commit**

```bash
git commit -m "docs(workspace): bot 指令模板补共享区写入约定（代码→workspace/，文档→docs/）"
```

---

### Task 15: 文档回写 + 全套回归

- [ ] **Step 1:** `docs/WORKSPACE-LAYOUT-DESIGN.md` 标注实现完成；若采纳 #1 锁，把「无并发约束」改为「共享工作树 per-group 互斥」并补进 §七决策记录。
- [ ] **Step 2:** 全套回归 `cd backend && python3 -m pytest -q`，全绿。
- [ ] **Step 3: Commit**

```bash
git commit -m "docs(workspace-layout): 标注实现完成，并发约束改记为共享工作树互斥"
```

---

## Self-Review 备注

- **Spec 覆盖**：§三目录树（Task 3/10/11）、§8.0 三支柱（layout=Task 1/3，贯穿=Task 5-9，边界解析=Task 9）、§8.1 连带改动 1-6（正则=Task 8，前缀=Task 4，shell=Task 12，可发现性=Task 14，git 凭证=系统层无需改，时序=Task 13）、§8.2 迁移（Task 11）。
- **未在原设计、本计划新增**：Task 13（锁，决策 #1）、Task 14（指令面，决策 #3）——均已在顶部标为待确认。
- **类型一致**：全程 `bot_dir(gid, bot_id)`、`group_shared_dir(gid)`、`_get_effective_ws(bot_id, path, group_id)`、VFS `(..., group_id=)`。
- **过渡可运行**：Task 3 垫片 → Task 10 移除，保证 Task 4-9 每步系统可跑、可分步提交。
