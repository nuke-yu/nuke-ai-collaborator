# 技能分层重做 — Plan 1：后端地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 356 行的 `backend/skills/discovery.py` 行为等价地拆成分层的 `SkillSource` 架构，新增统一的 `SkillScope`/`SkillStore` 写接口，并把 L3 角色技能来源从全局目录切到群内目录——全部不破坏对外 API。

**Architecture:** 读路径拆成"每层一个 `SkillSource`（只懂自己怎么枚举+签名）+ 一个 `composer`（只懂跨层优先级）+ 一个 `cache`（只懂指纹失效）+ `discovery.py` 瘦门面（对外签名不变）"。写路径新增 `SkillScope`（作用域→目录）与 `SkillStore`（list/read/write/delete/copy 统一原语，含本期即实现的 `BotScope`）。最后一步才把 L3 由 `ROLES_ROOT/<role>` 翻成 `group_<id>/roles/<role>`。

**Tech Stack:** Python 3（命令为 `python3`）· unittest · 现有 `backend/skills/*`、`backend/workspace/layout.py`。

## Global Constraints

- 测试命令一律 `python3 -m pytest <file> -v`（不是 `python3.11`）；只跑与改动相关的 test 文件。
- Git commit：作者只显示 `nuke`，**禁止任何 `Co-Authored-By` / AI 署名**；message 干净只描述改动。
- 对外 API（`list_skills_all` / `list_skills` / `load_always_skills` / `run_skill` / `invalidate_skills_cache`）**签名与返回结构不得改变**；本 Plan 的拆分对调用方零感知。
- **行为等价先行**：Task 1–10 不得改变任何外部可观察行为，`backend/tests/test_skills_*.py` 全程保持绿；唯一的行为变更（L3 群内化）集中在 Task 11–12。
- 技能恒为文件；不引入 DB 存技能。
- 路径解析一律走 `workspace/layout.py`（布局单一真相源），不在别处硬拼 `group_<id>` 路径。
- 每个 `SkillEntry` 的字段集合与现状 `_scan_dir_sync` / `parse_skill_meta` 产出的 dict 完全一致（`name/type/path/layer/description/always/status/when_to_use/learns/is_stub/fm_keys/...`）。

---

## File Structure

```
backend/skills/
├── sources/
│   ├── __init__.py        # 导出各 source 类
│   ├── base.py            # SkillSource 协议 + SkillEntry 类型别名
│   ├── system.py          # SystemPoolSource   (L1)
│   ├── group.py           # GroupSource        (L2)
│   ├── role.py            # RoleSource         (L3)
│   └── learned.py         # LearnedSource      (L4: personal + learned/active + draft)
├── composer.py            # merge_layers(): A1 保护 / A3 深合并 / A5 诊断 / injected / 排序 / draft
├── cache.py               # CachedScan: 聚合各 source.signature() 指纹 + 缓存
├── scope.py               # SkillScope 类型 + parse_descriptor() + resolve()
├── store.py               # SkillStore: list/read/write/delete/copy
├── discovery.py           # 瘦门面：装配 sources → composer → cache（对外 API 不变）
├── constants.py           # 改：ROLES_ROOT → TEMPLATES_ROOT
└── loader.py              # 改：_skills_dir_for_layer 的 L3 分支（Task 12）

backend/workspace/layout.py # 加 group_roles_dir / templates_roles_dir

backend/tests/
├── test_skill_layout_paths.py     # 新（Task 1）
├── test_skill_sources.py          # 新（Task 3-6）
├── test_skill_composer.py         # 新（Task 7）
├── test_skill_scope.py            # 新（Task 9）
├── test_skill_store.py            # 新（Task 10）
└── test_skills_group_path.py      # 改（Task 12）
```

**拆分策略：搬运而非重写。** 现有 `_scan_dir_sync` / `_scan_personal_layer_sync` / `_merge_skill_entry` / `_compute_skills_all` / `_scan_signature` 的**逻辑原样迁入**新模块，仅改变它们的归属与组织。这把回归风险降到最低，也让"行为等价"可由现存测试守住。

---

### Task 1: layout 路径助手 + TEMPLATES_ROOT

**Files:**
- Modify: `backend/workspace/layout.py`（在 `group_runs_dir` 后追加两个函数）
- Modify: `backend/skills/constants.py:7`
- Test: `backend/tests/test_skill_layout_paths.py`（新建）

**Interfaces:**
- Produces:
  - `layout.group_roles_dir(gid: int) -> Path` → `group_dir(gid)/"roles"`
  - `layout.templates_roles_dir(lang: str) -> Path` → `_root()/"templates"/lang/"roles"`
  - `constants.TEMPLATES_ROOT: Path` = `WORKSPACE_ROOT/"templates"`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_skill_layout_paths.py
import unittest
from pathlib import Path
from unittest.mock import patch
from workspace import layout


class TestLayoutRolePaths(unittest.TestCase):
    def test_group_roles_dir(self):
        with patch("skills.constants.WORKSPACE_ROOT", Path("/ws")):
            self.assertEqual(layout.group_roles_dir(7), Path("/ws/group_7/roles"))

    def test_templates_roles_dir(self):
        with patch("skills.constants.WORKSPACE_ROOT", Path("/ws")):
            self.assertEqual(layout.templates_roles_dir("en"), Path("/ws/templates/en/roles"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_layout_paths.py -v`
Expected: FAIL — `AttributeError: module 'workspace.layout' has no attribute 'group_roles_dir'`

- [ ] **Step 3: Add the helpers**

In `backend/workspace/layout.py`, after `group_runs_dir`:

```python
def group_roles_dir(gid: int) -> Path:
    return group_dir(gid) / "roles"


def templates_roles_dir(lang: str) -> Path:
    return _root() / "templates" / lang / "roles"
```

In `backend/skills/constants.py`, change line 7 from:

```python
ROLES_ROOT = WORKSPACE_ROOT / "roles"
```

to:

```python
ROLES_ROOT = WORKSPACE_ROOT / "roles"            # legacy global roles (migration source only; not scanned at runtime after Plan 1)
TEMPLATES_ROOT = WORKSPACE_ROOT / "templates"    # global role templates root (copied into groups on creation)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_layout_paths.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/workspace/layout.py backend/skills/constants.py backend/tests/test_skill_layout_paths.py
git commit -m "feat(skills): add group_roles_dir/templates_roles_dir layout helpers + TEMPLATES_ROOT"
```

---

### Task 2: SkillSource 协议 + SkillEntry 类型

**Files:**
- Create: `backend/skills/sources/__init__.py`
- Create: `backend/skills/sources/base.py`
- Test: 无独立测试（协议无运行逻辑；由 Task 3 起的 source 测试覆盖）

**Interfaces:**
- Produces:
  - `SkillEntry = Dict[str, Any]`（与现状技能 dict 同构）
  - `class SkillSource(Protocol)`: `def enumerate(self) -> list[SkillEntry]`; `def signature(self) -> tuple`
  - `class ScanCtx`（dataclass，frozen）: `bot_id: int`, `group_id: int | None`, `role: str | None`

- [ ] **Step 1: Create base.py**

```python
# backend/skills/sources/base.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, runtime_checkable

SkillEntry = Dict[str, Any]


@dataclass(frozen=True)
class ScanCtx:
    bot_id: int
    group_id: int | None = None
    role: str | None = None


@runtime_checkable
class SkillSource(Protocol):
    """One layer's read side: knows only how to enumerate its own skills and
    fingerprint its own files. Knows nothing about merging or other layers."""

    layer: str

    def enumerate(self) -> List[SkillEntry]: ...

    def signature(self) -> tuple: ...
```

- [ ] **Step 2: Create the package __init__**

```python
# backend/skills/sources/__init__.py
from .base import SkillSource, SkillEntry, ScanCtx

__all__ = ["SkillSource", "SkillEntry", "ScanCtx"]
```

- [ ] **Step 3: Verify import works**

Run: `cd backend && python3 -c "from skills.sources import SkillSource, ScanCtx; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/skills/sources/__init__.py backend/skills/sources/base.py
git commit -m "feat(skills): add SkillSource protocol + SkillEntry/ScanCtx types"
```

---

### Task 3: SystemPoolSource (L1) + 共享扫描助手

**Files:**
- Create: `backend/skills/sources/_scan.py`（容纳从 discovery 搬来的 `_scan_dir_sync`）
- Create: `backend/skills/sources/system.py`
- Modify: `backend/skills/sources/__init__.py`
- Test: `backend/tests/test_skill_sources.py`（新建）

**Interfaces:**
- Consumes: `ScanCtx`, `parse_skill_meta`（既有 `skills.metadata`）
- Produces:
  - `scan_dir(path: Path, layer: str) -> list[SkillEntry]`（行为同现 `discovery._scan_dir_sync`）
  - `dir_signature(path: Path) -> list[tuple]`（某目录下 `.md/.py` 的 `(fp, mtime_ns, size)`）
  - `class SystemPoolSource(ctx)`，`layer="system"`，扫 `SYSTEM_SKILLS_ROOT`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_skill_sources.py
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from skills.sources.base import ScanCtx


class TestSystemSource(unittest.TestCase):
    def test_enumerate_lists_system_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            sysdir = Path(tmp) / "system" / "skills"
            sysdir.mkdir(parents=True)
            (sysdir / "read-file.md").write_text(
                "---\nname: read-file\ndescription: reads\n---\nbody", encoding="utf-8")
            with patch("skills.constants.SYSTEM_SKILLS_ROOT", sysdir), \
                 patch("skills.sources.system.SYSTEM_SKILLS_ROOT", sysdir):
                from skills.sources.system import SystemPoolSource
                src = SystemPoolSource(ScanCtx(bot_id=1))
                names = [s["name"] for s in src.enumerate()]
                self.assertIn("read-file", names)
                self.assertTrue(any("read-file.md" in str(p) for p in src.signature()))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.sources.system'`

- [ ] **Step 3: Create _scan.py (move logic from discovery)**

Copy the body of `discovery._scan_dir_sync` verbatim into `scan_dir`, and add `dir_signature`:

```python
# backend/skills/sources/_scan.py
import os
from pathlib import Path
from typing import List
from ..metadata import parse_skill_meta
from .base import SkillEntry


def scan_dir(path: Path, layer: str) -> List[SkillEntry]:
    """Identical to the historical discovery._scan_dir_sync."""
    if not path.exists():
        return []
    seen: set = set()
    result: List[SkillEntry] = []
    for p in sorted(path.iterdir()):
        if p.is_dir():
            sf = p / "SKILL.md"
            if sf.exists() and p.name not in seen:
                seen.add(p.name)
                meta = parse_skill_meta(sf)
                meta["layer"] = meta.get("layer") or layer
                result.append({"name": p.name, "type": "md", "path": sf, **meta})
        elif p.suffix == ".md" and p.stem not in seen:
            seen.add(p.stem)
            meta = parse_skill_meta(p)
            meta["layer"] = meta.get("layer") or layer
            result.append({"name": p.stem, "type": "md", "path": p, **meta})
        elif p.suffix == ".py" and p.stem not in seen:
            seen.add(p.stem)
            result.append({
                "name": p.stem, "type": "py", "layer": layer,
                "description": "(代码技能)", "always": False,
                "status": "active", "when_to_use": "", "learns": False,
                "is_stub": False, "fm_keys": [], "path": p
            })
    return result


def dir_signature(path: Path) -> list:
    sig = []
    if not path.exists():
        return sig
    for root, _, files in os.walk(path):
        for fn in files:
            if fn.endswith((".md", ".py")):
                fp = os.path.join(root, fn)
                try:
                    st = os.stat(fp)
                    sig.append((fp, st.st_mtime_ns, st.st_size))
                except OSError:
                    continue
    return sig
```

- [ ] **Step 4: Create system.py**

```python
# backend/skills/sources/system.py
from typing import List
from ..constants import SYSTEM_SKILLS_ROOT
from .base import ScanCtx, SkillEntry
from ._scan import scan_dir, dir_signature


class SystemPoolSource:
    layer = "system"

    def __init__(self, ctx: ScanCtx):
        self.ctx = ctx

    def enumerate(self) -> List[SkillEntry]:
        return scan_dir(SYSTEM_SKILLS_ROOT, "system")

    def signature(self) -> tuple:
        return tuple(dir_signature(SYSTEM_SKILLS_ROOT))
```

Append to `backend/skills/sources/__init__.py`:

```python
from .system import SystemPoolSource
__all__.append("SystemPoolSource")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_sources.py -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/skills/sources/_scan.py backend/skills/sources/system.py backend/skills/sources/__init__.py backend/tests/test_skill_sources.py
git commit -m "feat(skills): add SystemPoolSource (L1) + shared scan_dir/dir_signature helpers"
```

---

### Task 4: GroupSource (L2)

**Files:**
- Create: `backend/skills/sources/group.py`
- Modify: `backend/skills/sources/__init__.py`
- Test: `backend/tests/test_skill_sources.py`（追加用例）

**Interfaces:**
- Consumes: `ScanCtx`, `scan_dir`, `dir_signature`, `layout.group_shared_dir`
- Produces: `class GroupSource(ctx)`, `layer="group"`，扫 `group_shared_dir(gid)/"skills"`；`group_id is None` → 空

- [ ] **Step 1: Write the failing test (append to test_skill_sources.py)**

```python
class TestGroupSource(unittest.TestCase):
    def test_enumerate_group_skills_under_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("skills.constants.WORKSPACE_ROOT", Path(tmp)):
                from workspace import layout
                gdir = layout.group_shared_dir(3) / "skills"
                gdir.mkdir(parents=True)
                (gdir / "house-style.md").write_text(
                    "---\nname: house-style\ndescription: x\n---\nb", encoding="utf-8")
                from skills.sources.group import GroupSource
                from skills.sources.base import ScanCtx
                src = GroupSource(ScanCtx(bot_id=1, group_id=3))
                self.assertEqual([s["name"] for s in src.enumerate()], ["house-style"])
                self.assertEqual(src.enumerate()[0]["layer"], "group")

    def test_no_group_id_is_empty(self):
        from skills.sources.group import GroupSource
        from skills.sources.base import ScanCtx
        src = GroupSource(ScanCtx(bot_id=1, group_id=None))
        self.assertEqual(src.enumerate(), [])
        self.assertEqual(src.signature(), ())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_sources.py::TestGroupSource -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.sources.group'`

- [ ] **Step 3: Create group.py**

```python
# backend/skills/sources/group.py
from typing import List
from workspace import layout
from .base import ScanCtx, SkillEntry
from ._scan import scan_dir, dir_signature


class GroupSource:
    layer = "group"

    def __init__(self, ctx: ScanCtx):
        self.ctx = ctx

    def _dir(self):
        if not self.ctx.group_id:
            return None
        return layout.group_shared_dir(self.ctx.group_id) / "skills"

    def enumerate(self) -> List[SkillEntry]:
        d = self._dir()
        return scan_dir(d, "group") if d else []

    def signature(self) -> tuple:
        d = self._dir()
        return tuple(dir_signature(d)) if d else ()
```

Append to `__init__.py`:

```python
from .group import GroupSource
__all__.append("GroupSource")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_sources.py::TestGroupSource -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/skills/sources/group.py backend/skills/sources/__init__.py backend/tests/test_skill_sources.py
git commit -m "feat(skills): add GroupSource (L2)"
```

---

### Task 5: RoleSource (L3) — 行为等价（暂用全局 ROLES_ROOT）

**Files:**
- Create: `backend/skills/sources/role.py`
- Modify: `backend/skills/sources/__init__.py`
- Test: `backend/tests/test_skill_sources.py`（追加）

> **关键：本任务保持现有行为**——L3 仍读全局 `ROLES_ROOT/<role>/skills`。群内化在 Task 12 才翻转。

**Interfaces:**
- Consumes: `ScanCtx`, `scan_dir`, `dir_signature`, `constants.ROLES_ROOT`
- Produces: `class RoleSource(ctx)`, `layer="role"`；`role is None` → 空

- [ ] **Step 1: Write the failing test (append)**

```python
class TestRoleSource(unittest.TestCase):
    def test_enumerate_role_skills_global_for_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            rdir = Path(tmp) / "roles" / "dev" / "skills"
            rdir.mkdir(parents=True)
            (rdir / "code-review.md").write_text(
                "---\nname: code-review\ndescription: x\n---\nb", encoding="utf-8")
            with patch("skills.sources.role.ROLES_ROOT", Path(tmp) / "roles"):
                from skills.sources.role import RoleSource
                from skills.sources.base import ScanCtx
                src = RoleSource(ScanCtx(bot_id=1, group_id=3, role="dev"))
                self.assertEqual([s["name"] for s in src.enumerate()], ["code-review"])

    def test_no_role_is_empty(self):
        from skills.sources.role import RoleSource
        from skills.sources.base import ScanCtx
        src = RoleSource(ScanCtx(bot_id=1, group_id=3, role=None))
        self.assertEqual(src.enumerate(), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_sources.py::TestRoleSource -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.sources.role'`

- [ ] **Step 3: Create role.py (global path, behavior-equivalent)**

```python
# backend/skills/sources/role.py
from typing import List
from ..constants import ROLES_ROOT
from .base import ScanCtx, SkillEntry
from ._scan import scan_dir, dir_signature


class RoleSource:
    layer = "role"

    def __init__(self, ctx: ScanCtx):
        self.ctx = ctx

    def _dir(self):
        if not self.ctx.role:
            return None
        # NOTE: still the global ROLES_ROOT here — flipped to group-internal in Task 12.
        return ROLES_ROOT / self.ctx.role / "skills"

    def enumerate(self) -> List[SkillEntry]:
        d = self._dir()
        return scan_dir(d, "role") if d else []

    def signature(self) -> tuple:
        d = self._dir()
        return tuple(dir_signature(d)) if d else ()
```

Append to `__init__.py`:

```python
from .role import RoleSource
__all__.append("RoleSource")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_sources.py::TestRoleSource -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/skills/sources/role.py backend/skills/sources/__init__.py backend/tests/test_skill_sources.py
git commit -m "feat(skills): add RoleSource (L3, global path — behavior-equivalent)"
```

---

### Task 6: LearnedSource (L4) — personal + learned/active + draft

**Files:**
- Create: `backend/skills/sources/learned.py`
- Modify: `backend/skills/sources/__init__.py`
- Test: `backend/tests/test_skill_sources.py`（追加）

> 搬运 `discovery._scan_personal_layer_sync` 与 draft 扫描逻辑。LearnedSource 产出三段：`learned/active`（status=active）、personal（manual+root）、`learned/draft`（status=draft，带 diagnostics）。**为保持 composer 与现 `_compute_skills_all` 完全一致的合并顺序，LearnedSource.enumerate() 返回一个有序结构**：`{"active": [...], "personal": {name: entry}, "draft": [...]}`。

**Interfaces:**
- Consumes: `ScanCtx`, `scan_dir`, `dir_signature`, `layout.bot_dir`, `parse_skill_meta`
- Produces:
  - `class LearnedSource(ctx)`, `layer="learned"`
  - `enumerate() -> dict`：键 `active: list[SkillEntry]`、`personal: dict[str, SkillEntry]`、`draft: list[SkillEntry]`
  - `signature() -> tuple`

- [ ] **Step 1: Write the failing test (append)**

```python
class TestLearnedSource(unittest.TestCase):
    def test_active_personal_draft_partitioned(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("skills.constants.WORKSPACE_ROOT", Path(tmp)):
                from workspace import layout
                base = layout.bot_dir(3, 7) / "skills"
                (base / "learned" / "active").mkdir(parents=True)
                (base / "learned" / "active" / "a.md").write_text(
                    "---\nname: a\ndescription: x\n---\nb", encoding="utf-8")
                (base / "manual").mkdir(parents=True)
                (base / "manual" / "p.md").write_text(
                    "---\nname: p\ndescription: x\n---\nb", encoding="utf-8")
                (base / "learned" / "draft").mkdir(parents=True)
                (base / "learned" / "draft" / "d.md").write_text(
                    "---\nname: d\ndescription: x\n---\nb", encoding="utf-8")
                from skills.sources.learned import LearnedSource
                from skills.sources.base import ScanCtx
                out = LearnedSource(ScanCtx(bot_id=7, group_id=3)).enumerate()
                self.assertEqual([s["name"] for s in out["active"]], ["a"])
                self.assertIn("p", out["personal"])
                self.assertEqual([s["name"] for s in out["draft"]], ["d"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_sources.py::TestLearnedSource -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.sources.learned'`

- [ ] **Step 3: Create learned.py (move _scan_personal_layer_sync + draft scan)**

```python
# backend/skills/sources/learned.py
from typing import Dict, List
from pathlib import Path
from workspace import layout
from ..metadata import parse_skill_meta
from .base import ScanCtx, SkillEntry
from ._scan import scan_dir, dir_signature


def _scan_personal(skills_dir: Path) -> Dict[str, SkillEntry]:
    """Verbatim port of discovery._scan_personal_layer_sync."""
    personal: Dict[str, SkillEntry] = {}
    if not skills_dir.exists():
        return personal

    def scan(dir_to_scan: Path):
        if not dir_to_scan.exists():
            return
        for p in sorted(dir_to_scan.iterdir()):
            if p.is_dir():
                if p.name in ("learned", "manual"):
                    continue
                sf = p / "SKILL.md"
                if sf.exists() and p.name not in personal:
                    meta = parse_skill_meta(sf)
                    meta["layer"] = meta.get("layer") or "personal"
                    personal[p.name] = {"name": p.name, "type": "md", "path": sf, **meta}
            elif p.suffix == ".md" and p.stem not in personal:
                meta = parse_skill_meta(p)
                meta["layer"] = meta.get("layer") or "personal"
                personal[p.stem] = {"name": p.stem, "type": "md", "path": p, **meta}
            elif p.suffix == ".py" and p.stem not in personal:
                personal[p.stem] = {
                    "name": p.stem, "type": "py", "layer": "personal",
                    "description": "(代码技能)", "always": False,
                    "status": "active", "when_to_use": "", "learns": False,
                    "is_stub": False, "fm_keys": [], "path": p
                }

    scan(skills_dir / "manual")
    scan(skills_dir)
    return personal


class LearnedSource:
    layer = "learned"

    def __init__(self, ctx: ScanCtx):
        self.ctx = ctx
        self._base = layout.bot_dir(ctx.group_id, ctx.bot_id) / "skills"

    def enumerate(self) -> dict:
        active = scan_dir(self._base / "learned" / "active", "learned")
        for s in active:
            s["status"] = "active"
        personal = _scan_personal(self._base)
        draft = scan_dir(self._base / "learned" / "draft", "learned")
        for s in draft:
            s["status"] = "draft"
        return {"active": active, "personal": personal, "draft": draft}

    def signature(self) -> tuple:
        return tuple(dir_signature(self._base))
```

Append to `__init__.py`:

```python
from .learned import LearnedSource
__all__.append("LearnedSource")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_sources.py::TestLearnedSource -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/skills/sources/learned.py backend/skills/sources/__init__.py backend/tests/test_skill_sources.py
git commit -m "feat(skills): add LearnedSource (L4 active/personal/draft)"
```

---

### Task 7: composer — 跨层合并/保护/覆盖/injected/draft 诊断

**Files:**
- Create: `backend/skills/composer.py`
- Test: `backend/tests/test_skill_composer.py`（新建）

> 搬运 `discovery._merge_skill_entry` 全文，以及 `_compute_skills_all` 末段的 draft 诊断（C1/C2）、injected 计算、`_LAYER_ORDER` 排序。composer 接收"已枚举的各层结果"，不碰磁盘。

**Interfaces:**
- Consumes: `SkillEntry`, `constants.SYSTEM_SKILLS_ROOT`
- Produces:
  - `merge_layers(system: list, group: list, role: list, learned: dict) -> list[SkillEntry]`
    顺序：system → group → role → learned["active"] → learned["personal"] → 计算 injected/排序 → 追加 learned["draft"]（带 diagnostics）。返回结构与现 `_compute_skills_all` 完全一致。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_skill_composer.py
import unittest
from pathlib import Path
from skills.composer import merge_layers


def entry(name, layer, **kw):
    base = {"name": name, "layer": layer, "type": "md", "path": Path(f"/x/{layer}/{name}.md"),
            "description": "", "always": False, "status": "active", "when_to_use": "",
            "learns": False, "is_stub": False, "fm_keys": []}
    base.update(kw)
    return base


class TestComposer(unittest.TestCase):
    def test_later_layer_overrides_and_injected_computed(self):
        out = merge_layers(
            system=[entry("read-file", "system")],
            group=[entry("house", "group", always=True)],
            role=[entry("code-review", "role")],
            learned={"active": [], "personal": {}, "draft": []},
        )
        by = {s["name"]: s for s in out}
        self.assertEqual(by["read-file"]["injected"], "metadata")
        self.assertEqual(by["house"]["injected"], "full")   # always -> full

    def test_system_protected_from_shadow(self):
        out = merge_layers(
            system=[entry("read-file", "system", description="SYS")],
            group=[entry("read-file", "group", description="GROUP")],
            role=[], learned={"active": [], "personal": {}, "draft": []},
        )
        by = {s["name"]: s for s in out}
        self.assertEqual(by["read-file"]["description"], "SYS")  # group cannot shadow system

    def test_disabled_not_injected(self):
        out = merge_layers(
            system=[entry("x", "system", status="disabled")],
            group=[], role=[], learned={"active": [], "personal": {}, "draft": []},
        )
        self.assertIsNone(out[0]["injected"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_composer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.composer'`

- [ ] **Step 3: Create composer.py**

Port `_merge_skill_entry` verbatim (it already lives in discovery.py:187-244), then the tail of `_compute_skills_all` (lines 296-356: draft diagnostics C1/C2, injected calc, sort). Skeleton:

```python
# backend/skills/composer.py
import logging
from pathlib import Path
from typing import Dict, List
from .constants import SYSTEM_SKILLS_ROOT
from .sources.base import SkillEntry

log = logging.getLogger(__name__)

_LAYER_ORDER = {"system": 0, "group": 1, "role": 2, "learned": 3, "personal": 4}


def _merge_skill_entry(merged: Dict[str, SkillEntry], incoming: SkillEntry) -> None:
    # >>> paste the EXACT body of discovery._merge_skill_entry (discovery.py:187-244)
    ...


def _draft_diagnostics(s: SkillEntry, merged: Dict[str, SkillEntry]) -> list:
    # >>> paste the C1 (collision) + C2 (privilege) logic from discovery.py:302-338
    ...


def merge_layers(system, group, role, learned) -> List[SkillEntry]:
    merged: Dict[str, SkillEntry] = {}
    for s in system:
        _merge_skill_entry(merged, s)
    for s in group:
        _merge_skill_entry(merged, s)
    for s in role:
        _merge_skill_entry(merged, s)
    for s in learned.get("active", []):
        _merge_skill_entry(merged, s)
    for name, s in learned.get("personal", {}).items():
        _merge_skill_entry(merged, s)

    result = []
    for s in merged.values():
        status = s.get("status", "active")
        if status in ("disabled", "deprecated"):
            s["injected"] = None
        elif s.get("always"):
            s["injected"] = "full"
        else:
            s["injected"] = "metadata"
        result.append(s)

    result.sort(key=lambda x: (_LAYER_ORDER.get(x.get("layer", ""), 5), x["name"]))

    for s in learned.get("draft", []):
        s["status"] = "draft"
        s["diagnostics"] = _draft_diagnostics(s, merged)
        result.append(s)
    return result
```

> 实现者注意：`_merge_skill_entry` 与 `_draft_diagnostics` 必须**逐字**搬自 discovery.py 当前实现（分别在 :187-244 与 :302-338），不得改写逻辑——这是行为等价的核心。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_composer.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/skills/composer.py backend/tests/test_skill_composer.py
git commit -m "feat(skills): add composer (merge/protect/override/injected/draft) ported from discovery"
```

---

### Task 8: cache — 聚合各 source 签名 + 缓存

**Files:**
- Create: `backend/skills/cache.py`
- Test: 由 Task 9 的门面回归覆盖（缓存命中/失效行为通过 discovery 对外测试守住）

**Interfaces:**
- Consumes: `ScanCtx`, source 实例的 `.signature()`
- Produces:
  - `class CachedScan`: `get(key: tuple, sig: tuple, compute: Callable[[], list]) -> list`（命中 sig 相同则返回深拷贝，否则 compute 并存）
  - `clear()`（供 `invalidate_skills_cache` 调用）

- [ ] **Step 1: Create cache.py**

```python
# backend/skills/cache.py
import threading
from typing import Callable, Dict, List, Tuple


class CachedScan:
    def __init__(self):
        self._cache: Dict[tuple, Tuple[tuple, list]] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def get(self, key: tuple, sig: tuple, compute: Callable[[], List[dict]]) -> List[dict]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and entry[0] == sig:
                return [dict(s) for s in entry[1]]
        result = compute()
        with self._lock:
            self._cache[key] = (sig, [dict(s) for s in result])
        return result
```

- [ ] **Step 2: Verify import + basic behavior**

Run:
```bash
cd backend && python3 -c "
from skills.cache import CachedScan
c = CachedScan()
calls = []
def compute():
    calls.append(1); return [{'name':'x'}]
a = c.get(('k',), (1,), compute)
b = c.get(('k',), (1,), compute)   # same sig -> no recompute
d = c.get(('k',), (2,), compute)   # new sig -> recompute
print(len(calls), a==b==[{'name':'x'}])
assert len(calls) == 2
print('ok')
"
```
Expected: ends with `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/skills/cache.py
git commit -m "feat(skills): add CachedScan (signature-keyed scan cache)"
```

---

### Task 9: discovery.py 门面重写 — 装配 sources→composer→cache（行为等价回归）

**Files:**
- Modify: `backend/skills/discovery.py`（重写 `_compute_skills_all` / `_scan_signature` / `_list_skills_all_sync` / `invalidate_skills_cache` 内部；对外函数签名不变）
- Test: 全量回归 `backend/tests/test_skills_*.py`

**Interfaces:**
- Produces (unchanged signatures): `list_skills_all`, `list_skills`, `_list_skills_all_sync`, `_scan_signature`, `invalidate_skills_cache`

- [ ] **Step 1: Rewrite discovery internals to delegate**

Replace the module-level cache + `_scan_signature` + `_compute_skills_all` + `_list_skills_all_sync` with delegation, keeping `_list_skills_sync` / `list_skills` as-is:

```python
from .sources.base import ScanCtx
from .sources.system import SystemPoolSource
from .sources.group import GroupSource
from .sources.role import RoleSource
from .sources.learned import LearnedSource
from .composer import merge_layers
from .cache import CachedScan

_SCAN_CACHE = CachedScan()


def invalidate_skills_cache() -> None:
    _SCAN_CACHE.clear()


def _sources(ctx: ScanCtx):
    return SystemPoolSource(ctx), GroupSource(ctx), RoleSource(ctx), LearnedSource(ctx)


def _scan_signature(bot_id, group_id=None, role=None) -> tuple:
    ctx = ScanCtx(bot_id, group_id, role)
    sysm, grp, rol, lrn = _sources(ctx)
    sig = list(sysm.signature()) + list(grp.signature()) + list(rol.signature()) + list(lrn.signature())
    return tuple(sorted(sig))


def _compute_skills_all(bot_id, group_id=None, role=None):
    ctx = ScanCtx(bot_id, group_id, role)
    sysm, grp, rol, lrn = _sources(ctx)
    return merge_layers(sysm.enumerate(), grp.enumerate(), rol.enumerate(), lrn.enumerate())


def _list_skills_all_sync(bot_id, group_id=None, role=None):
    key = (bot_id, group_id, role)
    sig = _scan_signature(bot_id, group_id, role)
    return _SCAN_CACHE.get(key, sig, lambda: _compute_skills_all(bot_id, group_id, role))
```

Delete the now-dead `_scan_dir_sync`, `_scan_personal_layer_sync`, `_merge_skill_entry`, the old `_SKILLS_CACHE`/`_CACHE_LOCK`, and the old `_scan_signature` body from discovery.py. Keep `list_skills_all`, `list_skills`, `_list_skills_sync` unchanged.

> 注意：旧 `_scan_signature` 把 personal 整个 `bot_ws/skills` 目录纳入指纹；`LearnedSource.signature()` 对同一目录做 `dir_signature`，覆盖 active/manual/draft，等价。system/group/role 旧实现只在 `_compute` 时扫、签名里**也**含它们（旧 `_scan_signature` 遍历 dirs 列表含 system/group/role/personal）——新实现逐 source 累加，集合一致。

- [ ] **Step 2: Run the FULL skills regression**

Run: `cd backend && python3 -m pytest tests/test_skills_a1_a3.py tests/test_skills_group_path.py tests/test_skill_frontmatter.py tests/test_skill_fixes.py tests/test_skill_watcher.py tests/test_fork_skill_usage.py tests/test_skill_selfwrite.py tests/test_skill_no_shell_exec.py -v`
Expected: PASS（全绿；若任一红，说明搬运偏离了原逻辑，对照 discovery 旧实现逐字校正——不得改测试）

- [ ] **Step 3: Sanity-check the watcher still invalidates**

Run: `cd backend && python3 -c "from skills.discovery import invalidate_skills_cache; invalidate_skills_cache(); print('ok')"`
Expected: `ok`（`watcher.py` 调用的是同名函数，签名不变）

- [ ] **Step 4: Commit**

```bash
git add backend/skills/discovery.py
git commit -m "refactor(skills): discovery becomes thin facade over sources+composer+cache (behavior-equivalent)"
```

---

### Task 10: SkillScope — 作用域类型 + descriptor 解析

**Files:**
- Create: `backend/skills/scope.py`
- Test: `backend/tests/test_skill_scope.py`（新建）

**Interfaces:**
- Consumes: `layout`, `constants.SYSTEM_SKILLS_ROOT`
- Produces:
  - dataclasses: `SystemScope()`, `GroupScope(gid)`, `RoleScope(gid, role)`, `TemplateScope(lang, role)`, `BotScope(gid, bot_id)`
  - `scope.dir() -> Path`（每个 scope 的方法）
  - `parse_descriptor(s: str) -> Scope`（`"system"`, `"group:7"`, `"role:7:dev"`, `"template:en:PM"`, `"bot:7:1018"`）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_skill_scope.py
import unittest
from pathlib import Path
from unittest.mock import patch
from skills import scope as S


class TestScope(unittest.TestCase):
    def test_dirs(self):
        with patch("skills.constants.WORKSPACE_ROOT", Path("/ws")), \
             patch("skills.constants.SYSTEM_SKILLS_ROOT", Path("/ws/system/skills")):
            self.assertEqual(S.GroupScope(7).dir(), Path("/ws/group_7/shared/skills"))
            self.assertEqual(S.RoleScope(7, "dev").dir(), Path("/ws/group_7/roles/dev/skills"))
            self.assertEqual(S.TemplateScope("en", "PM").dir(), Path("/ws/templates/en/roles/PM/skills"))
            self.assertEqual(S.BotScope(7, 1018).dir(), Path("/ws/group_7/bots/bot_1018/skills/manual"))

    def test_parse_descriptor(self):
        self.assertEqual(S.parse_descriptor("group:7"), S.GroupScope(7))
        self.assertEqual(S.parse_descriptor("role:7:dev"), S.RoleScope(7, "dev"))
        self.assertEqual(S.parse_descriptor("bot:7:1018"), S.BotScope(7, 1018))

    def test_parse_invalid_raises(self):
        with self.assertRaises(ValueError):
            S.parse_descriptor("bogus:1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_scope.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.scope'`

- [ ] **Step 3: Create scope.py**

```python
# backend/skills/scope.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from workspace import layout
from .constants import SYSTEM_SKILLS_ROOT


@dataclass(frozen=True)
class SystemScope:
    def dir(self) -> Path:
        return SYSTEM_SKILLS_ROOT


@dataclass(frozen=True)
class GroupScope:
    gid: int
    def dir(self) -> Path:
        return layout.group_shared_dir(self.gid) / "skills"


@dataclass(frozen=True)
class RoleScope:
    gid: int
    role: str
    def dir(self) -> Path:
        return layout.group_roles_dir(self.gid) / self.role / "skills"


@dataclass(frozen=True)
class TemplateScope:
    lang: str
    role: str
    def dir(self) -> Path:
        return layout.templates_roles_dir(self.lang) / self.role / "skills"


@dataclass(frozen=True)
class BotScope:
    gid: int
    bot_id: int
    def dir(self) -> Path:
        return layout.bot_dir(self.gid, self.bot_id) / "skills" / "manual"


def parse_descriptor(s: str):
    parts = s.split(":")
    kind = parts[0]
    try:
        if kind == "system":
            return SystemScope()
        if kind == "group":
            return GroupScope(int(parts[1]))
        if kind == "role":
            return RoleScope(int(parts[1]), parts[2])
        if kind == "template":
            return TemplateScope(parts[1], parts[2])
        if kind == "bot":
            return BotScope(int(parts[1]), int(parts[2]))
    except (IndexError, ValueError) as e:
        raise ValueError(f"bad scope descriptor: {s!r}") from e
    raise ValueError(f"unknown scope kind: {kind!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_scope.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/skills/scope.py backend/tests/test_skill_scope.py
git commit -m "feat(skills): add SkillScope types + descriptor parsing (system/group/role/template/bot)"
```

---

### Task 11: SkillStore — list/read/write/delete/copy 统一原语

**Files:**
- Create: `backend/skills/store.py`
- Test: `backend/tests/test_skill_store.py`（新建）

**Interfaces:**
- Consumes: `scope` 模块、`skills.metadata._is_safe_name`、`skills.lifecycle.file_lock`、`scan_dir`
- Produces:
  - `class SkillStore`:
    - `list(scope) -> list[SkillEntry]`
    - `read(scope, name) -> str`
    - `write(scope, name, content) -> dict`（返回 `{"name", "high_privilege": [...]}`，沿用 C2 高权扫描）
    - `delete(scope, name) -> None`
    - `copy(src, name, dst) -> None`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_skill_store.py
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from skills.store import SkillStore
from skills import scope as S


class TestStore(unittest.TestCase):
    def _ws(self, tmp):
        return patch("skills.constants.WORKSPACE_ROOT", Path(tmp))

    def test_write_read_list_delete_group(self):
        with tempfile.TemporaryDirectory() as tmp, self._ws(tmp):
            st = SkillStore()
            sc = S.GroupScope(7)
            st.write(sc, "house-style", "---\nname: house-style\ndescription: x\n---\nbody")
            self.assertIn("house-style", st.read(sc, "house-style"))
            self.assertEqual([s["name"] for s in st.list(sc)], ["house-style"])
            st.delete(sc, "house-style")
            self.assertEqual(st.list(sc), [])

    def test_copy_template_to_role(self):
        with tempfile.TemporaryDirectory() as tmp, self._ws(tmp):
            st = SkillStore()
            src, dst = S.TemplateScope("zh", "dev"), S.RoleScope(7, "dev")
            st.write(src, "code-review", "---\nname: code-review\ndescription: x\n---\nb")
            st.copy(src, "code-review", dst)
            self.assertEqual([s["name"] for s in st.list(dst)], ["code-review"])

    def test_write_flags_high_privilege(self):
        with tempfile.TemporaryDirectory() as tmp, self._ws(tmp):
            st = SkillStore()
            res = st.write(S.BotScope(7, 9), "danger",
                           "---\nname: danger\n---\nuse run_shell to do x")
            self.assertIn("run_shell", res["high_privilege"])

    def test_reject_bad_name(self):
        with tempfile.TemporaryDirectory() as tmp, self._ws(tmp):
            with self.assertRaises(ValueError):
                SkillStore().write(S.GroupScope(7), "../evil", "x")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.store'`

- [ ] **Step 3: Create store.py**

```python
# backend/skills/store.py
import shutil
from pathlib import Path
from .metadata import _is_safe_name
from .lifecycle import file_lock
from .sources._scan import scan_dir

_HIGH_PRIVILEGE = ("run_shell", "write_file")


def _skill_file(scope, name: str) -> Path:
    return scope.dir() / f"{name}.md"


class SkillStore:
    def list(self, scope) -> list:
        return scan_dir(scope.dir(), getattr(scope, "layer", "scope"))

    def read(self, scope, name: str) -> str:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        return _skill_file(scope, name).read_text(encoding="utf-8")

    def write(self, scope, name: str, content: str) -> dict:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        fp = _skill_file(scope, name)
        with file_lock(fp):
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
        low = content.lower()
        flagged = [t for t in _HIGH_PRIVILEGE if t in low]
        return {"name": name, "high_privilege": flagged}

    def delete(self, scope, name: str) -> None:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        fp = _skill_file(scope, name)
        with file_lock(fp):
            if fp.exists():
                fp.unlink()

    def copy(self, src, name: str, dst) -> None:
        if not _is_safe_name(name):
            raise ValueError(f"unsafe skill name: {name!r}")
        s = _skill_file(src, name)
        d = _skill_file(dst, name)
        with file_lock(d):
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)   # copy2 preserves mtime
```

> 注：`scan_dir` 的第二参 `layer` 仅用于给 entry 打 `layer` 标签；`store.list` 用 `getattr(scope, "layer", "scope")`，对 store 用途无副作用。`SkillScope` dataclass 无 `layer` 属性，故回退 `"scope"`，不影响 store 调用方（仅取 name/path）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_store.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/skills/store.py backend/tests/test_skill_store.py
git commit -m "feat(skills): add SkillStore (list/read/write/delete/copy) over SkillScope, incl BotScope"
```

---

### Task 12: L3 群内化 — 翻转 RoleSource + loader，跨群隔离

**Files:**
- Modify: `backend/skills/sources/role.py`（`_dir` 改用 `layout.group_roles_dir`）
- Modify: `backend/skills/loader.py:28-29`（`_skills_dir_for_layer` 的 role 分支）
- Test: `backend/tests/test_skills_group_path.py`（追加 L3 群内化用例）

> **这是本 Plan 唯一的行为变更**：L3 从全局 `ROLES_ROOT/<role>` 翻到 `group_<id>/roles/<role>`。前 11 个任务已就绪，本任务一翻即生效。

**Interfaces:**
- Consumes: `layout.group_roles_dir`
- Produces: RoleSource 现解析 `group_roles_dir(gid)/<role>/skills`；`gid is None` → 空

- [ ] **Step 1: Write the failing test (append to test_skills_group_path.py)**

```python
    def test_l3_role_resolves_under_group_and_isolated(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from skills.sources.role import RoleSource
        from skills.sources.base import ScanCtx
        with tempfile.TemporaryDirectory() as tmp:
            with patch("skills.constants.WORKSPACE_ROOT", Path(tmp)):
                from workspace import layout
                rdir = layout.group_roles_dir(3) / "dev" / "skills"
                rdir.mkdir(parents=True)
                (rdir / "code-review.md").write_text(
                    "---\nname: code-review\ndescription: x\n---\nb", encoding="utf-8")
                # group 3 sees it
                src3 = RoleSource(ScanCtx(bot_id=7, group_id=3, role="dev"))
                self.assertEqual([s["name"] for s in src3.enumerate()], ["code-review"])
                # group 4 (no such dir) sees nothing — cross-group isolation
                src4 = RoleSource(ScanCtx(bot_id=7, group_id=4, role="dev"))
                self.assertEqual(src4.enumerate(), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skills_group_path.py::TestSkillsGroupPath::test_l3_role_resolves_under_group_and_isolated -v`
Expected: FAIL — RoleSource still reads global `ROLES_ROOT`, so group 4 also returns `code-review` (assert mismatch) or group 3 returns empty.

- [ ] **Step 3: Flip RoleSource._dir to group-internal**

In `backend/skills/sources/role.py`, replace imports + `_dir`:

```python
from workspace import layout
...
    def _dir(self):
        if not self.ctx.role or not self.ctx.group_id:
            return None
        return layout.group_roles_dir(self.ctx.group_id) / self.ctx.role / "skills"
```

Remove the now-unused `from ..constants import ROLES_ROOT`.

- [ ] **Step 4: Flip loader._skills_dir_for_layer role branch**

In `backend/skills/loader.py`, change line 28-29:

```python
    if layer == "role" and role and group_id:
        return layout.group_roles_dir(group_id) / role / "skills"
```

Add `from workspace import layout` at top of loader.py if not present, and drop `ROLES_ROOT` from its `constants` import.

- [ ] **Step 5: Run the new test + full skills regression**

Run: `cd backend && python3 -m pytest tests/test_skills_group_path.py tests/test_skills_a1_a3.py tests/test_skill_sources.py -v`
Expected: PASS（新用例通过；注意 Task 5 的 `TestRoleSource.test_enumerate_role_skills_global_for_now` 现在会失败——**更新它**为群内路径断言：把 fixture 改成在 `layout.group_roles_dir(3)/dev/skills` 下建文件、patch `WORKSPACE_ROOT`，断言 group 3 命中。改测试以反映新行为是正确的，因为行为已按设计变更。）

- [ ] **Step 6: Commit**

```bash
git add backend/skills/sources/role.py backend/skills/loader.py backend/tests/test_skills_group_path.py backend/tests/test_skill_sources.py
git commit -m "feat(skills): L3 role skills resolve under group_<id>/roles (cross-group isolation)"
```

---

## Self-Review

**Spec coverage（Plan 1 范围）**：
- SkillSource 分层（system/group/role/learned + composer + cache + 门面）→ Task 2–9 ✅
- 行为等价先行、L3 变更后叠 → Task 5（全局占位）+ Task 9（回归绿）+ Task 12（翻转）✅
- SkillScope + SkillStore + copy 原语 + BotScope 本期实现 → Task 10–11 ✅
- layout 路径助手 + TEMPLATES_ROOT/ROLES_ROOT 退役 → Task 1 ✅
- L3 群内化、跨群隔离 → Task 12 ✅
- **本 Plan 不含**：模板目录/迁移脚本/建群拷贝/三条 UI 链路/双语 → 留给 Plan 2、Plan 3（见下）。

**Placeholder scan**：composer.py 的 `_merge_skill_entry`/`_draft_diagnostics` 标注"逐字搬自 discovery.py:187-244 / :302-338"——这是搬运指令而非占位，源代码在仓库中可直接复制。其余步骤均含完整代码。

**Type consistency**：`ScanCtx(bot_id, group_id, role)` 全程一致；`SkillEntry=dict` 字段集与 `parse_skill_meta`/`scan_dir` 产出一致；`merge_layers(system, group, role, learned)` 中 `learned` 为 `{active,personal,draft}` dict，与 `LearnedSource.enumerate()` 返回一致；scope `.dir()` 在 scope.py 与 store.py 用法一致。

## 后续 Plan（不在本文件）

- **Plan 2 — 模板与迁移**：建 `templates/<lang>/roles/*`（zh + Architecture/PM）、`role.yaml`、建群拷贝（`SkillStore.copy(TemplateScope→RoleScope)` 挂 `ensure_group_db_ready`）、`migrate_role_skills.py`（建模板/灌老群/对齐老 bot/退役 `roles.legacy`）、英文技能正文骨架。
- **Plan 3 — API 与 UI 与双语**：scope 参数化 `/api/skills*`、roles/templates 元数据端点、`add_member` role 校验、`MemberList` 下拉、`SkillPanel` 群内整理台、`TemplateManager` 升级、i18n key（zh/en）与一致性单测。
```
