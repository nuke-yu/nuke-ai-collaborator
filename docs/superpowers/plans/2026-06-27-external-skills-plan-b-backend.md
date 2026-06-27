# External Skills (Plan B — Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the three external-skill abilities at the backend/API layer — 识别 (git import → registry + provenance), 自动加载 (two external pools scanned + merged + per-bot visibility), 分配 (assignment API writing the Plan-A `bot_skills` table) — on top of the Plan A foundation.

**Architecture:** Two new skill pools (global operator pool + per-group pool) become a single `ExternalPoolSource` plugged into the existing four-layer discovery/merge pipeline as two new layers (`external_global`, `external_group`) ordered between `role` and `learned`. A post-cache `available_skills_for_bot()` wrapper applies Plan A's `assignment.filter_visible` so external skills are only visible to bots they're assigned to. A `registry` module owns `external_skills` CRUD and an `importer` module runs the git-clone → sanitize → classify → land-on-disk → write-registry pipeline. Two REST surfaces expose it: import/remove on `api/skills.py`, assignment on `api/groups.py`.

**Tech Stack:** Python 3.13 · FastAPI · aiosqlite/SQLite · `git` CLI via `subprocess` · pytest (tests are `unittest`-style, run under `python3 -m pytest`).

## Global Constraints

- **Python interpreter is `python3`.**
- **Test rhythm (backend/CLAUDE.md):** after each feature point write its unit test and run ONLY that test file. Run the full suite (`python3 -m pytest`) once before the final commit.
- **No AI co-author trailer in commits.** Author is `nuke`; message describes the change only. Never add `Co-Authored-By`.
- **Builds on Plan A (already merged on this branch):** `bot_skills`/`external_skills` tables exist; `skills/assignment.py` provides `set_assignment`/`remove_assignment`/`list_assignments`/`enabled_skill_names`/`filter_visible`/`EXTERNAL_LAYERS = {"external_global","external_group"}`. `external_skills.group_id` uses `0` for global scope.
- **Capability ≠ permission.** Assignment writes `bot_skills` (capability/visibility); call-time HIL stays in `permission_rules`. Never collapse the two.
- **Group isolation is inviolable.** The global pool holds cross-group skill *definitions*; the per-group pool (`group_{gid}/external/skills`) is visible ONLY to that group; assignment is per-bot. A bot in group X must never see group Y's pool.
- **External pools default opt-in:** an external skill is visible to a bot only when `bot_skills(bot_id, name).enabled = 1` (enforced by `filter_visible`, OUTSIDE the mtime scan cache — never cache a per-bot DB fact by file signature).
- **Layer override order (spec §6.2):** `system < group < role < external_global < external_group < learned`. System skills stay protected (A1).
- **Imported skills are untrusted:** inline shell markers are already inert (DFT-022 / `processor.py`); the importer additionally records `high_privilege` hits and rejects path/symlink escapes (reuse `SkillStore.copy`'s checks).
- **DB:** `external_skills` is central. Reads via `db.global_db()`; writes via `db.write_connect(db.DB_PATH)` — mirror `skills/assignment.py`.
- **Defaults (spec §12):** `shell` defaults `bash`; `platforms` defaults `pure`; `version` defaults `""`. Git host allowlist defaults to allowing `github.com` plus private/internal hosts; clone uses `--depth 1` with a wall-clock timeout.

---

### Task 1: layout dirs for the two external pools

**Files:**
- Modify: `backend/workspace/layout.py` (add two pure path functions)
- Test: `backend/tests/test_external_layout.py`

**Interfaces:**
- Produces:
  - `external_global_skills_dir() -> Path` → `<WORKSPACE_ROOT>/external/skills`
  - `group_external_skills_dir(gid: int) -> Path` → `<WORKSPACE_ROOT>/group_{gid}/external/skills`
  Both are pure (no I/O, no mkdir), reading `WORKSPACE_ROOT` live via `_root()`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_external_layout.py`:

```python
"""Plan B Task 1 — external pool layout dirs."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skills.constants as _const
from workspace import layout


class TestExternalLayout(unittest.TestCase):
    def test_global_and_group_external_dirs(self):
        root = _const.WORKSPACE_ROOT
        self.assertEqual(layout.external_global_skills_dir(), root / "external" / "skills")
        self.assertEqual(layout.group_external_skills_dir(7), root / "group_7" / "external" / "skills")

    def test_dirs_are_pure_no_mkdir(self):
        # Calling them must not create anything on disk.
        p = layout.group_external_skills_dir(999999)
        self.assertFalse(p.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_external_layout.py -v`
Expected: FAIL — `AttributeError: module 'workspace.layout' has no attribute 'external_global_skills_dir'`.

- [ ] **Step 3: Add the two functions**

In `backend/workspace/layout.py`, after `group_roles_dir`:

```python
def external_global_skills_dir() -> Path:
    """Global operator-curated external skill pool (cross-group definitions)."""
    return _root() / "external" / "skills"


def group_external_skills_dir(gid: int) -> Path:
    """Per-group external skill pool — visible ONLY to that group (isolation)."""
    return group_dir(gid) / "external" / "skills"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_external_layout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/workspace/layout.py backend/tests/test_external_layout.py
git commit -m "feat(skills): external pool layout dirs (global + per-group)"
```

---

### Task 2: frontmatter parsing for `platforms` + `version`

**Files:**
- Modify: `backend/skills/metadata.py` (`parse_frontmatter` string-field list; `parse_skill_meta` return dict)
- Test: `backend/tests/test_skill_frontmatter.py` (append `TestPlatformsVersionParsing`)

**Interfaces:**
- Consumes: existing `parse_frontmatter` / `parse_skill_meta`.
- Produces: `parse_skill_meta(path)` returns `platforms` (str, default `"pure"`) and `version` (str, default `""`). `shell` is already parsed (default `"bash"`). These feed the importer's registry row and the pool listing.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_skill_frontmatter.py` (before `if __name__`):

```python
class TestPlatformsVersionParsing(unittest.TestCase):
    def _meta(self, body):
        import tempfile
        from pathlib import Path
        from skills.metadata import parse_skill_meta
        d = tempfile.mkdtemp()
        p = Path(d) / "SKILL.md"
        p.write_text(body, encoding="utf-8")
        return parse_skill_meta(p)

    def test_parses_platforms_and_version(self):
        meta = self._meta(
            "---\nname: x\ndescription: d\nplatforms: posix\nversion: 1.2.3\nshell: powershell\n---\nbody"
        )
        self.assertEqual(meta["platforms"], "posix")
        self.assertEqual(meta["version"], "1.2.3")
        self.assertEqual(meta["shell"], "powershell")

    def test_defaults_when_absent(self):
        meta = self._meta("---\nname: x\ndescription: d\n---\nbody")
        self.assertEqual(meta["platforms"], "pure")
        self.assertEqual(meta["version"], "")
        self.assertEqual(meta["shell"], "bash")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_frontmatter.py::TestPlatformsVersionParsing -v`
Expected: FAIL — `KeyError: 'platforms'` (not in the returned meta dict).

- [ ] **Step 3: Parse and surface the fields**

In `backend/skills/metadata.py`, in `parse_frontmatter`, add `platforms` and `version` to the string-field loop list:

```python
    for str_key in ["name", "description", "when_to_use", "status", "layer", "paths", "context", "shell", "model", "platforms", "version"]:
```

In `parse_skill_meta`'s returned dict (the success branch), add the two keys (next to `"shell"`):

```python
            "shell": fm.get("shell", "bash"),
            "platforms": fm.get("platforms", "pure"),
            "version": fm.get("version", ""),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_frontmatter.py::TestPlatformsVersionParsing -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/metadata.py backend/tests/test_skill_frontmatter.py
git commit -m "feat(skills): parse platforms + version frontmatter fields"
```

---

### Task 3: `ExternalPoolSource` (two-layer skill source)

**Files:**
- Create: `backend/skills/sources/external.py`
- Test: `backend/tests/test_external_source.py`

**Interfaces:**
- Consumes: `ScanCtx` (`sources/base.py`), `scan_dir`/`dir_signature` (`sources/_scan.py`), `layout.external_global_skills_dir`/`group_external_skills_dir` (Task 1).
- Produces: `ExternalPoolSource(ctx)` with:
  - `layer = "external"` (class attr, for the source registry; entries carry their own `external_global`/`external_group` layer via `scan_dir`).
  - `enumerate() -> list[SkillEntry]` — global entries (tagged `external_global`) then group entries (tagged `external_group`, only if `ctx.group_id`). Global-then-group order so the group layer wins on a name clash.
  - `signature() -> tuple` — merged `dir_signature` of both dirs.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_external_source.py`:

```python
"""Plan B Task 3 — ExternalPoolSource enumerates global + group external pools."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skills.constants as _const


def _write_skill(pool_dir: Path, name: str, desc: str):
    sd = pool_dir / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nbody", encoding="utf-8")


class TestExternalSource(unittest.TestCase):
    def setUp(self):
        self._orig_root = _const.WORKSPACE_ROOT
        self._tmp = tempfile.mkdtemp()
        _const.WORKSPACE_ROOT = Path(self._tmp)

    def tearDown(self):
        _const.WORKSPACE_ROOT = self._orig_root

    def test_enumerates_both_layers_with_correct_tags(self):
        from workspace import layout
        from skills.sources.base import ScanCtx
        from skills.sources.external import ExternalPoolSource

        _write_skill(layout.external_global_skills_dir(), "deploy", "global one")
        _write_skill(layout.group_external_skills_dir(3), "lint", "group one")

        entries = ExternalPoolSource(ScanCtx(bot_id=1, group_id=3)).enumerate()
        by_name = {e["name"]: e for e in entries}
        self.assertEqual(by_name["deploy"]["layer"], "external_global")
        self.assertEqual(by_name["lint"]["layer"], "external_group")

    def test_group_pool_skipped_when_no_group_id(self):
        from workspace import layout
        from skills.sources.base import ScanCtx
        from skills.sources.external import ExternalPoolSource

        _write_skill(layout.external_global_skills_dir(), "deploy", "global one")
        _write_skill(layout.group_external_skills_dir(3), "lint", "group one")

        entries = ExternalPoolSource(ScanCtx(bot_id=1, group_id=None)).enumerate()
        names = {e["name"] for e in entries}
        self.assertEqual(names, {"deploy"})   # group pool not scanned without a group

    def test_signature_changes_when_a_skill_added(self):
        from workspace import layout
        from skills.sources.base import ScanCtx
        from skills.sources.external import ExternalPoolSource

        src = ExternalPoolSource(ScanCtx(bot_id=1, group_id=3))
        sig_before = src.signature()
        _write_skill(layout.external_global_skills_dir(), "deploy", "global one")
        self.assertNotEqual(src.signature(), sig_before)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_external_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.sources.external'`.

- [ ] **Step 3: Create `backend/skills/sources/external.py`**

```python
# backend/skills/sources/external.py
from typing import List
from workspace import layout
from .base import ScanCtx, SkillEntry
from ._scan import scan_dir, dir_signature


class ExternalPoolSource:
    """The two external skill pools as one source.

    Global pool (operator-curated, cross-group) tags entries `external_global`;
    the per-group pool (group-private) tags `external_group`. Enumeration order
    is global-then-group so the group layer wins a name clash during merge.
    Visibility per bot is applied LATER (filter_visible), never here.
    """
    layer = "external"

    def __init__(self, ctx: ScanCtx):
        self.ctx = ctx

    def _global_dir(self):
        return layout.external_global_skills_dir()

    def _group_dir(self):
        if not self.ctx.group_id:
            return None
        return layout.group_external_skills_dir(self.ctx.group_id)

    def enumerate(self) -> List[SkillEntry]:
        out = scan_dir(self._global_dir(), "external_global")
        gd = self._group_dir()
        if gd:
            out = out + scan_dir(gd, "external_group")
        return out

    def signature(self) -> tuple:
        sig = list(dir_signature(self._global_dir()))
        gd = self._group_dir()
        if gd:
            sig.extend(dir_signature(gd))
        return tuple(sig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_external_source.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/sources/external.py backend/tests/test_external_source.py
git commit -m "feat(skills): ExternalPoolSource for global + per-group pools"
```

---

### Task 4: merge the external layer into `composer.merge_layers`

**Files:**
- Modify: `backend/skills/composer.py` (`_LAYER_ORDER`; `merge_layers` signature + merge loop)
- Test: `backend/tests/test_external_merge.py`

**Interfaces:**
- Consumes: `_merge_skill_entry`, `SkillEntry`.
- Produces: `merge_layers(system, group, role, learned, *, external=None)` — backward compatible (existing 4-positional callers/tests unaffected). External entries are merged AFTER `role` and BEFORE `learned`, so they override role but are overridden by learned. `_LAYER_ORDER` gains `external_global: 2.3` and `external_group: 2.6` (between `role`=2 and `learned`=3) so the final sort places them correctly.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_external_merge.py`:

```python
"""Plan B Task 4 — external layers merge between role and learned."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.composer import merge_layers


def _entry(name, layer, **kw):
    base = {"name": name, "layer": layer, "description": layer, "is_stub": False,
            "fm_keys": [], "status": "active", "path": f"/p/{layer}/{name}.md"}
    base.update(kw)
    return base


class TestExternalMerge(unittest.TestCase):
    def test_external_overrides_role_but_not_learned(self):
        role = [_entry("dup", "role")]
        external = [_entry("dup", "external_global")]
        learned = {"active": [_entry("dup", "learned")]}
        result = merge_layers([], [], role, learned, external=external)
        dup = next(s for s in result if s["name"] == "dup")
        self.assertEqual(dup["layer"], "learned")   # learned still wins

    def test_external_group_overrides_external_global(self):
        external = [_entry("dup", "external_global"), _entry("dup", "external_group")]
        result = merge_layers([], [], [], {}, external=external)
        dup = next(s for s in result if s["name"] == "dup")
        self.assertEqual(dup["layer"], "external_group")

    def test_external_overrides_role_when_no_learned(self):
        role = [_entry("dup", "role")]
        external = [_entry("dup", "external_global")]
        result = merge_layers([], [], role, {}, external=external)
        dup = next(s for s in result if s["name"] == "dup")
        self.assertEqual(dup["layer"], "external_global")

    def test_backward_compatible_without_external(self):
        result = merge_layers([_entry("a", "system")], [], [], {})
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_external_merge.py -v`
Expected: FAIL — `merge_layers() got an unexpected keyword argument 'external'`.

- [ ] **Step 3: Wire external into the merge**

In `backend/skills/composer.py`, replace the `_LAYER_ORDER` line:

```python
_LAYER_ORDER = {"system": 0, "group": 1, "role": 2,
                "external_global": 2.3, "external_group": 2.6,
                "learned": 3, "personal": 4}
```

Change the `merge_layers` signature and add the external merge step (after the `role` loop, before the `learned.get("active")` loop):

```python
def merge_layers(system: list, group: list, role: list, learned: dict,
                 *, external: list | None = None) -> List[SkillEntry]:
```

Inside, after:

```python
    for s in role:
        _merge_skill_entry(merged, s)
```

insert:

```python
    for s in (external or []):
        _merge_skill_entry(merged, s)
```

(The docstring's precedence line may be updated to mention external; not required for the test.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_external_merge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/composer.py backend/tests/test_external_merge.py
git commit -m "feat(skills): merge external layers between role and learned"
```

---

### Task 5: discovery scans the external pool

**Files:**
- Modify: `backend/skills/discovery.py` (`_sources`, `_scan_signature`, `_compute_skills_all`)
- Test: `backend/tests/test_external_discovery.py`

**Interfaces:**
- Consumes: `ExternalPoolSource` (Task 3), `merge_layers(..., external=...)` (Task 4).
- Produces: `_compute_skills_all`/`list_skills_all` now include external-pool skills (tagged `external_global`/`external_group`); `_scan_signature` covers the external dirs so add/edit/delete invalidates the cache. NO per-bot visibility filtering happens here (that is Task 6).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_external_discovery.py`:

```python
"""Plan B Task 5 — external pool flows through discovery (unfiltered)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skills.constants as _const


def _write_skill(pool_dir: Path, name: str):
    sd = pool_dir / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody", encoding="utf-8")


class TestExternalDiscovery(unittest.TestCase):
    def setUp(self):
        self._orig_root = _const.WORKSPACE_ROOT
        self._orig_sys = _const.SYSTEM_SKILLS_ROOT
        self._tmp = tempfile.mkdtemp()
        _const.WORKSPACE_ROOT = Path(self._tmp)
        _const.SYSTEM_SKILLS_ROOT = Path(self._tmp) / "system" / "skills"
        from skills.discovery import invalidate_skills_cache
        invalidate_skills_cache()

    def tearDown(self):
        _const.WORKSPACE_ROOT = self._orig_root
        _const.SYSTEM_SKILLS_ROOT = self._orig_sys
        from skills.discovery import invalidate_skills_cache
        invalidate_skills_cache()

    def test_external_skill_listed_and_tagged(self):
        from workspace import layout
        from skills.discovery import _compute_skills_all
        _write_skill(layout.external_global_skills_dir(), "deploy")
        skills = _compute_skills_all(bot_id=1, group_id=2, role=None)
        by_name = {s["name"]: s for s in skills}
        self.assertIn("deploy", by_name)
        self.assertEqual(by_name["deploy"]["layer"], "external_global")

    def test_signature_sensitive_to_external_changes(self):
        from workspace import layout
        from skills.discovery import _scan_signature
        sig_before = _scan_signature(bot_id=1, group_id=2, role=None)
        _write_skill(layout.external_global_skills_dir(), "deploy")
        self.assertNotEqual(_scan_signature(bot_id=1, group_id=2, role=None), sig_before)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_external_discovery.py -v`
Expected: FAIL — `deploy` not in the listed skills (external source not wired in).

- [ ] **Step 3: Wire the external source into discovery**

In `backend/skills/discovery.py`, add the import near the other source imports:

```python
from .sources.external import ExternalPoolSource
```

Change `_sources` to return a 5-tuple:

```python
def _sources(bot_id: int, group_id: Optional[int], role: Optional[str]):
    """Instantiate the per-layer SkillSource objects for this scan key."""
    ctx = ScanCtx(bot_id, group_id, role)
    return (
        SystemPoolSource(ctx),
        GroupSource(ctx),
        RoleSource(ctx),
        ExternalPoolSource(ctx),
        LearnedSource(ctx),
    )
```

Change `_scan_signature` to unpack 5:

```python
    sysm, grp, rol, ext, lrn = _sources(bot_id, group_id, role)
    union: list = []
    for src in (sysm, grp, rol, ext, lrn):
        union.extend(src.signature())
    return tuple(sorted(union))
```

Change `_compute_skills_all` to unpack 5 and pass `external=`:

```python
    sysm, grp, rol, ext, lrn = _sources(bot_id, group_id, role)
    return merge_layers(
        sysm.enumerate(),
        grp.enumerate(),
        rol.enumerate(),
        lrn.enumerate(),
        external=ext.enumerate(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_external_discovery.py -v`
Expected: PASS

- [ ] **Step 5: Run the existing skill-discovery regression**

Run: `cd backend && python3 -m pytest tests/test_skills_a1_a3.py tests/test_skill_fixes.py -v`
Expected: PASS (the 5-tuple change and the keyword `external` arg are backward compatible).

- [ ] **Step 6: Commit**

```bash
git add backend/skills/discovery.py backend/tests/test_external_discovery.py
git commit -m "feat(skills): scan external pool in discovery (unfiltered)"
```

---

### Task 6: per-bot visibility — `available_skills_for_bot` mounted at consumers

**Files:**
- Modify: `backend/skills/discovery.py` (add async `available_skills_for_bot`)
- Modify: `backend/skills/__init__.py` (export it)
- Modify: `backend/core/orchestration/prompt_builder.py:89` (use it)
- Modify: `backend/skills/loader.py:53,98` (use it in `load_always_skills` + `run_skill`)
- Test: `backend/tests/test_external_visibility.py`

**Interfaces:**
- Consumes: `list_skills_all` (Task 5 output), `assignment.filter_visible` (Plan A).
- Produces: `async available_skills_for_bot(bot_id, group_id=None, role=None) -> list[dict]` — the single visibility-filtered entry point shared by prompt-build and run_skill: returns `list_skills_all(...)` with external-layer entries not enabled for this bot removed. Non-external layers pass through untouched. This is the enforcement seam: an unassigned external skill is invisible AND un-runnable.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_external_visibility.py`:

```python
"""Plan B Task 6 — external skills filtered per-bot via bot_skills."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db


def _run(coro):
    return asyncio.run(coro)


class TestAvailableSkillsForBot(unittest.TestCase):
    def test_unassigned_external_hidden_assigned_visible(self):
        from skills import discovery
        from skills import assignment
        from db.schema_split import init_central_db

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        all_skills = [
            {"name": "write-spec", "layer": "system"},
            {"name": "deploy", "layer": "external_global"},
            {"name": "secret", "layer": "external_global"},
        ]

        async def fake_list(bot_id, group_id=None, role=None):
            return list(all_skills)

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type) VALUES (1,1,'dev','bot')")
                await conn.commit()
            await assignment.set_assignment(1, "deploy", "external_global", enabled=True)
            with patch.object(discovery, "list_skills_all", new=fake_list):
                return await discovery.available_skills_for_bot(1, group_id=1)

        try:
            _db.DB_PATH = path
            visible = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        names = {s["name"] for s in visible}
        self.assertEqual(names, {"write-spec", "deploy"})  # 'secret' filtered out


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_external_visibility.py -v`
Expected: FAIL — `AttributeError: module 'skills.discovery' has no attribute 'available_skills_for_bot'`.

- [ ] **Step 3: Add `available_skills_for_bot` and mount it**

In `backend/skills/discovery.py`, add at the end:

```python
async def available_skills_for_bot(bot_id: int, group_id: Optional[int] = None,
                                   role: Optional[str] = None) -> List[Dict]:
    """list_skills_all + per-bot external visibility (Plan B §6.3).

    The single visibility-filtered entry point shared by prompt-build and
    run_skill. External-layer skills not enabled in bot_skills for this bot are
    dropped; all other layers pass through. Runs OUTSIDE the mtime scan cache
    because visibility is a per-bot DB fact, not a file-signature fact.
    """
    from .assignment import filter_visible
    skills = await list_skills_all(bot_id, group_id=group_id, role=role)
    return await filter_visible(bot_id, skills)
```

In `backend/skills/__init__.py`, add to the discovery import line and `__all__`:

```python
from .discovery import list_skills, list_skills_all, available_skills_for_bot
```
and add `"available_skills_for_bot",` to `__all__`.

In `backend/core/orchestration/prompt_builder.py`, change the import on line 3 to include it and replace line 89's call:

```python
from skills import list_skills_all, load_always_skills, filter_skills_by_context, available_skills_for_bot
```
```python
    raw_skills = await available_skills_for_bot(bot["id"], group_id=ctx.group_id, role=bot.get("role"))
```

In `backend/skills/loader.py`, replace the two `list_skills_all(...)` calls with the filtered entry point. Change the import line:

```python
from .discovery import list_skills, list_skills_all, available_skills_for_bot
```

In `load_always_skills` (line ~53):

```python
    skills = await available_skills_for_bot(bot_id, group_id=group_id, role=role)
```

In `run_skill` (line ~98):

```python
    available_skills = await available_skills_for_bot(bot_id, group_id=group_id, role=role)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_external_visibility.py -v`
Expected: PASS

- [ ] **Step 5: Run loader + prompt regression**

Run: `cd backend && python3 -m pytest tests/test_skills_a1_a3.py tests/test_skill_loader_planA.py tests/test_skill_fixes.py -v`
Expected: PASS. (The loader tests patch `loader.list_skills_all`; since `run_skill` now calls `available_skills_for_bot` — which calls the module-level `list_skills_all` — those patches still apply because `available_skills_for_bot` resolves `list_skills_all` from the discovery module at call time, and the loader Plan-A tests patch `loader.list_skills_all` specifically. Verify; if a loader Plan-A test fails because it patches the wrong symbol, update that test to patch `skills.discovery.list_skills_all` instead.)

- [ ] **Step 6: Commit**

```bash
git add backend/skills/discovery.py backend/skills/__init__.py backend/core/orchestration/prompt_builder.py backend/skills/loader.py backend/tests/test_external_visibility.py
git commit -m "feat(skills): per-bot external visibility via available_skills_for_bot"
```

---

### Task 7: `registry.py` — `external_skills` CRUD

**Files:**
- Create: `backend/skills/registry.py`
- Test: `backend/tests/test_external_registry.py`

**Interfaces:**
- Consumes: `db.global_db()`, `db.write_connect(db.DB_PATH)`, central `external_skills` table (Plan A).
- Produces:
  - `async register(name, scope_kind, group_id, source_url, ref, commit_sha, version, platforms, high_privilege, imported_by) -> int` — INSERT one row, returns its id. Raises `ValueError("duplicate")` on the `UNIQUE(scope_kind, group_id, name)` clash.
  - `async list_external(scope_kind=None, group_id=None) -> list[dict]` — rows as dicts (all columns), newest first.
  - `async get_external(id) -> dict | None`
  - `async remove_external(id) -> dict | None` — delete by id, returns the deleted row (so the caller can also delete files), or None if absent.
  - constant `GLOBAL_GROUP_ID = 0` (the sentinel for global scope).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_external_registry.py`:

```python
"""Plan B Task 7 — external_skills registry CRUD."""
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


class TestRegistry(unittest.TestCase):
    def test_register_list_get_remove_and_dup(self):
        from skills import registry
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            rid = await registry.register(
                "deploy", "global", registry.GLOBAL_GROUP_ID,
                "https://github.com/x/y", "main", "abc123", "1.0.0",
                "posix", "run_shell", 42,
            )
            rows = await registry.list_external()
            got = await registry.get_external(rid)
            # Duplicate same (scope_kind, group_id, name) → ValueError
            dup_raised = False
            try:
                await registry.register("deploy", "global", registry.GLOBAL_GROUP_ID,
                                        "u", "r", "c", "v", "pure", "", 1)
            except ValueError:
                dup_raised = True
            removed = await registry.remove_external(rid)
            after = await registry.list_external()
            return rid, rows, got, dup_raised, removed, after

        try:
            _db.DB_PATH = path
            rid, rows, got, dup_raised, removed, after = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(got["name"], "deploy")
        self.assertEqual(got["commit_sha"], "abc123")
        self.assertEqual(got["platforms"], "posix")
        self.assertTrue(dup_raised)
        self.assertEqual(removed["id"], rid)
        self.assertEqual(after, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_external_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.registry'`.

- [ ] **Step 3: Create `backend/skills/registry.py`**

```python
"""external_skills registry CRUD (Plan B) — provenance + lifecycle truth source.

The file content is the skill truth source (scanner reads disk); this registry
row records WHERE it came from (source/ref/commit), its declared version /
platforms, the high-privilege tools it touches, and who imported it. import
writes file + row together; remove deletes both.

Central-DB table. Reads via db.global_db(); writes via db.write_connect — mirrors
skills/assignment.py.
"""
import sqlite3
import db as _db

# Sentinel group_id for global-scope rows (matches external_skills DEFAULT 0).
GLOBAL_GROUP_ID = 0

_COLS = ["id", "name", "scope_kind", "group_id", "source_url", "ref",
         "commit_sha", "version", "platforms", "high_privilege",
         "imported_by", "imported_at", "status"]


def _row_to_dict(row) -> dict:
    return {c: row[i] for i, c in enumerate(_COLS)}


async def register(name: str, scope_kind: str, group_id: int, source_url: str,
                   ref: str, commit_sha: str, version: str, platforms: str,
                   high_privilege: str, imported_by: int | None) -> int:
    async with _db.write_connect(_db.DB_PATH) as db:
        try:
            cur = await db.execute(
                """INSERT INTO external_skills
                   (name, scope_kind, group_id, source_url, ref, commit_sha,
                    version, platforms, high_privilege, imported_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (name, scope_kind, group_id, source_url, ref, commit_sha,
                 version, platforms, high_privilege, imported_by),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError("duplicate") from e
        await db.commit()
        return cur.lastrowid


async def list_external(scope_kind: str | None = None,
                        group_id: int | None = None) -> list[dict]:
    sql = f"SELECT {', '.join(_COLS)} FROM external_skills"
    where, params = [], []
    if scope_kind is not None:
        where.append("scope_kind=?")
        params.append(scope_kind)
    if group_id is not None:
        where.append("group_id=?")
        params.append(group_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    async with _db.global_db() as db:
        async with db.execute(sql, tuple(params)) as cur:
            return [_row_to_dict(r) for r in await cur.fetchall()]


async def get_external(id: int) -> dict | None:
    async with _db.global_db() as db:
        async with db.execute(
            f"SELECT {', '.join(_COLS)} FROM external_skills WHERE id=?", (id,)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def remove_external(id: int) -> dict | None:
    existing = await get_external(id)
    if existing is None:
        return None
    async with _db.write_connect(_db.DB_PATH) as db:
        await db.execute("DELETE FROM external_skills WHERE id=?", (id,))
        await db.commit()
    return existing
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_external_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/registry.py backend/tests/test_external_registry.py
git commit -m "feat(skills): external_skills registry CRUD module"
```

---

### Task 8: `importer.py` — git import pipeline (sanitize + classify, no network in tests)

**Files:**
- Create: `backend/skills/importer.py`
- Test: `backend/tests/test_external_importer.py`

**Interfaces:**
- Consumes: `parse_skill_meta` (metadata), `constants.HIGH_PRIVILEGE_TOOLS`, `registry.register` (Task 7), `layout.external_global_skills_dir`/`group_external_skills_dir` (Task 1).
- Produces:
  - `classify_platforms(meta: dict) -> str` — returns the skill's `platforms` value, defaulting `"pure"`.
  - `scan_high_privilege(skill_dir: Path) -> str` — comma-joined `HIGH_PRIVILEGE_TOOLS` whose name appears in any `SKILL.md`/`*.md` body under the dir; `""` if none.
  - `_safe_dest(name: str) -> None` — raises `ValueError` if `name` fails `_is_safe_name` (blocks `..`, separators, drive letters).
  - `async import_from_dir(repo_dir, scope_kind, group_id, source_url, ref, commit_sha, imported_by) -> dict` — the post-clone pipeline: find every `<name>/SKILL.md`, validate name/description, copy each valid skill dir into the right pool (reusing `SkillStore.copy`'s symlink-escape protection), register a row, and return `{"imported": [...], "rejected": [...]}`. (The network clone is a thin separate wrapper, Task 9, so this is unit-testable from a local dir.)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_external_importer.py`:

```python
"""Plan B Task 8 — importer pipeline from a local repo dir (no network)."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db
import skills.constants as _const
from db.schema_split import init_central_db


def _run(coro):
    return asyncio.run(coro)


def _mk_skill(repo: Path, name: str, body_extra: str = ""):
    sd = repo / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\nplatforms: posix\nversion: 2.0\n---\n{body_extra}",
        encoding="utf-8",
    )
    return sd


class TestImporter(unittest.TestCase):
    def setUp(self):
        self._orig_root = _const.WORKSPACE_ROOT
        self._tmp = tempfile.mkdtemp()
        _const.WORKSPACE_ROOT = Path(self._tmp)

    def tearDown(self):
        _const.WORKSPACE_ROOT = self._orig_root

    def test_classify_and_high_privilege_scan(self):
        from skills import importer
        repo = Path(tempfile.mkdtemp())
        sd = _mk_skill(repo, "deploy", body_extra="please run_shell the script")
        self.assertEqual(importer.scan_high_privilege(sd), "run_shell")
        from skills.metadata import parse_skill_meta
        self.assertEqual(importer.classify_platforms(parse_skill_meta(sd / "SKILL.md")), "posix")

    def test_import_lands_files_and_registers(self):
        from skills import importer, registry
        from workspace import layout
        repo = Path(tempfile.mkdtemp())
        _mk_skill(repo, "deploy")
        _mk_skill(repo, "Bad Name")  # space → unsafe → rejected

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            result = await importer.import_from_dir(
                repo, "global", registry.GLOBAL_GROUP_ID,
                "https://github.com/x/y", "main", "abc123", imported_by=1,
            )
            rows = await registry.list_external()
            return result, rows

        try:
            _db.DB_PATH = path
            result, rows = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        imported_names = {i["name"] for i in result["imported"]}
        self.assertEqual(imported_names, {"deploy"})
        self.assertTrue(any("Bad Name" in r["path"] for r in result["rejected"]))
        # File landed in the global pool
        self.assertTrue((layout.external_global_skills_dir() / "deploy" / "SKILL.md").exists())
        # Registry row written with provenance
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["commit_sha"], "abc123")
        self.assertEqual(rows[0]["platforms"], "posix")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_external_importer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.importer'`.

- [ ] **Step 3: Create `backend/skills/importer.py`**

```python
"""External skill import pipeline (Plan B §4).

Given a LOCAL repo directory (the git clone is a thin wrapper in api/skills.py),
find every `<name>/SKILL.md`, validate it, copy valid skills into the target
pool with symlink-escape protection (reusing SkillStore.copy), and write an
external_skills registry row per imported skill. Imported skills are untrusted:
inline shell is already inert (DFT-022); we additionally record high-privilege
tool hits for the operator UI.
"""
from pathlib import Path

from . import constants as C
from . import registry
from .metadata import parse_skill_meta, _is_safe_name
from .store import SkillStore
from .scope import GroupScope  # not used directly; pool dir resolved below
from workspace import layout


def classify_platforms(meta: dict) -> str:
    return meta.get("platforms") or "pure"


def scan_high_privilege(skill_dir: Path) -> str:
    """Comma-joined HIGH_PRIVILEGE_TOOLS mentioned anywhere in the skill's text."""
    hits: list[str] = []
    texts = []
    for p in skill_dir.rglob("*.md"):
        try:
            texts.append(p.read_text(encoding="utf-8").lower())
        except Exception:
            continue
    blob = "\n".join(texts)
    for tool in C.HIGH_PRIVILEGE_TOOLS:
        if tool in blob:
            hits.append(tool)
    return ",".join(hits)


def _safe_dest(name: str) -> None:
    if not _is_safe_name(name):
        raise ValueError(f"unsafe skill name: {name!r}")


def _pool_dir(scope_kind: str, group_id: int) -> Path:
    if scope_kind == "global":
        return layout.external_global_skills_dir()
    return layout.group_external_skills_dir(group_id)


async def import_from_dir(repo_dir, scope_kind: str, group_id: int,
                          source_url: str, ref: str, commit_sha: str,
                          imported_by: int | None) -> dict:
    repo_dir = Path(repo_dir)
    pool = _pool_dir(scope_kind, group_id)
    store = SkillStore()

    # A tiny scope shim so SkillStore.copy lands the dir under `pool`.
    class _PoolScope:
        def dir(self_inner):
            return pool

    imported, rejected = [], []
    # Every directory holding a SKILL.md is one skill.
    for skill_md in sorted(repo_dir.rglob("SKILL.md")):
        skill_dir = skill_md.parent
        name = skill_dir.name
        try:
            _safe_dest(name)
        except ValueError as e:
            rejected.append({"path": str(skill_dir), "reason": str(e)})
            continue

        meta = parse_skill_meta(skill_md)
        if not meta.get("description") or not meta.get("name"):
            rejected.append({"path": str(skill_dir), "reason": "missing name/description"})
            continue

        platforms = classify_platforms(meta)
        high_priv = scan_high_privilege(skill_dir)
        version = meta.get("version", "")

        # Copy into the pool (symlink-escape protection lives in SkillStore.copy).
        src_scope = _PoolScope.__new__(_PoolScope)
        src_scope.dir = lambda d=skill_dir.parent: d
        try:
            store.copy(src_scope, name, _PoolScope())
        except (ValueError, FileNotFoundError) as e:
            rejected.append({"path": str(skill_dir), "reason": f"copy failed: {e}"})
            continue

        try:
            rid = await registry.register(
                name, scope_kind, group_id, source_url, ref, commit_sha,
                version, platforms, high_priv, imported_by,
            )
        except ValueError:
            rejected.append({"path": str(skill_dir), "reason": "duplicate name in scope"})
            continue

        imported.append({"id": rid, "name": name, "version": version,
                         "platforms": platforms, "high_privilege": high_priv})

    return {"imported": imported, "rejected": rejected}
```

> Implementation note for the engineer: `SkillStore.copy(src, name, dst)` copies `src.dir()/<name>` (a directory skill) into `dst.dir()/<name>`, running the symlink-escape check against `src.dir()`. The `src_scope.dir` returns the skill's PARENT dir so `src.dir()/name` resolves to the skill folder; `dst` is the pool. If `SkillStore.copy`'s signature differs from this, adapt the two `.dir()` shims accordingly (the contract you need: copy `skill_dir` → `pool/name`, rejecting escaping symlinks).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_external_importer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/skills/importer.py backend/tests/test_external_importer.py
git commit -m "feat(skills): external import pipeline (sanitize + classify + register)"
```

---

### Task 9: import / remove REST endpoints

**Files:**
- Modify: `backend/skills/importer.py` (add `clone_and_import` git wrapper with guards)
- Modify: `backend/api/skills.py` (add `POST /api/skills/import`, `DELETE /api/skills/external/{id}`, `GET /api/skills/external`)
- Test: `backend/tests/test_external_import_api.py`

**Interfaces:**
- Consumes: `import_from_dir` (Task 8), `registry.list_external`/`remove_external` (Task 7), `layout` pool dirs.
- Produces:
  - `async clone_and_import(git_url, ref, scope_kind, group_id, imported_by, *, _clone=None) -> dict` — host-allowlist check → `git clone --depth 1 --branch <ref>` into a tempdir (overridable via `_clone` for tests) → resolve `commit_sha` → `import_from_dir`. Raises `ValueError` for a disallowed host.
  - `POST /api/skills/import` body `{git_url, ref?, scope}` where `scope` is `"global"` or `{"group_id": N}` → `{imported, rejected}`.
  - `DELETE /api/skills/external/{id}` → removes the registry row AND the pool files; `{ok: true}` or 404.
  - `GET /api/skills/external?scope_kind=&group_id=` → `{external: [...]}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_external_import_api.py`:

```python
"""Plan B Task 9 — import/remove via importer wrapper + host allowlist."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db
import skills.constants as _const
from db.schema_split import init_central_db


def _run(coro):
    return asyncio.run(coro)


def _mk_skill(repo: Path, name: str):
    sd = repo / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody", encoding="utf-8")


class TestCloneAndImport(unittest.TestCase):
    def setUp(self):
        self._orig_root = _const.WORKSPACE_ROOT
        self._tmp = tempfile.mkdtemp()
        _const.WORKSPACE_ROOT = Path(self._tmp)

    def tearDown(self):
        _const.WORKSPACE_ROOT = self._orig_root

    def test_disallowed_host_rejected(self):
        from skills import importer
        with self.assertRaises(ValueError):
            _run(importer.clone_and_import(
                "https://evil.example.com/x/y", "main", "global", 0, 1,
                _clone=lambda url, ref, dst: None,
            ))

    def test_allowed_host_imports_via_injected_clone(self):
        from skills import importer, registry
        repo = Path(tempfile.mkdtemp())
        _mk_skill(repo, "deploy")

        def fake_clone(url, ref, dst):
            # Simulate `git clone` by copying our fixture into dst.
            import shutil
            shutil.copytree(repo, dst, dirs_exist_ok=True)
            return "deadbeef"

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            result = await importer.clone_and_import(
                "https://github.com/x/y", "main", "global", 0, 1, _clone=fake_clone,
            )
            rows = await registry.list_external()
            return result, rows

        try:
            _db.DB_PATH = path
            result, rows = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        self.assertEqual({i["name"] for i in result["imported"]}, {"deploy"})
        self.assertEqual(rows[0]["commit_sha"], "deadbeef")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_external_import_api.py -v`
Expected: FAIL — `AttributeError: module 'skills.importer' has no attribute 'clone_and_import'`.

- [ ] **Step 3: Add `clone_and_import` to `backend/skills/importer.py`**

Add near the top:

```python
import os
import re
import shutil
import subprocess
import tempfile

# Host allowlist for git import. Default allows github.com plus private/internal
# hosts; tighten in production via NUKE_SKILL_IMPORT_HOSTS (comma-separated).
_DEFAULT_ALLOWED_HOSTS = {"github.com"}
_CLONE_TIMEOUT_SECONDS = 120


def _allowed_hosts() -> set[str]:
    env = os.environ.get("NUKE_SKILL_IMPORT_HOSTS", "")
    extra = {h.strip().lower() for h in env.split(",") if h.strip()}
    return _DEFAULT_ALLOWED_HOSTS | extra


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "").lower()


def _is_private_host(host: str) -> bool:
    # Internal hosts (no dot, .local, or RFC1918-looking) are allowed by default.
    if not host or "." not in host or host.endswith(".local"):
        return True
    return bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)", host))


def _git_clone(url: str, ref: str, dst: str) -> str:
    """Clone shallow and return the commit sha. Raises on failure/timeout."""
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, dst]
    subprocess.run(cmd, check=True, capture_output=True, timeout=_CLONE_TIMEOUT_SECONDS)
    sha = subprocess.run(["git", "-C", dst, "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True,
                         timeout=30).stdout.strip()
    return sha
```

Add the wrapper (after `import_from_dir`):

```python
async def clone_and_import(git_url: str, ref: str, scope_kind: str, group_id: int,
                           imported_by: int | None, *, _clone=None) -> dict:
    """Host-checked git clone → import_from_dir. `_clone(url, ref, dst)->sha` is
    injectable for tests (no network)."""
    host = _host_of(git_url)
    if not (host in _allowed_hosts() or _is_private_host(host)):
        raise ValueError(f"host not allowed for skill import: {host!r}")

    clone = _clone or _git_clone
    tmp = tempfile.mkdtemp(prefix="nuke_skill_import_")
    try:
        commit_sha = clone(git_url, ref or "", tmp) or ""
        return await import_from_dir(
            tmp, scope_kind, group_id, git_url, ref or "", commit_sha, imported_by,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 4: Add the endpoints to `backend/api/skills.py`**

Add imports at the top:

```python
from skills import importer, registry
from workspace import layout
import shutil
```

Add request models and routes (after the existing `CopySkillRequest`):

```python
class ImportSkillRequest(BaseModel):
    git_url: str
    ref: str = ""
    scope: object   # "global" or {"group_id": int}


def _scope_kind_group(scope) -> tuple[str, int]:
    if scope == "global":
        return "global", registry.GLOBAL_GROUP_ID
    if isinstance(scope, dict) and "group_id" in scope:
        return "group", int(scope["group_id"])
    raise HTTPException(400, "scope must be 'global' or {group_id}")


@router.post("/api/skills/import")
async def import_external_skill(req: ImportSkillRequest):
    scope_kind, group_id = _scope_kind_group(req.scope)
    try:
        return await importer.clone_and_import(
            req.git_url, req.ref, scope_kind, group_id, imported_by=None
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"import failed: {e}")


@router.get("/api/skills/external")
async def list_external_skills(scope_kind: str | None = None, group_id: int | None = None):
    return {"external": await registry.list_external(scope_kind, group_id)}


@router.delete("/api/skills/external/{external_id}")
async def remove_external_skill(external_id: int):
    row = await registry.remove_external(external_id)
    if row is None:
        raise HTTPException(404, f"external skill not found: {external_id}")
    # Delete the pool files too (registry + disk stay consistent).
    if row["scope_kind"] == "global":
        pool = layout.external_global_skills_dir()
    else:
        pool = layout.group_external_skills_dir(row["group_id"])
    target = pool / row["name"]
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    return {"ok": True}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_external_import_api.py -v`
Expected: PASS

- [ ] **Step 6: Run the API import-surface smoke**

Run: `cd backend && python3 -c "import api.skills"`
Expected: no import error (routes register cleanly).

- [ ] **Step 7: Commit**

```bash
git add backend/skills/importer.py backend/api/skills.py backend/tests/test_external_import_api.py
git commit -m "feat(skills): import/remove external skill REST endpoints + host allowlist"
```

---

### Task 10: assignment REST endpoints (write `bot_skills`)

**Files:**
- Modify: `backend/api/groups.py` (add `GET`/`PUT .../members/{bot_id}/skills`)
- Test: `backend/tests/test_skill_assignment_api.py`

**Interfaces:**
- Consumes: `assignment.list_assignments`/`set_assignment`/`remove_assignment` (Plan A), `discovery.available_skills_for_bot` is NOT used here (the panel shows the FULL pool, not the filtered view); use `registry.list_external` for the pool listing.
- Produces:
  - `GET /api/groups/{gid}/members/{bot_id}/skills` → `{pool: [...], assigned: [...]}` — `pool` = global registry rows + this group's registry rows (name/description-less rows are fine; include `name, version, platforms, high_privilege, scope_kind`); `assigned` = `list_assignments(bot_id)`.
  - `PUT /api/groups/{gid}/members/{bot_id}/skills` body `{assigned: [{name, pool, enabled}]}` → reconciles `bot_skills`: upsert each listed assignment, remove any previously-assigned name not in the new list; returns the new `assigned` list.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_skill_assignment_api.py`:

```python
"""Plan B Task 10 — assignment endpoints reconcile bot_skills."""
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


class TestAssignmentReconcile(unittest.TestCase):
    def test_put_upserts_and_removes(self):
        # Drive the reconcile helper directly (route body extracted to a testable fn).
        from api.groups import _reconcile_bot_skills
        from skills import assignment

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        orig = _db.DB_PATH

        async def go():
            await init_central_db(path)
            async with _db.write_connect(path) as conn:
                await conn.execute("INSERT INTO groups (id, name) VALUES (1,'g')")
                await conn.execute("INSERT INTO members (id, group_id, name, type) VALUES (5,1,'dev','bot')")
                await conn.commit()
            await assignment.set_assignment(5, "old", "external_global", enabled=True)
            # New desired state: keep 'deploy', drop 'old'
            await _reconcile_bot_skills(5, [
                {"name": "deploy", "pool": "external_global", "enabled": True},
            ])
            return await assignment.list_assignments(5)

        try:
            _db.DB_PATH = path
            rows = _run(go())
        finally:
            _db.DB_PATH = orig
            os.unlink(path)

        names = {r["skill_name"] for r in rows}
        self.assertEqual(names, {"deploy"})  # 'old' removed, 'deploy' added


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_skill_assignment_api.py -v`
Expected: FAIL — `ImportError: cannot import name '_reconcile_bot_skills'`.

- [ ] **Step 3: Add the reconcile helper + routes to `backend/api/groups.py`**

Add the import near the top:

```python
from skills import assignment, registry
```

Add the helper and routes (anywhere after the router definition):

```python
async def _reconcile_bot_skills(bot_id: int, desired: list[dict]) -> list[dict]:
    """Make bot_skills match `desired` exactly: upsert listed, remove the rest."""
    desired_names = set()
    for d in desired:
        name = d["name"]
        desired_names.add(name)
        await assignment.set_assignment(
            bot_id, name, d.get("pool", "external_global"),
            enabled=bool(d.get("enabled", True)),
        )
    for row in await assignment.list_assignments(bot_id):
        if row["skill_name"] not in desired_names:
            await assignment.remove_assignment(bot_id, row["skill_name"])
    return await assignment.list_assignments(bot_id)


@router.get("/api/groups/{gid}/members/{bot_id}/skills")
async def get_member_skills(gid: int, bot_id: int):
    pool = await registry.list_external("global")
    pool += await registry.list_external("group", gid)
    return {"pool": pool, "assigned": await assignment.list_assignments(bot_id)}


@router.put("/api/groups/{gid}/members/{bot_id}/skills")
async def put_member_skills(gid: int, bot_id: int, body: dict):
    desired = body.get("assigned", [])
    assigned = await _reconcile_bot_skills(bot_id, desired)
    return {"assigned": assigned}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_skill_assignment_api.py -v`
Expected: PASS

- [ ] **Step 5: Verify the API module imports cleanly**

Run: `cd backend && python3 -c "import api.groups"`
Expected: no import error.

- [ ] **Step 6: Commit**

```bash
git add backend/api/groups.py backend/tests/test_skill_assignment_api.py
git commit -m "feat(skills): bot skill assignment REST endpoints"
```

---

### Task 11: Full-suite regression + final verification

**Files:** none (verification only).

- [ ] **Step 1: Run the external-skill + skill + permissions families**

Run: `cd backend && python3 -m pytest tests/test_external_layout.py tests/test_external_source.py tests/test_external_merge.py tests/test_external_discovery.py tests/test_external_visibility.py tests/test_external_registry.py tests/test_external_importer.py tests/test_external_import_api.py tests/test_skill_assignment_api.py tests/test_bot_skills.py tests/test_skills_a1_a3.py tests/test_skill_fixes.py tests/test_skill_loader_planA.py tests/test_skill_frontmatter.py -v`
Expected: PASS

- [ ] **Step 2: Run the full suite (pre-commit regression gate)**

Run: `cd backend && python3 -m pytest`
Expected: PASS (no new failures). Investigate and fix any regression before proceeding — most likely suspects are tests that call `merge_layers` positionally (still supported) or patch `loader.list_skills_all` (Task 6 note).

- [ ] **Step 3: Confirm clean tree**

Run: `git status`
Expected: all Plan B changes committed across Tasks 1–10.

---

## Self-Review

**Spec coverage (§ references):**
- §3.1 two external pools (layout dirs) → Task 1.
- §3.4 import registry table → already in Plan A; CRUD → Task 7.
- §4 import pipeline (clone/sanitize/classify/land/register) → Tasks 8 (pipeline) + 9 (clone wrapper + guards + endpoints).
- §5 cross-platform frontmatter (`platforms`/`version`; `shell` already parsed) → Task 2. (`${SKILL_DIR}` normalization + companion cap shipped in Plan A.)
- §6.1 `ExternalPoolSource` → Task 3; §6.2 discovery + merge wiring → Tasks 4, 5; §6.3 per-bot visibility filter → Task 6.
- §7.1 assignment API → Task 10; pool listing via registry → Tasks 7 + 10.
- §8 affected-file rows tagged "B": `layout.py` (T1), `sources/external.py` (T3), `discovery.py` (T5), `composer.py` (T4), `importer.py` (T8/T9), `registry.py` (T7), `metadata.py` (T2), `api/skills.py` (T9), `api/groups.py` (T10) — all covered.

**Out of scope (deferred):**
- **Frontend UI** (spec §7.2 — Bot-config skill panel: assignment toggles, approval-policy dropdown, import/manage UI). It is a distinct, visually-oriented React subsystem; per the writing-plans scope check it gets its own follow-up plan (`2026-06-27-external-skills-plan-c-frontend.md` or similar) and likely the frontend-design skill. Every ability it needs is exposed by the Task 9–10 endpoints.
- **Plan C migration** (`migrate_skill_assignment.py` + release note + 灰度) — separate plan per spec §11.
- Registry `update`/`pin`/`audit` (spec §3.4 / §12 — data model reserved, v1 implements import/remove only).

**Placeholder scan:** every code step has complete code; every test step has runnable assertions; every run step has an exact command + expected result. The two `_PoolScope.dir` shims in Task 8 carry an explicit engineer note describing the exact `SkillStore.copy` contract to satisfy, with adaptation guidance — not a placeholder.

**Type/name consistency:** `external_global_skills_dir`/`group_external_skills_dir` (T1) used identically in T3/T8/T9. `ExternalPoolSource` (T3) consumed in T5. `merge_layers(..., external=...)` (T4) called in T5. `available_skills_for_bot(bot_id, group_id, role)` (T6) mounted in prompt_builder + loader. `registry.register/list_external/get_external/remove_external/GLOBAL_GROUP_ID` (T7) consumed in T8/T9/T10. `import_from_dir`/`clone_and_import` (T8/T9) signatures match their callers and tests. `_reconcile_bot_skills` (T10) matches its test. `bot_skills` columns and `assignment.*` come verbatim from Plan A.

**Known cross-cutting note:** Task 6 changes the skill menu seen by every bot to the visibility-filtered view. Because non-external layers always pass `filter_visible` untouched and the common case (no external skills assigned) costs zero DB queries (Plan A's lazy `filter_visible`), existing single-bot behavior is unchanged; the full-suite gate in Task 11 confirms no regression.
