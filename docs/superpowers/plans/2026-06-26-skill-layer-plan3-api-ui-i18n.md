# Skill Layer — Plan 3: API / UI / i18n Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the four-layer skill store + file-based role catalog (built in Plans 1–2) through a scope-descriptor HTTP API, validate role binding at member creation, and surface it all in the React UI with full zh/en i18n.

**Architecture:** Plans 1–2 built the backend foundation: `SkillScope`/`SkillStore` primitives, a layered loader, file-based role templates under `templates/<lang>/roles/`, per-group role copies under `group_<id>/roles/`, and `role.yaml` metadata. Plan 3 is the consumer layer. A new `api/skills.py` router exposes scope CRUD + copy and two role-catalog endpoints, all built on `parse_descriptor`/`SkillStore`/`read_role_meta` so path-safety lives in one place (`scope._safe_segment`). `api/groups.py:add_member` gains role-catalog validation (422, group-language message) and a `system_prompt` snapshot from the bound role's `role.yaml`. The frontend gains api-client wrappers, a role dropdown in `MemberList`, scope browsing/copy in `SkillPanel`, and a file-based role view in `TemplateManager`. New UI strings flow through the existing `react-i18next` + `K` key-registry, gated by a parity test.

**Tech Stack:** Python · FastAPI · httpx (test client) · React 19 · Vite · vitest · @testing-library/react · react-i18next

## Global Constraints

- Commit author is `nuke` only — **never** add `Co-Authored-By` / any AI-signature trailer; commit messages describe the change only.
- Every user-facing string is bilingual. zh is the fallback default (`fallbackLng: 'zh'`). Frontend strings go through `t(K.<path>)`; never hardcode display text in JSX.
- Group isolation is inviolable: a group's role catalog is `group_<id>/roles/` only; never read another group's roles. The scope path-safety boundary is `skills/scope.py:_safe_segment` — all scope strings reach the store via `parse_descriptor`, never by hand-built paths.
- Auth is token-only by design (DFT-082): new routers mount under `Depends(auth.get_current_user)` like every other router. Do **not** add per-user `members.user_id` membership checks.
- Path resolution obeys the LIVE-READ INVARIANT: resolve through `workspace.layout` / `skills.constants` at call time (`from workspace import layout`), never import-bind a path constant, so test monkeypatching of `WORKSPACE_ROOT` propagates.
- Backend test command: `python3 -m pytest tests/<file> -v` (python is `python3`). Run only the test file(s) covering the change; full suite (`python3 -m pytest`) only at the pre-merge gate.
- Frontend test command: `npx vitest run src/<file>` from `frontend/`.
- New backend deps: none. New frontend deps: none.

---

## File Structure

**Backend (new):**
- `backend/skills/role_catalog.py` — pure role-catalog enumeration (`list_role_catalog`) over a roles root, joining `role.yaml` metadata + skill count.
- `backend/api/skills.py` — scope-descriptor skill CRUD + copy, plus the two role-catalog endpoints.
- `backend/tests/test_role_catalog.py` — unit tests for `list_role_catalog`.
- `backend/tests/test_skills_api.py` — route tests for `api/skills.py`.
- `backend/tests/test_member_role_binding.py` — route tests for add_member validation + system_prompt snapshot.

**Backend (modified):**
- `backend/api/groups.py:add_member` — role-catalog validation (422) + system_prompt snapshot.
- `backend/main.py` — register the new `skills` router.

**Frontend (new):**
- `frontend/src/skillsApi.js` — api-client wrappers for the new endpoints.
- `frontend/src/skillsApi.test.js` — vitest wiring tests for the wrappers.
- `frontend/src/i18n/i18n.test.js` — key-parity test (`K` ↔ `zh.json` ↔ `en.json`).

**Frontend (modified):**
- `frontend/src/components/MemberList.jsx` — role free-text input → catalog dropdown + autofill.
- `frontend/src/components/SkillPanel.jsx` — add scope browser + copy-into-scope.
- `frontend/src/components/TemplateManager.jsx` — file-based role catalog view.
- `frontend/src/i18n/keys.js`, `frontend/src/i18n/locales/zh.json`, `frontend/src/i18n/locales/en.json` — new keys.

---

## Interfaces consumed from Plans 1–2 (do not re-implement)

- `skills.scope.parse_descriptor(s: str) -> Scope` — descriptors: `system`, `group:<gid>`, `role:<gid>:<role>`, `template:<lang>:<role>`, `bot:<gid>:<bot_id>`. Raises `ValueError` on unknown kind / unsafe segment / bad shape. Each scope has `.dir() -> Path`.
- `skills.store.SkillStore` — `.list(scope) -> list[dict]` (scan entries: `{name, ...}`), `.read(scope, name) -> str`, `.write(scope, name, content) -> {name, high_privilege}`, `.delete(scope, name) -> None` (idempotent), `.copy(src, name, dst) -> None` (raises if source missing). All raise `ValueError` on unsafe `name`.
- `skills.role_meta.read_role_meta(role_dir: Path) -> dict | None` — `{display_name, avatar_color, system_prompt}` (missing keys → None); file absent / parse fail → None.
- `workspace.layout.group_roles_dir(gid) -> Path`, `layout.templates_roles_dir(lang) -> Path`, `layout.get_group_language(gid) -> str` ("zh" default).

---

### Task 1: Role catalog enumeration (`list_role_catalog`)

Pure function that lists role directories under a roles root with joined metadata + skill count. Reused by both catalog endpoints and by add_member validation.

**Files:**
- Create: `backend/skills/role_catalog.py`
- Test: `backend/tests/test_role_catalog.py`

**Interfaces:**
- Consumes: `skills.role_meta.read_role_meta`.
- Produces: `list_role_catalog(roles_root: Path) -> list[dict]` where each dict is `{"role": str, "display_name": str, "avatar_color": str | None, "system_prompt": str | None, "skill_count": int}`, sorted by `role`. Missing root → `[]`. `display_name` falls back to the directory name when `role.yaml` lacks it.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_role_catalog.py
import unittest
import tempfile
from pathlib import Path

from skills.role_catalog import list_role_catalog
from skills.role_meta import write_role_meta


class TestListRoleCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _role(self, name, *, skills=(), meta=None):
        d = self.root / name
        (d / "skills").mkdir(parents=True)
        for s in skills:
            (d / "skills" / f"{s}.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
        if meta is not None:
            write_role_meta(d, meta)

    def test_missing_root_returns_empty(self):
        self.assertEqual(list_role_catalog(self.root / "nope"), [])

    def test_lists_roles_sorted_with_meta_and_count(self):
        self._role("系统架构师", skills=["design-architecture", "tech-stack-review"],
                   meta={"display_name": "系统架构师", "avatar_color": "#8b5cf6",
                         "system_prompt": "你是架构师"})
        self._role("PM", skills=["write-spec"], meta={"avatar_color": "#0ea5e9"})
        rows = list_role_catalog(self.root)
        self.assertEqual([r["role"] for r in rows], ["PM", "系统架构师"])  # sorted
        pm, arch = rows[0], rows[1]
        self.assertEqual(pm["display_name"], "PM")          # falls back to dir name
        self.assertEqual(pm["avatar_color"], "#0ea5e9")
        self.assertIsNone(pm["system_prompt"])
        self.assertEqual(pm["skill_count"], 1)
        self.assertEqual(arch["display_name"], "系统架构师")
        self.assertEqual(arch["skill_count"], 2)

    def test_role_without_skills_dir_counts_zero(self):
        (self.root / "Empty").mkdir(parents=True)
        rows = list_role_catalog(self.root)
        self.assertEqual(rows[0]["skill_count"], 0)

    def test_non_dir_entries_ignored(self):
        (self.root / "stray.txt").write_text("x", encoding="utf-8")
        self._role("PM", skills=["write-spec"])
        rows = list_role_catalog(self.root)
        self.assertEqual([r["role"] for r in rows], ["PM"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_role_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skills.role_catalog'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/skills/role_catalog.py
"""角色目录枚举：列出某 roles 根下的角色目录 + role.yaml 元数据 + 技能数。

纯读、无副作用。被 /api/templates/roles、/api/groups/{id}/roles 与 add_member
校验共用。discovery 永不读 role.yaml；角色元数据只走 read_role_meta。"""
from __future__ import annotations
from pathlib import Path

from .role_meta import read_role_meta


def list_role_catalog(roles_root: Path) -> list[dict]:
    """列出 roles_root/* 角色目录。返回按 role 名排序的
    [{role, display_name, avatar_color, system_prompt, skill_count}]。
    根不存在 → []。display_name 缺省回退目录名。"""
    out: list[dict] = []
    if not roles_root.exists():
        return out
    for d in sorted(roles_root.iterdir()):
        if not d.is_dir():
            continue
        meta = read_role_meta(d) or {}
        skills_dir = d / "skills"
        skill_count = len(list(skills_dir.glob("*.md"))) if skills_dir.exists() else 0
        out.append({
            "role": d.name,
            "display_name": meta.get("display_name") or d.name,
            "avatar_color": meta.get("avatar_color"),
            "system_prompt": meta.get("system_prompt"),
            "skill_count": skill_count,
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_role_catalog.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/skills/role_catalog.py backend/tests/test_role_catalog.py
git commit -m "feat(skills): role_catalog enumeration over a roles root"
```

---

### Task 2: Scope skills API — read endpoints + router registration

New `api/skills.py` router with list + read endpoints driven by scope descriptors, mounted in `main.py`. This task establishes the router and its read surface; Task 3 adds writes; Task 4 adds the catalog endpoints.

**Files:**
- Create: `backend/api/skills.py`
- Modify: `backend/main.py` (import + `include_router`)
- Test: `backend/tests/test_skills_api.py`

**Interfaces:**
- Consumes: `skills.scope.parse_descriptor`, `skills.store.SkillStore`.
- Produces:
  - `GET /api/skills?scope=<descriptor>` → `{"skills": [...]}` (`SkillStore().list`)
  - `GET /api/skills/content?scope=<descriptor>&name=<name>` → `{"name": str, "content": str}` (`SkillStore().read`)
  - Invalid descriptor → 400; unsafe/absent skill file → 404.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_skills_api.py
import unittest
import os
import sys
import shutil
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
import db.writer as _db_writer
import workspace

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_skills_api.db")
TEST_WS = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "test_skills_api_ws"
database.DB_PATH = TEST_DB_PATH
_db_writer.DB_PATH = TEST_DB_PATH
workspace.WORKSPACE_ROOT = TEST_WS
import skills.constants as _skill_const
_skill_const.WORKSPACE_ROOT = TEST_WS

from main import app
from httpx import AsyncClient, ASGITransport
from workspace import layout


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class TestSkillsReadApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)
        # Seed a group role skill: group_7/roles/PM/skills/write-spec.md
        d = layout.group_roles_dir(7) / "PM" / "skills"
        d.mkdir(parents=True)
        (d / "write-spec.md").write_text("---\nname: write-spec\n---\nspec body", encoding="utf-8")

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)

    async def test_list_role_scope(self):
        async with _client() as c:
            r = await c.get("/api/skills", params={"scope": "role:7:PM"})
        self.assertEqual(r.status_code, 200)
        names = [s["name"] for s in r.json()["skills"]]
        self.assertIn("write-spec", names)

    async def test_read_skill_content(self):
        async with _client() as c:
            r = await c.get("/api/skills/content", params={"scope": "role:7:PM", "name": "write-spec"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("spec body", r.json()["content"])

    async def test_bad_descriptor_400(self):
        async with _client() as c:
            r = await c.get("/api/skills", params={"scope": "role:7:../etc"})
        self.assertEqual(r.status_code, 400)

    async def test_missing_skill_404(self):
        async with _client() as c:
            r = await c.get("/api/skills/content", params={"scope": "role:7:PM", "name": "nope"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_skills_api.py -v`
Expected: FAIL (404 on `/api/skills` — route not registered)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/api/skills.py
"""Scope-descriptor skill API: browse/read/write/copy skills at any layer, plus
role-catalog listing. Path-safety lives entirely in skills.scope.parse_descriptor
(_safe_segment); this module never builds a path by hand. Auth is router-level
(token-only, DFT-082)."""
from fastapi import APIRouter, HTTPException

from skills.scope import parse_descriptor
from skills.store import SkillStore

router = APIRouter()
_store = SkillStore()


def _scope(descriptor: str):
    try:
        return parse_descriptor(descriptor)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/skills")
async def list_scope_skills(scope: str):
    return {"skills": _store.list(_scope(scope))}


@router.get("/api/skills/content")
async def read_scope_skill(scope: str, name: str):
    try:
        content = _store.read(_scope(scope), name)
    except ValueError as e:           # unsafe name
        raise HTTPException(400, str(e))
    except (FileNotFoundError, NotADirectoryError):
        raise HTTPException(404, f"skill not found: {name!r}")
    return {"name": name, "content": content}
```

In `backend/main.py`, beside the other `from api.* import router` lines (near line 26-34):

```python
from api.skills import router as skills_router
```

and beside the other `app.include_router(...)` calls (near line 113-121):

```python
app.include_router(skills_router, dependencies=[Depends(auth.get_current_user)])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_skills_api.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/api/skills.py backend/main.py backend/tests/test_skills_api.py
git commit -m "feat(api): scope-descriptor skill read endpoints"
```

---

### Task 3: Scope skills API — write / delete / copy endpoints

Add the mutating endpoints to `api/skills.py`. Copy is the cross-scope primitive the UI uses to pull a System/template skill into a group/role.

**Files:**
- Modify: `backend/api/skills.py`
- Test: `backend/tests/test_skills_api.py` (extend)

**Interfaces:**
- Consumes: `SkillStore().write/delete/copy`.
- Produces:
  - `POST /api/skills` body `{"scope": str, "name": str, "content": str}` → `{"name", "high_privilege": [...]}`
  - `DELETE /api/skills?scope=<descriptor>&name=<name>` → `{"ok": true}` (idempotent)
  - `POST /api/skills/copy` body `{"src": str, "name": str, "dst": str}` → `{"ok": true}`; missing source → 404.

- [ ] **Step 1: Write the failing test (append to test_skills_api.py)**

```python
class TestSkillsWriteApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)
        d = layout.templates_roles_dir("zh") / "PM" / "skills"
        d.mkdir(parents=True)
        (d / "write-spec.md").write_text("---\nname: write-spec\n---\nspec", encoding="utf-8")

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)

    async def test_write_then_read(self):
        async with _client() as c:
            w = await c.post("/api/skills", json={
                "scope": "group:7", "name": "house-rule", "content": "---\nname: house-rule\n---\nbe nice"})
            self.assertEqual(w.status_code, 200)
            r = await c.get("/api/skills/content", params={"scope": "group:7", "name": "house-rule"})
        self.assertIn("be nice", r.json()["content"])

    async def test_copy_template_to_role(self):
        async with _client() as c:
            cp = await c.post("/api/skills/copy", json={
                "src": "template:zh:PM", "name": "write-spec", "dst": "role:7:PM"})
            self.assertEqual(cp.status_code, 200)
            r = await c.get("/api/skills", params={"scope": "role:7:PM"})
        self.assertIn("write-spec", [s["name"] for s in r.json()["skills"]])

    async def test_copy_missing_source_404(self):
        async with _client() as c:
            cp = await c.post("/api/skills/copy", json={
                "src": "template:zh:PM", "name": "ghost", "dst": "role:7:PM"})
        self.assertEqual(cp.status_code, 404)

    async def test_delete_is_idempotent(self):
        async with _client() as c:
            await c.post("/api/skills", json={"scope": "group:7", "name": "tmp", "content": "x"})
            d1 = await c.delete("/api/skills", params={"scope": "group:7", "name": "tmp"})
            d2 = await c.delete("/api/skills", params={"scope": "group:7", "name": "tmp"})
        self.assertEqual(d1.status_code, 200)
        self.assertEqual(d2.status_code, 200)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_skills_api.py::TestSkillsWriteApi -v`
Expected: FAIL (405/404 — write/copy/delete routes absent)

- [ ] **Step 3: Write minimal implementation (append to api/skills.py)**

```python
from pydantic import BaseModel


class WriteSkillRequest(BaseModel):
    scope: str
    name: str
    content: str


class CopySkillRequest(BaseModel):
    src: str
    name: str
    dst: str


@router.post("/api/skills")
async def write_scope_skill(req: WriteSkillRequest):
    try:
        return _store.write(_scope(req.scope), req.name, req.content)
    except ValueError as e:           # unsafe name
        raise HTTPException(400, str(e))


@router.delete("/api/skills")
async def delete_scope_skill(scope: str, name: str):
    try:
        _store.delete(_scope(scope), name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/api/skills/copy")
async def copy_scope_skill(req: CopySkillRequest):
    try:
        _store.copy(_scope(req.src), req.name, _scope(req.dst))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (FileNotFoundError, NotADirectoryError):
        raise HTTPException(404, f"source skill not found: {req.name!r}")
    return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_skills_api.py -v`
Expected: PASS (all read + write classes)

- [ ] **Step 5: Commit**

```bash
git add backend/api/skills.py backend/tests/test_skills_api.py
git commit -m "feat(api): scope-descriptor skill write/delete/copy endpoints"
```

---

### Task 4: Role-catalog endpoints

Add `/api/templates/roles` (global template roles per language) and `/api/groups/{group_id}/roles` (a group's provisioned roles) to `api/skills.py`, both powered by `list_role_catalog`.

**Files:**
- Modify: `backend/api/skills.py`
- Test: `backend/tests/test_skills_api.py` (extend)

**Interfaces:**
- Consumes: `skills.role_catalog.list_role_catalog`, `workspace.layout.templates_roles_dir`, `layout.group_roles_dir`.
- Produces:
  - `GET /api/templates/roles?lang=zh` → `{"lang": str, "roles": [...catalog rows...]}` (lang defaults to `"zh"`)
  - `GET /api/groups/{group_id}/roles` → `{"group_id": int, "roles": [...catalog rows...]}`

- [ ] **Step 1: Write the failing test (append to test_skills_api.py)**

```python
class TestRoleCatalogApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)
        from skills.role_meta import write_role_meta
        tpl = layout.templates_roles_dir("zh") / "PM"
        (tpl / "skills").mkdir(parents=True)
        (tpl / "skills" / "write-spec.md").write_text("---\nname: write-spec\n---\nx", encoding="utf-8")
        write_role_meta(tpl, {"display_name": "需求分析师", "avatar_color": "#0ea5e9"})
        grp = layout.group_roles_dir(7) / "PM"
        (grp / "skills").mkdir(parents=True)
        write_role_meta(grp, {"display_name": "需求分析师", "avatar_color": "#0ea5e9"})

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)

    async def test_template_roles_default_lang_zh(self):
        async with _client() as c:
            r = await c.get("/api/templates/roles")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["lang"], "zh")
        pm = next(x for x in body["roles"] if x["role"] == "PM")
        self.assertEqual(pm["display_name"], "需求分析师")
        self.assertEqual(pm["skill_count"], 1)

    async def test_group_roles(self):
        async with _client() as c:
            r = await c.get("/api/groups/7/roles")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["role"] for x in r.json()["roles"]], ["PM"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_skills_api.py::TestRoleCatalogApi -v`
Expected: FAIL (404 — catalog routes absent)

- [ ] **Step 3: Write minimal implementation (append to api/skills.py)**

Add the import at the top of the file alongside the existing imports:

```python
from skills.role_catalog import list_role_catalog
from workspace import layout
```

Add the endpoints:

```python
@router.get("/api/templates/roles")
async def list_template_roles(lang: str = "zh"):
    return {"lang": lang, "roles": list_role_catalog(layout.templates_roles_dir(lang))}


@router.get("/api/groups/{group_id}/roles")
async def list_group_roles(group_id: int):
    return {"group_id": group_id, "roles": list_role_catalog(layout.group_roles_dir(group_id))}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_skills_api.py -v`
Expected: PASS (all classes)

- [ ] **Step 5: Commit**

```bash
git add backend/api/skills.py backend/tests/test_skills_api.py
git commit -m "feat(api): role-catalog endpoints (template + group)"
```

---

### Task 5: add_member role validation + system_prompt snapshot

When a **bot** is added with a `role`, validate that role against the group's catalog (422 in group language) and snapshot the role's `role.yaml` `system_prompt` when the caller didn't supply one. Humans and roleless bots are unaffected; an un-provisioned (empty) catalog is not enforced (graceful for legacy groups pre-migration).

**Files:**
- Modify: `backend/api/groups.py:add_member`
- Test: `backend/tests/test_member_role_binding.py`

**Interfaces:**
- Consumes: `skills.role_catalog.list_role_catalog`, `skills.role_meta.read_role_meta`, `workspace.layout.group_roles_dir`, `layout.get_group_language`.
- Produces: behavior change only — `add_member` returns 422 on an off-catalog role for a bot; bound bots get a snapshotted `system_prompt`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_member_role_binding.py
import unittest
import os
import sys
import shutil
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
import db.writer as _db_writer
import workspace

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_role_binding.db")
TEST_WS = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "test_role_binding_ws"
database.DB_PATH = TEST_DB_PATH
_db_writer.DB_PATH = TEST_DB_PATH
workspace.WORKSPACE_ROOT = TEST_WS
import skills.constants as _skill_const
_skill_const.WORKSPACE_ROOT = TEST_WS

from main import app
from httpx import AsyncClient, ASGITransport
from workspace import layout
from skills.role_meta import write_role_meta


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class TestMemberRoleBinding(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        for p in (TEST_DB_PATH,):
            if os.path.exists(p):
                os.remove(p)
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)
        await database.init_db()
        async with _db_writer.write_connect() as db:
            await db.execute("INSERT INTO groups (id, name) VALUES (7, 'g7')")
            await db.commit()
        # Group 7 catalog: PM (with a system_prompt to snapshot)
        pm = layout.group_roles_dir(7) / "PM"
        (pm / "skills").mkdir(parents=True)
        write_role_meta(pm, {"display_name": "需求分析师", "system_prompt": "你是需求分析师"})

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        if TEST_WS.exists():
            shutil.rmtree(TEST_WS)

    async def test_off_catalog_role_rejected_422_zh(self):
        async with _client() as c:
            r = await c.post("/api/groups/7/members",
                             json={"name": "b1", "type": "bot", "role": "Wizard"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Wizard", r.json()["detail"])

    async def test_valid_role_snapshots_system_prompt(self):
        async with _client() as c:
            r = await c.post("/api/groups/7/members",
                             json={"name": "b2", "type": "bot", "role": "PM"})
        self.assertEqual(r.status_code, 200)
        mid = r.json()["id"]
        async with database.get_db() as db:
            m = await database.get_member(db, mid)
        self.assertEqual(m["system_prompt"], "你是需求分析师")

    async def test_explicit_system_prompt_not_overwritten(self):
        async with _client() as c:
            r = await c.post("/api/groups/7/members",
                             json={"name": "b3", "type": "bot", "role": "PM",
                                   "system_prompt": "custom"})
        mid = r.json()["id"]
        async with database.get_db() as db:
            m = await database.get_member(db, mid)
        self.assertEqual(m["system_prompt"], "custom")

    async def test_human_skips_validation(self):
        async with _client() as c:
            r = await c.post("/api/groups/7/members",
                             json={"name": "alice", "type": "human", "role": "anything"})
        self.assertEqual(r.status_code, 200)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_member_role_binding.py -v`
Expected: FAIL (off-catalog role returns 200, not 422; snapshot absent)

- [ ] **Step 3: Write minimal implementation**

In `backend/api/groups.py`, add imports near the top (with the other `from ...` lines):

```python
from skills.role_catalog import list_role_catalog
from skills.role_meta import read_role_meta
from workspace import layout
```

In `add_member`, immediately after the early-return de-dup block (after the `if existing: return ...`) and before `config_str = json.dumps(...)`, insert:

```python
        # Role binding (bots only): validate against the group's role catalog and
        # snapshot the role's system_prompt when the caller didn't supply one.
        # An empty catalog (un-provisioned legacy group) is not enforced.
        system_prompt = req.system_prompt
        if req.type == "bot" and req.role:
            catalog = {r["role"] for r in list_role_catalog(layout.group_roles_dir(group_id))}
            if catalog and req.role not in catalog:
                lang = layout.get_group_language(group_id)
                msg = (f"角色 '{req.role}' 不在本群角色目录中" if lang == "zh"
                       else f"Role '{req.role}' is not in this group's role catalog")
                raise HTTPException(422, msg)
            if not (system_prompt and system_prompt.strip()):
                meta = read_role_meta(layout.group_roles_dir(group_id) / req.role)
                if meta and meta.get("system_prompt"):
                    system_prompt = meta["system_prompt"]
```

Then change the INSERT to use the local `system_prompt` instead of `req.system_prompt` (the `system_prompt` column value in the VALUES tuple), and the `init_bot_workspace` dict's `"system_prompt": req.system_prompt` to `"system_prompt": system_prompt`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_member_role_binding.py -v`
Expected: PASS (4 tests)

Then run the existing member-route regression to confirm no break:

Run: `python3 -m pytest tests/test_member_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/groups.py backend/tests/test_member_role_binding.py
git commit -m "feat(api): validate bot role against group catalog + snapshot role system_prompt"
```

---

### Task 6: Frontend api-client wrappers

Add a `skillsApi.js` module wrapping the new endpoints, mirroring the existing `authFetch` style in `src/api.js`. Member-creation already flows through `addMember` in `api.js`; this task only adds the new scope/role calls, with vitest wiring tests in the established `api.test.js` style.

**Files:**
- Create: `frontend/src/skillsApi.js`
- Test: `frontend/src/skillsApi.test.js`

**Interfaces:**
- Consumes: `authFetch` from `./api` (the existing token-attaching fetch wrapper).
- Produces (all return parsed JSON, throw on non-ok):
  - `fetchScopeSkills(scope)` → `GET /api/skills?scope=`
  - `fetchSkillContent(scope, name)` → `GET /api/skills/content?scope=&name=`
  - `writeScopeSkill(scope, name, content)` → `POST /api/skills`
  - `deleteScopeSkill(scope, name)` → `DELETE /api/skills?scope=&name=`
  - `copyScopeSkill(src, name, dst)` → `POST /api/skills/copy`
  - `fetchTemplateRoles(lang)` → `GET /api/templates/roles?lang=`
  - `fetchGroupRoles(groupId)` → `GET /api/groups/{id}/roles`

First confirm the export name of the fetch wrapper:

Run: `grep -n "authFetch\|export" frontend/src/api.js | head`
Use whatever the file actually exports (it is `authFetch` in the current code). If `authFetch` is not exported, export it.

- [ ] **Step 1: Write the failing test**

```javascript
// frontend/src/skillsApi.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  fetchScopeSkills, copyScopeSkill, fetchTemplateRoles, fetchGroupRoles,
} from './skillsApi'

describe('skillsApi wiring', () => {
  beforeEach(() => {
    global.localStorage = { getItem: () => 'tok', setItem: () => {}, removeItem: () => {} }
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ skills: [], roles: [] }) }))
  })
  afterEach(() => vi.restoreAllMocks())

  it('fetchScopeSkills encodes the scope descriptor', async () => {
    await fetchScopeSkills('role:7:PM')
    const [url] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/skills?scope=role%3A7%3APM')
  })

  it('copyScopeSkill posts src/name/dst', async () => {
    await copyScopeSkill('template:zh:PM', 'write-spec', 'role:7:PM')
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/skills/copy')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual({ src: 'template:zh:PM', name: 'write-spec', dst: 'role:7:PM' })
  })

  it('fetchTemplateRoles passes lang', async () => {
    await fetchTemplateRoles('en')
    expect(global.fetch.mock.calls[0][0]).toBe('/api/templates/roles?lang=en')
  })

  it('fetchGroupRoles hits the group path', async () => {
    await fetchGroupRoles(7)
    expect(global.fetch.mock.calls[0][0]).toBe('/api/groups/7/roles')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/skillsApi.test.js`
Expected: FAIL (cannot resolve `./skillsApi`)

- [ ] **Step 3: Write minimal implementation**

```javascript
// frontend/src/skillsApi.js
import { authFetch } from './api'

async function jsonOrThrow(res) {
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* non-json */ }
    throw new Error(detail)
  }
  return res.json()
}

export async function fetchScopeSkills(scope) {
  return jsonOrThrow(await authFetch(`/api/skills?scope=${encodeURIComponent(scope)}`))
}

export async function fetchSkillContent(scope, name) {
  return jsonOrThrow(await authFetch(
    `/api/skills/content?scope=${encodeURIComponent(scope)}&name=${encodeURIComponent(name)}`))
}

export async function writeScopeSkill(scope, name, content) {
  return jsonOrThrow(await authFetch('/api/skills', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope, name, content }),
  }))
}

export async function deleteScopeSkill(scope, name) {
  return jsonOrThrow(await authFetch(
    `/api/skills?scope=${encodeURIComponent(scope)}&name=${encodeURIComponent(name)}`,
    { method: 'DELETE' }))
}

export async function copyScopeSkill(src, name, dst) {
  return jsonOrThrow(await authFetch('/api/skills/copy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src, name, dst }),
  }))
}

export async function fetchTemplateRoles(lang = 'zh') {
  return jsonOrThrow(await authFetch(`/api/templates/roles?lang=${encodeURIComponent(lang)}`))
}

export async function fetchGroupRoles(groupId) {
  return jsonOrThrow(await authFetch(`/api/groups/${groupId}/roles`))
}
```

If `api.js` does not already `export` its fetch wrapper, add `export` to its declaration (`export function authFetch(...)` / `export async function authFetch(...)`) — do not duplicate the wrapper.

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run src/skillsApi.test.js`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/skillsApi.js frontend/src/skillsApi.test.js frontend/src/api.js
git commit -m "feat(ui): api-client wrappers for scope skills + role catalog"
```

---

### Task 7: MemberList role dropdown + autofill

Replace the role free-text input (`MemberList.jsx:398-404`) with a dropdown sourced from the group's role catalog. Selecting a role autofills `avatar_color` (and shows the role's prompt as the system_prompt placeholder). Surface a 422 from add_member as an inline error. Keep a free-text fallback when the catalog is empty (legacy/un-provisioned groups).

**Files:**
- Modify: `frontend/src/components/MemberList.jsx`
- Test: `frontend/src/components/MemberList.roles.test.jsx`

**Interfaces:**
- Consumes: `fetchGroupRoles` from `../skillsApi`; `K.member.*` i18n keys (added in Task 10 — for this task, add the literal keys to `keys.js` and both locale files inline so the component renders, and Task 10 folds them into the parity test).
- The component needs the current group id. `ChatWindow.jsx` renders `<MemberList ... />` at lines ~556 and ~571 and has `activeGroupId` in scope — pass `groupId={activeGroupId}` to both render sites.

- [ ] **Step 1: Write the failing test**

```jsx
// frontend/src/components/MemberList.roles.test.jsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import MemberList from './MemberList'

vi.mock('../skillsApi', () => ({
  fetchGroupRoles: vi.fn(() => Promise.resolve({
    roles: [
      { role: 'PM', display_name: '需求分析师', avatar_color: '#0ea5e9', system_prompt: '你是需求分析师', skill_count: 3 },
      { role: '系统架构师', display_name: '系统架构师', avatar_color: '#8b5cf6', system_prompt: '', skill_count: 2 },
    ],
  })),
}))

describe('MemberList role dropdown', () => {
  beforeEach(() => {
    global.localStorage = { getItem: () => 'zh', setItem: () => {}, removeItem: () => {} }
  })
  afterEach(() => vi.restoreAllMocks())

  it('renders a role option per catalog entry for a bot in a provisioned group', async () => {
    render(<MemberList groupId={7} onAddMember={() => {}} onClose={() => {}}
                       initialData={{ type: 'bot' }} />)
    await waitFor(() => {
      expect(screen.getByRole('option', { name: /需求分析师/ })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: /系统架构师/ })).toBeInTheDocument()
    })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/MemberList.roles.test.jsx`
Expected: FAIL (no role options rendered from a catalog)

- [ ] **Step 3: Write minimal implementation**

In `MemberList.jsx`:

1. Add imports at the top:

```jsx
import { fetchGroupRoles } from '../skillsApi'
```

2. Accept `groupId` in the component signature: `export default function MemberList({ groupId, onAddMember, onEditMember, onClose, initialData }) {`

3. Add catalog state + load effect (near the other `useState`/`useEffect` hooks):

```jsx
  const [roleCatalog, setRoleCatalog] = useState([])
  useEffect(() => {
    if (!groupId) return
    fetchGroupRoles(groupId).then((d) => setRoleCatalog(d.roles || [])).catch(() => setRoleCatalog([]))
  }, [groupId])
```

4. Replace the role `<input>` block (currently `MemberList.jsx:398-404`) with a catalog-driven dropdown that falls back to free text when the catalog is empty:

```jsx
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.role)}</label>
                {roleCatalog.length > 0 ? (
                  <select
                    className="w-full bg-gray-800 rounded px-3 py-2 text-sm"
                    value={form.role}
                    onChange={(e) => {
                      const role = e.target.value
                      const meta = roleCatalog.find((r) => r.role === role)
                      setField({
                        role,
                        ...(meta?.avatar_color ? { avatar_color: meta.avatar_color } : {}),
                      })
                    }}
                  >
                    <option value="">{t(K.member.roleSelectPlaceholder)}</option>
                    {roleCatalog.map((r) => (
                      <option key={r.role} value={r.role}>
                        {r.display_name} ({r.skill_count})
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="w-full bg-gray-800 rounded px-3 py-2 text-sm"
                    placeholder={t(K.member.rolePlaceholder2)}
                    value={form.role}
                    onChange={(e) => setField({ role: e.target.value })}
                  />
                )}
```

5. In the submit handler, when `onAddMember`/the add call rejects, set an error message from the thrown error and render it near the submit button. Locate the existing submit handler and wrap its call in try/catch:

```jsx
  const [submitError, setSubmitError] = useState('')
  // inside the submit handler, replace the bare call with:
  //   try { await onAddMember(payload) } catch (err) { setSubmitError(err.message); return }
```

   Render `{submitError && <p className="text-xs text-red-400 mt-2">{submitError}</p>}` above/below the submit button. (If `onAddMember` is synchronous in the current code, keep it synchronous and only add the catch when it returns a promise — inspect the call site and match it.)

6. Add the new i18n keys used here to `keys.js` and both locale files:

   - `K.member.roleSelectPlaceholder` → zh `"选择角色（来自本群角色目录）"`, en `"Select a role (from this group's catalog)"`

7. In `ChatWindow.jsx`, pass `groupId={activeGroupId}` to both `<MemberList ... />` render sites (~lines 556 and 571).

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run src/components/MemberList.roles.test.jsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MemberList.jsx frontend/src/components/MemberList.roles.test.jsx frontend/src/components/ChatWindow.jsx frontend/src/i18n/keys.js frontend/src/i18n/locales/zh.json frontend/src/i18n/locales/en.json
git commit -m "feat(ui): role dropdown sourced from group role catalog in MemberList"
```

---

### Task 8: SkillPanel scope browser + copy-into-scope

`SkillPanel` currently lists one bot's effective skills (member-centric). Add a "browse other scopes" mode: a scope selector (Group / its Role / System / a template language) that lists that scope's raw skills via `fetchScopeSkills`, with a "copy into this bot's group/role" action via `copyScopeSkill`. The existing member-centric view and its endpoints stay unchanged.

**Files:**
- Modify: `frontend/src/components/SkillPanel.jsx`
- Test: `frontend/src/components/SkillPanel.scope.test.jsx`

**Interfaces:**
- Consumes: `fetchScopeSkills`, `copyScopeSkill` from `../skillsApi`; `bot` (`{id, role}`) and `groupId` props it already receives (`WorkspacePanel.jsx:57`).
- Scope descriptor construction is centralized in a small helper inside the component: `group:<groupId>`, `role:<groupId>:<bot.role>`, `system`, `template:<lang>:<bot.role>`.

- [ ] **Step 1: Write the failing test**

```jsx
// frontend/src/components/SkillPanel.scope.test.jsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import SkillPanel from './SkillPanel'

vi.mock('../skillsApi', () => ({
  fetchScopeSkills: vi.fn(() => Promise.resolve({ skills: [{ name: 'design-architecture' }] })),
  copyScopeSkill: vi.fn(() => Promise.resolve({ ok: true })),
}))

describe('SkillPanel scope browser', () => {
  beforeEach(() => {
    global.localStorage = { getItem: () => 'zh', setItem: () => {}, removeItem: () => {} }
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ skills: [] }) }))
  })
  afterEach(() => vi.restoreAllMocks())

  it('lists skills from a chosen scope and can copy one', async () => {
    const { fetchScopeSkills, copyScopeSkill } = await import('../skillsApi')
    render(<SkillPanel bot={{ id: 1, role: '系统架构师' }} groupId={7} onClose={() => {}} />)
    fireEvent.click(screen.getByTestId('browse-scopes-toggle'))
    await waitFor(() => expect(fetchScopeSkills).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('design-architecture')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('copy-skill-design-architecture'))
    await waitFor(() => expect(copyScopeSkill).toHaveBeenCalled())
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/SkillPanel.scope.test.jsx`
Expected: FAIL (no scope browser controls)

- [ ] **Step 3: Write minimal implementation**

In `SkillPanel.jsx`:

1. Import: `import { fetchScopeSkills, copyScopeSkill } from '../skillsApi'`
2. State:

```jsx
  const [browsing, setBrowsing] = useState(false)
  const [browseScope, setBrowseScope] = useState('system')
  const [scopeSkills, setScopeSkills] = useState([])

  const scopeDescriptor = useCallback((kind) => {
    if (kind === 'group') return `group:${groupId}`
    if (kind === 'role') return `role:${groupId}:${bot.role}`
    return 'system'
  }, [groupId, bot.role])

  const loadScope = useCallback(async (kind) => {
    const d = await fetchScopeSkills(scopeDescriptor(kind))
    setScopeSkills(d.skills || [])
  }, [scopeDescriptor])

  useEffect(() => {
    if (browsing) loadScope(browseScope)
  }, [browsing, browseScope, loadScope])
```

3. Render a toggle button `data-testid="browse-scopes-toggle"` (label `t(K.skill.browseScopes)`), a scope `<select>` (options Group/Role/System via `t(K.skill.scopeGroup/scopeRole/scopeSystem)`), the `scopeSkills` list, and per-row a copy button `data-testid={"copy-skill-" + s.name}` that calls:

```jsx
  await copyScopeSkill(scopeDescriptor(browseScope), s.name, `group:${groupId}`)
```

   then re-loads the member-centric list (`load()`).

4. Add i18n keys `K.skill.browseScopes`, `K.skill.scopeGroup`, `K.skill.scopeRole`, `K.skill.scopeSystem`, `K.skill.copyToGroup` to `keys.js` + both locales (folded into Task 10's parity test). zh: `"浏览其他层技能" / "群组层" / "角色层" / "系统层" / "复制到本群"`; en: `"Browse other layers" / "Group" / "Role" / "System" / "Copy to group"`.

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run src/components/SkillPanel.scope.test.jsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SkillPanel.jsx frontend/src/components/SkillPanel.scope.test.jsx frontend/src/i18n/keys.js frontend/src/i18n/locales/zh.json frontend/src/i18n/locales/en.json
git commit -m "feat(ui): scope browser + copy-into-group in SkillPanel"
```

---

### Task 9: TemplateManager file-based role catalog view

`TemplateManager` currently manages the legacy `role_templates` DB table. Repurpose it (additively) to show the **file-based** role catalog per language via `fetchTemplateRoles`, listing each role's display_name / avatar / skill_count, and (selecting a role) its skills via `fetchScopeSkills('template:<lang>:<role>')`. Editing skill bodies uses `writeScopeSkill`. The legacy DB-backed section may remain for now (removed in the later `role_templates` DROP cleanup); do not delete it in this plan.

**Files:**
- Modify: `frontend/src/components/TemplateManager.jsx`
- Test: `frontend/src/components/TemplateManager.roles.test.jsx`

**Interfaces:**
- Consumes: `fetchTemplateRoles`, `fetchScopeSkills` from `../skillsApi`.

- [ ] **Step 1: Write the failing test**

```jsx
// frontend/src/components/TemplateManager.roles.test.jsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import TemplateManager from './TemplateManager'

vi.mock('../skillsApi', () => ({
  fetchTemplateRoles: vi.fn(() => Promise.resolve({
    lang: 'zh',
    roles: [{ role: 'PM', display_name: '需求分析师', avatar_color: '#0ea5e9', system_prompt: '', skill_count: 3 }],
  })),
  fetchScopeSkills: vi.fn(() => Promise.resolve({ skills: [] })),
}))
// keep the legacy DB template fetch from blowing up
vi.mock('../api', () => ({ authFetch: vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })) }))

describe('TemplateManager role catalog', () => {
  beforeEach(() => { global.localStorage = { getItem: () => 'zh', setItem: () => {}, removeItem: () => {} } })
  afterEach(() => vi.restoreAllMocks())

  it('lists file-based template roles', async () => {
    render(<TemplateManager onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText(/需求分析师/)).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/TemplateManager.roles.test.jsx`
Expected: FAIL (file-based role catalog not rendered)

- [ ] **Step 3: Write minimal implementation**

In `TemplateManager.jsx`, add:

1. `import { fetchTemplateRoles, fetchScopeSkills } from '../skillsApi'`
2. State + effect:

```jsx
  const [lang, setLang] = useState('zh')
  const [tplRoles, setTplRoles] = useState([])
  const [selRole, setSelRole] = useState(null)
  const [roleSkills, setRoleSkills] = useState([])

  useEffect(() => {
    fetchTemplateRoles(lang).then((d) => setTplRoles(d.roles || [])).catch(() => setTplRoles([]))
  }, [lang])

  useEffect(() => {
    if (!selRole) return
    fetchScopeSkills(`template:${lang}:${selRole}`).then((d) => setRoleSkills(d.skills || [])).catch(() => setRoleSkills([]))
  }, [selRole, lang])
```

3. Render a language toggle (zh/en), a list of `tplRoles` rows (display_name + `(${skill_count})` + avatar swatch) each selectable (set `selRole`), and the selected role's `roleSkills` names. Use new keys `K.template.fileRolesTitle`, `K.template.lang` + reuse `K.common.*`. zh: `"角色模板（文件）" / "语言"`; en: `"Role templates (files)" / "Language"`.

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run src/components/TemplateManager.roles.test.jsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TemplateManager.jsx frontend/src/components/TemplateManager.roles.test.jsx frontend/src/i18n/keys.js frontend/src/i18n/locales/zh.json frontend/src/i18n/locales/en.json
git commit -m "feat(ui): file-based role catalog view in TemplateManager"
```

---

### Task 10: i18n key parity test

Lock the invariant that every leaf in the `K` registry has a value in both `zh.json` and `en.json`, and that neither locale has orphan keys. This catches any key added across Tasks 7–9 that was missed in one locale.

**Files:**
- Create: `frontend/src/i18n/i18n.test.js`

**Interfaces:**
- Consumes: `K` from `./keys`, `zh.json`, `en.json`.

- [ ] **Step 1: Write the failing test**

```javascript
// frontend/src/i18n/i18n.test.js
import { describe, it, expect } from 'vitest'
import { K } from './keys'
import zh from './locales/zh.json'
import en from './locales/en.json'

// All dotted key strings referenced by the K registry.
function collectKeys(obj, out = []) {
  for (const v of Object.values(obj)) {
    if (typeof v === 'string') out.push(v)
    else if (v && typeof v === 'object') collectKeys(v, out)
  }
  return out
}

// All dotted leaf paths present in a locale resource.
function collectPaths(obj, prefix = '', out = []) {
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object') collectPaths(v, path, out)
    else out.push(path)
  }
  return out
}

const referenced = collectKeys(K)
const zhPaths = new Set(collectPaths(zh))
const enPaths = new Set(collectPaths(en))

describe('i18n key parity', () => {
  it('every K key resolves in zh', () => {
    expect(referenced.filter((k) => !zhPaths.has(k))).toEqual([])
  })
  it('every K key resolves in en', () => {
    expect(referenced.filter((k) => !enPaths.has(k))).toEqual([])
  })
  it('zh and en have identical leaf paths', () => {
    const onlyZh = [...zhPaths].filter((p) => !enPaths.has(p))
    const onlyEn = [...enPaths].filter((p) => !zhPaths.has(p))
    expect({ onlyZh, onlyEn }).toEqual({ onlyZh: [], onlyEn: [] })
  })
})
```

- [ ] **Step 2: Run test to verify it fails (or surfaces gaps)**

Run (from `frontend/`): `npx vitest run src/i18n/i18n.test.js`
Expected: FAIL listing any keys from Tasks 7–9 missing in a locale, or any pre-existing zh/en path mismatch.

- [ ] **Step 3: Fix gaps until green**

For each reported missing/mismatched key, add the value to the locale file (the new Plan-3 keys: `member.roleSelectPlaceholder`, `skill.browseScopes`, `skill.scopeGroup`, `skill.scopeRole`, `skill.scopeSystem`, `skill.copyToGroup`, `template.fileRolesTitle`, `template.lang`). If the test reveals **pre-existing** zh/en mismatches unrelated to Plan 3, list them in the progress ledger as Minor and fix only the Plan-3 keys here — do not expand scope mid-task; raise the pre-existing set to the human.

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run src/i18n/i18n.test.js`
Expected: PASS (Plan-3 keys; pre-existing mismatches escalated if any)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/i18n/i18n.test.js frontend/src/i18n/keys.js frontend/src/i18n/locales/zh.json frontend/src/i18n/locales/en.json
git commit -m "test(i18n): key parity across K registry and zh/en locales"
```

---

## Self-Review

**Spec coverage** (vs. Plan 3 scope from the design):
- scope-param `/api/skills*` + `/api/skills/copy` → Tasks 2, 3 ✅
- `/api/groups/{id}/roles` + `/api/templates/roles` → Task 4 ✅
- `add_member` role validation (422, group-language) → Task 5 ✅
- bot-creation system_prompt snapshot from role.yaml → Task 5 ✅
- MemberList dropdown → Task 7 ✅
- SkillPanel → Task 8 ✅
- TemplateManager → Task 9 ✅
- i18n keys (zh/en) + consistency test → Tasks 7–10 ✅

**Type consistency:** scope descriptor strings (`role:<gid>:<role>`, `template:<lang>:<role>`, `group:<gid>`, `system`) are identical across backend `parse_descriptor`, `skillsApi.js`, and the components. Catalog row shape `{role, display_name, avatar_color, system_prompt, skill_count}` is produced by `list_role_catalog` (Task 1) and consumed unchanged in Tasks 4/7/9. `SkillStore` method names match Plan 1–2.

**Known follow-ups (out of scope, raise at final review):**
- DROP the legacy `role_templates` table + remove the DB-backed half of `TemplateManager` and `api/templates.py` once the file-based catalog fully replaces it.
- Author real English skill bodies (Plan 2 left en bodies as `TODO` placeholders).
- The 3 Plan-2 Minor robustness items (store.copy lowercase-only name guard; non-zh/en lang seeding note; dry-run B/C report fidelity).
- System-scope writes via `POST /api/skills` are allowed under the trusted-internal threat model (DFT-082); if an admin/non-admin split is ever introduced, gate `system`/`template` writes.

**Open decision for the human before execution:** Task 9 keeps the legacy DB-backed `role_templates` UI alongside the new file-based view (additive, non-destructive). Confirm this is the intended interim state rather than a hard cutover in this plan.
