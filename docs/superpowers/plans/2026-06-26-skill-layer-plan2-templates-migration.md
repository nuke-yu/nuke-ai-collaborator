# Skill-Layer Plan 2 — 角色模板与迁移（后端/数据） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把全局角色模板落成 `templates/<lang>/roles/*` 文件夹（中英双套），建群时把模板拷进群 `group_<id>/roles/*`，并提供一次性迁移脚本把现网的老 `workspaces/roles/` + `role_templates` 表数据迁过去、对齐现有 bot 的 role。

**Architecture:** 纯文件方案。模板是 `templates/<lang>/roles/<role>/{role.yaml, skills/*.md}`；建群拷贝复用 Plan 1 的 `SkillStore.copy(TemplateScope → RoleScope)` 单一原语 + `role.yaml` 文件拷贝，挂在现有 `init_group_workspace` 群初始化路径上，幂等。迁移脚本沿用 `migrate_workspace_layout.py` 的「纯函数规划 + `apply(dry_run)` + `verify` + `main(argv)`」范式：默认 dry-run，`--apply` 才写盘。

**Tech Stack:** Python 3（`python3`，非 `python3.11`）· PyYAML（`yaml`，仓库已用于 `skills/metadata.py`）· `shutil` · 既有 `db.connect_sync()` 读中央 DB · Plan 1 的 `skills.scope` / `skills.store` / `workspace.layout`。

## Global Constraints

- 技能与角色恒为**文件**，不进 DB。`role_templates` 表本期**保留为空壳**（不 DROP，留回滚），下个迁移再删。
- 默认 12 个 zh 角色 = 现有 10 个中文角色（`代码助手` `后端Python专家` `后端Java工程师` `前端工程师` `系统架构师` `需求分析师` `QA测试工程师` `DevOps工程师` `写作助手` `翻译专家`）+ 新增 `Architecture` + `PM`。**丢弃** `developer` / `qa` / `pm` 三个老英文残留目录。
- `Architecture` 默认技能 = `design-architecture` + `tech-stack-review`；`PM` 默认技能 = `write-spec` + `update-board` + `write-user-story`（实现中可微调）。
- 中英文**两套**模板：`templates/zh/roles/*` 与 `templates/en/roles/*`。**en 技能正文本期为骨架/占位**，完整英文正文是后续独立工作量，不在本 Plan。
- `role.yaml` 字段恒为三项：`display_name` / `avatar_color` / `system_prompt`。**discovery 永不读 `role.yaml`**（它只列 skills），元数据只由 role_meta / 将来的 roles API 读。
- 建群拷贝：按 `layout.get_group_language(group_id)`（缺省回退 `zh`）把每个模板角色拷进群；**幂等**（群里已存在该角色目录则跳过，不覆盖）；**System 池（L1）不拷贝**（跨群共享引用）；挂在 `init_group_workspace`。
- 迁移：**默认 dry-run，`--apply` 生效**；apply 前须停服务 + 备份 `workspaces/` 与中央 DB（脚本只打印提醒，不替你备份）。**非破坏性**：bot 的 role 不命中群角色时**自动建同名空角色**（空 skills + 最小 role.yaml），bot 身份与行为不变。老 `workspaces/roles/` 改名 `workspaces/roles.legacy/`（不删，留一发布周期）。
- 运行测试用 `backend/venv/bin/python3 -m pytest`（homebrew python 的 chromadb 是坏的）。命令以功能点粒度只跑相关测试；commit 前跑一次相关全量回归。
- Git：commit 只显示 author = `nuke`，**不加** `Co-Authored-By` 或任何 AI 署名。
- 本 Plan **不含** API/UI/i18n 端点（`/api/skills` scope 化、`/api/groups/{id}/roles`、`/api/templates/roles`、`add_member` role 校验、`MemberList`/`SkillPanel`/`TemplateManager`、i18n key）——全部留给 Plan 3。

## 既有事实（实现者必读，省去重新摸索）

- `WORKSPACE_ROOT` = `backend/workspaces`（`skills/constants.py:4`，可被 `NUKE_WORKSPACE_ROOT` 覆盖）。`TEMPLATES_ROOT = WORKSPACE_ROOT/"templates"`（`constants.py:8`，Plan 1 已加）。
- `workspace/layout.py` 已有：`group_dir(gid)`、`group_roles_dir(gid)→group_dir(gid)/"roles"`、`templates_roles_dir(lang)→_root()/"templates"/lang/"roles"`、`get_group_language(group_id)→读 group_<id>/lang.txt，缺省 "zh"`。`_root()` 实时读 `skills.constants.WORKSPACE_ROOT`（monkeypatch 生效）。
- Plan 1 的 `skills/scope.py`：`TemplateScope(lang, role).dir() == templates/<lang>/roles/<role>/skills`，`RoleScope(gid, role).dir() == group_<gid>/roles/<role>/skills`。`parse_descriptor` 会拒绝带 `/`/`..` 的非法段。
- Plan 1 的 `skills/store.py` `SkillStore`：`list(scope)->[{"name",...}]`、`read/write/delete(scope,name)`、`copy(src_scope, name, dst_scope)`（`shutil.copy2`，拷 `src.dir()/f"{name}.md"` → `dst.dir()/f"{name}.md"`，自动 `mkdir` 目标父目录）。
- 老角色在 git 里：`backend/workspaces/roles/<role>/skills/*.md`（**扁平 `.md`**，非目录式 SKILL.md）。13 个目录，其中 `developer`/`qa`/`pm` 要丢。各角色技能与 catalog 完全对应（已核对）。`PM` 的 `update-board.md` 源自 `roles/pm/skills/`，`write-spec`/`write-user-story` 源自 `roles/需求分析师/skills/`，`Architecture` 两技能源自 `roles/系统架构师/skills/`。
- 中央 DB `role_templates(name, role, system_prompt, avatar_color)`：`role` 列即磁盘角色目录名（如 `系统架构师`），`name` 列是另一套别名（如 `架构师`）。取元数据按 **`role` 列**匹配。`db.connect_sync()` 同步连中央 DB（见 `scripts/migrate_workspace_layout.py:145`）。`members(id, group_id, role, type)`，bot 行 `type='bot'`。
- 群创建链路：`api/groups.py:create_group` → `await init_group_workspace(group_id, req.name)`（`workspace/__init__.py:695`）。建群时群语言未知，`get_group_language` 返回缺省 `zh`——符合 spec 的缺省回退。
- `migrate_workspace_layout.py` 范式：纯函数 `plan_*`（不碰盘、可单测）+ `apply_*(…, dry_run)` + `verify()` + `_load_*_from_db()` + `main(argv)->int`。**照抄此风格**。

## File Structure

- **Create `backend/skills/role_meta.py`** — `role.yaml` 读写：`read_role_meta(role_dir)->dict|None`、`write_role_meta(role_dir, meta)`。单一职责：role.yaml 序列化。建群拷贝、迁移、将来 roles API 都用它。
- **Create `backend/workspace/role_provision.py`** — `provision_group_roles(group_id, lang=None)->list[str]`：把某语言模板角色拷进群，幂等。建群与迁移 step B 共用。
- **Modify `backend/workspace/__init__.py`**（`init_group_workspace` 末尾）— 调 `provision_group_roles(group_id)`。
- **Create `backend/scripts/migrate_role_skills.py`** — 一次性迁移：step A（建 zh 模板）/ A2（建 en 骨架）/ B（灌现有群）/ C（对齐 bot role）/ D（退役老目录）+ `verify` + `main`。
- **Test:** `backend/tests/test_role_meta.py`、`backend/tests/test_role_provision.py`、`backend/tests/test_migrate_role_skills.py`。

---

### Task 1: `role_meta.py` — role.yaml 读写

**Files:**
- Create: `backend/skills/role_meta.py`
- Test: `backend/tests/test_role_meta.py`

**Interfaces:**
- Consumes: `yaml`（PyYAML，仓库已依赖）。
- Produces:
  - `read_role_meta(role_dir: Path) -> dict | None` — 读 `role_dir/role.yaml`，返回 `{"display_name","avatar_color","system_prompt"}`（缺字段为 `None`）；文件不存在或解析失败返回 `None`。
  - `write_role_meta(role_dir: Path, meta: dict) -> None` — 写 `role_dir/role.yaml`，只落 3 个已知字段中 `meta` 里非 None 的项，`allow_unicode=True`、`sort_keys=False`，自动 `mkdir(parents=True)`。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_role_meta.py
import tempfile, unittest
from pathlib import Path
from skills.role_meta import read_role_meta, write_role_meta


class TestRoleMeta(unittest.TestCase):
    def test_write_then_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "系统架构师"
            write_role_meta(d, {
                "display_name": "系统架构师",
                "avatar_color": "#8b5cf6",
                "system_prompt": "你是本项目的系统架构师……",
            })
            self.assertTrue((d / "role.yaml").exists())
            meta = read_role_meta(d)
            self.assertEqual(meta["display_name"], "系统架构师")
            self.assertEqual(meta["avatar_color"], "#8b5cf6")
            self.assertIn("架构师", meta["system_prompt"])

    def test_read_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_role_meta(Path(tmp) / "nope"))

    def test_write_omits_none_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "PM"
            write_role_meta(d, {"display_name": "PM", "avatar_color": None, "extra": "x"})
            meta = read_role_meta(d)
            self.assertEqual(meta["display_name"], "PM")
            self.assertIsNone(meta["avatar_color"])     # not written → None on read
            raw = (d / "role.yaml").read_text(encoding="utf-8")
            self.assertNotIn("extra", raw)              # unknown field dropped
            self.assertNotIn("avatar_color", raw)       # None field dropped


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_role_meta.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.role_meta'`

- [ ] **Step 3: Create role_meta.py**

```python
# backend/skills/role_meta.py
"""role.yaml 元数据读写（display_name / avatar_color / system_prompt）。

discovery 永不读 role.yaml；它只列 skills/。角色元数据只由本模块与上层
roles/templates API 读取。"""
from __future__ import annotations
from pathlib import Path

import yaml

_FIELDS = ("display_name", "avatar_color", "system_prompt")


def read_role_meta(role_dir: Path) -> dict | None:
    """读 role_dir/role.yaml → {display_name, avatar_color, system_prompt}（缺字段为 None）。
    文件不存在 / 解析失败 → None。"""
    fp = role_dir / "role.yaml"
    if not fp.exists():
        return None
    try:
        data = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {k: data.get(k) for k in _FIELDS}


def write_role_meta(role_dir: Path, meta: dict) -> None:
    """写 role_dir/role.yaml：仅 3 个已知字段中非 None 的项，保序、允许中文。"""
    role_dir.mkdir(parents=True, exist_ok=True)
    out = {k: meta[k] for k in _FIELDS if meta.get(k) is not None}
    (role_dir / "role.yaml").write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_role_meta.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/skills/role_meta.py backend/tests/test_role_meta.py
git commit -m "feat(skills): add role.yaml metadata read/write (role_meta)"
```

---

### Task 2: `provision_group_roles` — 模板拷进群（幂等）

**Files:**
- Create: `backend/workspace/role_provision.py`
- Test: `backend/tests/test_role_provision.py`

**Interfaces:**
- Consumes: `workspace.layout`（`templates_roles_dir`、`group_roles_dir`、`get_group_language`）；`skills.store.SkillStore`；`skills.scope.TemplateScope/RoleScope`。
- Produces: `provision_group_roles(group_id: int, lang: str | None = None) -> list[str]` — 把 `templates/<lang>/roles/*` 每个角色拷进 `group_<id>/roles/*`（`role.yaml` + 各 skill 经 `SkillStore.copy`）；**幂等**（群里已存在该角色目录 → 跳过）；返回本次新建的角色名列表。`lang=None` 时取 `get_group_language(group_id)`。模板根不存在 → 返回 `[]`。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_role_provision.py
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from workspace.role_provision import provision_group_roles
from workspace import layout


class TestProvision(unittest.TestCase):
    def _seed_template(self, root: Path, lang: str, role: str, skills: dict):
        tdir = root / "templates" / lang / "roles" / role
        (tdir / "skills").mkdir(parents=True)
        (tdir / "role.yaml").write_text(
            f"display_name: {role}\navatar_color: '#111'\n", encoding="utf-8")
        for name, body in skills.items():
            (tdir / "skills" / f"{name}.md").write_text(body, encoding="utf-8")

    def test_provisions_roles_into_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                self._seed_template(root, "zh", "系统架构师",
                                    {"design-architecture": "---\nname: design-architecture\n---\nb"})
                created = provision_group_roles(7, lang="zh")
                self.assertEqual(created, ["系统架构师"])
                rdir = layout.group_roles_dir(7) / "系统架构师"
                self.assertTrue((rdir / "role.yaml").exists())
                self.assertTrue((rdir / "skills" / "design-architecture.md").exists())

    def test_idempotent_second_call_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                self._seed_template(root, "zh", "PM", {"write-spec": "---\nname: write-spec\n---\nb"})
                self.assertEqual(provision_group_roles(7, lang="zh"), ["PM"])
                self.assertEqual(provision_group_roles(7, lang="zh"), [])  # already there

    def test_no_templates_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("skills.constants.WORKSPACE_ROOT", Path(tmp)):
                self.assertEqual(provision_group_roles(7, lang="en"), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_role_provision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'workspace.role_provision'`

- [ ] **Step 3: Create role_provision.py**

```python
# backend/workspace/role_provision.py
"""建群拷贝：把全局角色模板按群语言拷进 group_<id>/roles/。

唯一拷贝原语是 SkillStore.copy(TemplateScope → RoleScope)；role.yaml 作为元数据
单独文件拷贝。幂等：群里已存在该角色目录则整体跳过（不覆盖群内自治内容）。
System 池（L1）不在此拷贝，是跨群共享引用。"""
from __future__ import annotations
import shutil

from workspace import layout
from skills.store import SkillStore
from skills.scope import TemplateScope, RoleScope


def provision_group_roles(group_id: int, lang: str | None = None) -> list[str]:
    """把 templates/<lang>/roles/* 拷进 group_<id>/roles/*。返回本次新建的角色名。"""
    if lang is None:
        lang = layout.get_group_language(group_id)
    templates_root = layout.templates_roles_dir(lang)
    if not templates_root.exists():
        return []

    store = SkillStore()
    provisioned: list[str] = []
    for tdir in sorted(templates_root.iterdir()):
        if not tdir.is_dir():
            continue
        role = tdir.name
        dst_dir = layout.group_roles_dir(group_id) / role
        if dst_dir.exists():
            continue  # 幂等：该角色已建过，跳过
        dst_dir.mkdir(parents=True, exist_ok=True)

        src_meta = tdir / "role.yaml"
        if src_meta.exists():
            shutil.copy2(src_meta, dst_dir / "role.yaml")

        src_scope = TemplateScope(lang, role)
        dst_scope = RoleScope(group_id, role)
        for entry in store.list(src_scope):
            store.copy(src_scope, entry["name"], dst_scope)
        provisioned.append(role)
    return provisioned
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_role_provision.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/workspace/role_provision.py backend/tests/test_role_provision.py
git commit -m "feat(workspace): provision_group_roles — copy templates into group roles (idempotent)"
```

---

### Task 3: 把建群拷贝挂进 `init_group_workspace`

**Files:**
- Modify: `backend/workspace/__init__.py`（`init_group_workspace` 末尾，约 `:695-754`）
- Test: `backend/tests/test_role_provision.py`（追加一条钩子测试到既有文件）

**Interfaces:**
- Consumes: `workspace.role_provision.provision_group_roles`。
- Produces: 无新符号；`init_group_workspace(group_id, group_name="")` 末尾副作用新增「按群语言拷模板角色进群」。

- [ ] **Step 1: Write the failing test（追加到 `tests/test_role_provision.py` 的 `TestProvision` 类）**

```python
    def test_init_group_workspace_provisions_roles(self):
        import asyncio
        from workspace import init_group_workspace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                self._seed_template(root, "zh", "代码助手", {"code-review": "---\nname: code-review\n---\nb"})
                asyncio.run(init_group_workspace(5, "Proj"))
                self.assertTrue(
                    (layout.group_roles_dir(5) / "代码助手" / "skills" / "code-review.md").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_role_provision.py::TestProvision::test_init_group_workspace_provisions_roles -v`
Expected: FAIL — 群角色目录未被建（`init_group_workspace` 还没挂拷贝）。

- [ ] **Step 3: 在 `init_group_workspace` 末尾加调用**

在 `backend/workspace/__init__.py` 的 `init_group_workspace` 函数体**最后**（四个协调件 `for` 循环之后、函数返回前）追加：

```python
    # 建群拷贝：按群语言把全局角色模板拷进 group_<id>/roles/（幂等，System 池不拷）。
    # 延迟 import 避免 workspace 包 import 期与 skills.store 形成环。
    from workspace.role_provision import provision_group_roles
    provision_group_roles(group_id)
```

（`provision_group_roles` 是同步文件 IO、量小，建群属低频路径，直接调用即可，不必 `to_thread`。）

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_role_provision.py -v`
Expected: PASS（4 passed，含既有 3 条）

回归既有建群链路测试（确认没破坏）：

Run: `cd backend && venv/bin/python3 -m pytest tests/test_workspace_scaffold.py tests/test_create_group_routing.py -v`
Expected: PASS（这些测试 mock 或在 tmp 工作区下跑；模板根不存在时 `provision_group_roles` 返回 `[]`，无副作用）。

- [ ] **Step 5: Commit**

```bash
git add backend/workspace/__init__.py backend/tests/test_role_provision.py
git commit -m "feat(workspace): provision group roles on group creation (init_group_workspace hook)"
```

---

### Task 4: 迁移脚手架 — catalog 常量 + role.yaml 合成 + main(dry-run)

**Files:**
- Create: `backend/scripts/migrate_role_skills.py`
- Test: `backend/tests/test_migrate_role_skills.py`

**Interfaces:**
- Consumes: `skills.constants.WORKSPACE_ROOT`；`skills.role_meta.write_role_meta`；`db.connect_sync`（仅 `main`/loader 用）。
- Produces（本任务先落常量、helper、空壳 main，后续任务填各 step）：
  - 模块常量 `DISCARD: set[str]`、`KEPT_ZH_ROLES` 不显式列（由磁盘减 DISCARD 得出）、`NEW_ROLES: dict[str, list[tuple[str,str]]]`、`EN_DISPLAY: dict[str,str]`、`NEW_ROLE_META: dict[str, dict]`。
  - `synth_role_yaml(dst_dir, role, db_meta, *, display_name=None)` — 用 `write_role_meta` 落 role.yaml；`db_meta` 是 `{system_prompt, avatar_color}` 或 None。
  - `main(argv=None) -> int` — 解析 `--apply`，打印模式与备份提醒，dry-run 返回 0、不动盘（各 step 在 Task 9 接入）。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_migrate_role_skills.py
import io, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import scripts.migrate_role_skills as M


class TestScaffold(unittest.TestCase):
    def test_catalog_constants(self):
        self.assertEqual(M.DISCARD, {"developer", "qa", "pm"})
        self.assertIn("Architecture", M.NEW_ROLES)
        self.assertIn("PM", M.NEW_ROLES)
        # Architecture sources both its skills from 系统架构师
        self.assertEqual(M.NEW_ROLES["Architecture"],
                         [("系统架构师", "design-architecture"), ("系统架构师", "tech-stack-review")])
        # PM update-board comes from the (to-be-discarded) pm dir
        self.assertIn(("pm", "update-board"), M.NEW_ROLES["PM"])
        self.assertEqual(M.EN_DISPLAY["系统架构师"], "System Architect")

    def test_synth_role_yaml_uses_db_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "系统架构师"
            M.synth_role_yaml(d, "系统架构师",
                              {"system_prompt": "你是架构师", "avatar_color": "#8b5cf6"})
            from skills.role_meta import read_role_meta
            meta = read_role_meta(d)
            self.assertEqual(meta["display_name"], "系统架构师")
            self.assertEqual(meta["avatar_color"], "#8b5cf6")
            self.assertEqual(meta["system_prompt"], "你是架构师")

    def test_synth_role_yaml_minimal_when_no_db_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "Architecture"
            M.synth_role_yaml(d, "Architecture", None)
            from skills.role_meta import read_role_meta
            meta = read_role_meta(d)
            self.assertEqual(meta["display_name"], "Architecture")
            self.assertIsNone(meta["avatar_color"])

    def test_main_dryrun_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = M.main([])           # no --apply
                self.assertEqual(rc, 0)
                self.assertEqual(list(root.iterdir()), [])  # nothing written
                self.assertIn("DRY-RUN", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.migrate_role_skills'`

- [ ] **Step 3: Create migrate_role_skills.py（脚手架版）**

```python
# backend/scripts/migrate_role_skills.py
"""一次性迁移：把老 workspaces/roles/ + role_templates 表迁成全局角色模板，
并对齐现有群与 bot。沿用 migrate_workspace_layout 的纪律。

安全约定：
- **跑前停机 + 备份** workspaces/ 与中央 DB（脚本不替你备份）。
- 默认 dry-run：只打印计划，不动盘。加 --apply 才执行。
- 幂等：已建模板 / 已灌群 / 已改名 → 跳过，可重复运行。

步骤：
  A. 建全局 zh 模板  templates/zh/roles/<role>/{role.yaml, skills/}
  A2. 建全局 en 模板骨架（role.yaml + 占位 skills，正文后续补）
  B. 给现有群灌角色（按群语言拷模板，复用 provision_group_roles）
  C. 对齐现有 bot 的自由文本 role（不命中→在该群自动建同名空角色）
  D. 退役老全局目录  roles/ → roles.legacy/

用法：
    python3 -m scripts.migrate_role_skills            # dry-run
    python3 -m scripts.migrate_role_skills --apply    # 执行
"""
from __future__ import annotations
import sys
from pathlib import Path

from skills.role_meta import write_role_meta

# 丢弃的老英文残留目录（与中文角色重复，role_templates 无对应行）
DISCARD = {"developer", "qa", "pm"}

# 新增角色：role -> [(源角色目录名, 技能名)]，技能正文从既有 .md 取。
NEW_ROLES: dict[str, list[tuple[str, str]]] = {
    "Architecture": [("系统架构师", "design-architecture"), ("系统架构师", "tech-stack-review")],
    "PM": [("需求分析师", "write-spec"), ("pm", "update-board"), ("需求分析师", "write-user-story")],
}

# 12 角色的英文 display_name（en 模板套用）。键为磁盘角色目录名。
EN_DISPLAY: dict[str, str] = {
    "代码助手": "Code Assistant",
    "后端Python专家": "Backend Python Expert",
    "后端Java工程师": "Backend Java Engineer",
    "前端工程师": "Frontend Engineer",
    "系统架构师": "System Architect",
    "需求分析师": "Requirements Analyst",
    "QA测试工程师": "QA Engineer",
    "DevOps工程师": "DevOps Engineer",
    "写作助手": "Writing Assistant",
    "翻译专家": "Translation Expert",
    "Architecture": "Architecture",
    "PM": "PM",
}

# 新角色无 role_templates 行，给个最小元数据（avatar 复用近义角色色）。
NEW_ROLE_META: dict[str, dict] = {
    "Architecture": {"avatar_color": "#8b5cf6"},
    "PM": {"avatar_color": "#0ea5e9"},
}


def synth_role_yaml(dst_dir: Path, role: str, db_meta: dict | None, *,
                    display_name: str | None = None) -> None:
    """落 dst_dir/role.yaml。display_name 缺省取 role；db_meta 提供 system_prompt/avatar。"""
    meta = {
        "display_name": display_name or role,
        "avatar_color": (db_meta or {}).get("avatar_color")
                        or NEW_ROLE_META.get(role, {}).get("avatar_color"),
        "system_prompt": (db_meta or {}).get("system_prompt"),
    }
    write_role_meta(dst_dir, meta)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv

    from skills.constants import WORKSPACE_ROOT
    root = Path(WORKSPACE_ROOT)

    print(f"[迁移] 工作区根: {root}")
    print(f"[迁移] 模式: {'APPLY（写盘）' if apply else 'DRY-RUN（只打印）'}")
    if apply:
        print("[迁移] 确认：已停机且已备份 workspaces/ 与中央 DB ？(Ctrl-C 取消)")

    # 各 step 在 Task 5-9 接入；本脚手架版 dry-run 不动盘。
    if not apply:
        print("\n[迁移] dry-run 完成。确认无误后加 --apply 执行。")
        return 0
    print("\n[迁移] （步骤尚未接入，见后续任务）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_role_skills.py backend/tests/test_migrate_role_skills.py
git commit -m "feat(migrate): role-skills migration scaffold (catalog, role.yaml synth, dry-run main)"
```

---

### Task 5: 迁移 step A — 建 zh 模板

**Files:**
- Modify: `backend/scripts/migrate_role_skills.py`（加 `build_zh_templates`）
- Test: `backend/tests/test_migrate_role_skills.py`（加 `TestStepA`）

**Interfaces:**
- Consumes: `synth_role_yaml`、`DISCARD`、`NEW_ROLES`、`shutil`。
- Produces: `build_zh_templates(root: Path, role_db_meta: dict[str, dict], *, dry_run: bool) -> dict` — 从 `root/roles/<role>/skills/*.md` 建 `root/templates/zh/roles/<role>/`（10 个保留角色，跳过 `DISCARD`）+ `NEW_ROLES`（Architecture/PM，技能从既有 .md 取）。`role_db_meta`：`role 目录名 -> {system_prompt, avatar_color}`（来自 role_templates）。幂等（目标 skills 文件已存在则覆盖拷贝，role.yaml 重写——重复运行结果一致）。返回 `{"built": [...], "dry_run": bool}`。

- [ ] **Step 1: Write the failing test（追加 `TestStepA` 到 `tests/test_migrate_role_skills.py`）**

```python
class TestStepA(unittest.TestCase):
    def _legacy(self, root: Path):
        # 造两个保留角色 + 一个 discard + PM 的源
        for role, skills in {
            "系统架构师": ["design-architecture", "tech-stack-review"],
            "需求分析师": ["write-spec", "write-user-story"],
            "pm": ["update-board", "write-spec"],         # discard 目录，但 PM 借其 update-board
        }.items():
            sd = root / "roles" / role / "skills"
            sd.mkdir(parents=True)
            for s in skills:
                (sd / f"{s}.md").write_text(f"# {s}\n", encoding="utf-8")

    def test_build_zh_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(root)
            db_meta = {"系统架构师": {"system_prompt": "你是架构师", "avatar_color": "#8b5cf6"}}
            rep = M.build_zh_templates(root, db_meta, dry_run=False)
            zh = root / "templates" / "zh" / "roles"
            # 保留角色拷过来了
            self.assertTrue((zh / "系统架构师" / "skills" / "design-architecture.md").exists())
            from skills.role_meta import read_role_meta
            self.assertEqual(read_role_meta(zh / "系统架构师")["system_prompt"], "你是架构师")
            # discard 目录没建成模板
            self.assertFalse((zh / "pm").exists())
            # 新角色 Architecture（源自系统架构师）+ PM（update-board 源自 pm）
            self.assertTrue((zh / "Architecture" / "skills" / "tech-stack-review.md").exists())
            self.assertTrue((zh / "PM" / "skills" / "update-board.md").exists())
            self.assertTrue((zh / "PM" / "skills" / "write-spec.md").exists())
            self.assertIn("Architecture", rep["built"])

    def test_build_zh_templates_dryrun_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(root)
            M.build_zh_templates(root, {}, dry_run=True)
            self.assertFalse((root / "templates").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py::TestStepA -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'build_zh_templates'`

- [ ] **Step 3: 在 migrate_role_skills.py 加 `build_zh_templates`（含 `shutil` import）**

在文件顶部 import 区把 `import sys` 一行改为：

```python
import shutil
import sys
```

在 `synth_role_yaml` 之后加：

```python
def _copy_skill_md(src_md: Path, dst_skills: Path, *, dry_run: bool) -> None:
    if dry_run or not src_md.exists():
        return
    dst_skills.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_md, dst_skills / src_md.name)


def build_zh_templates(root: Path, role_db_meta: dict[str, dict], *,
                       dry_run: bool) -> dict:
    """Step A：从 root/roles/* 建 templates/zh/roles/*（保留 10 角色 + Architecture/PM）。"""
    roles_src = root / "roles"
    zh_root = root / "templates" / "zh" / "roles"
    built: list[str] = []

    # 10 个保留的中文角色：磁盘有、且不在 DISCARD
    if roles_src.exists():
        for src in sorted(roles_src.iterdir()):
            if not src.is_dir() or src.name in DISCARD:
                continue
            role = src.name
            dst = zh_root / role
            for md in sorted((src / "skills").glob("*.md")) if (src / "skills").exists() else []:
                _copy_skill_md(md, dst / "skills", dry_run=dry_run)
            if not dry_run:
                synth_role_yaml(dst, role, role_db_meta.get(role))
            built.append(role)

    # 新角色：技能正文从既有角色 .md 取
    for role, sources in NEW_ROLES.items():
        dst = zh_root / role
        for src_role, skill in sources:
            _copy_skill_md(roles_src / src_role / "skills" / f"{skill}.md",
                           dst / "skills", dry_run=dry_run)
        if not dry_run:
            synth_role_yaml(dst, role, role_db_meta.get(role))
        built.append(role)

    return {"built": built, "dry_run": dry_run}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py -v`
Expected: PASS（全部，含 `TestStepA` 2 条）

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_role_skills.py backend/tests/test_migrate_role_skills.py
git commit -m "feat(migrate): step A — build zh role templates from legacy roles + Architecture/PM"
```

---

### Task 6: 迁移 step A2 — 建 en 模板骨架

**Files:**
- Modify: `backend/scripts/migrate_role_skills.py`（加 `build_en_skeletons`）
- Test: `backend/tests/test_migrate_role_skills.py`（加 `TestStepA2`）

**Interfaces:**
- Consumes: `build_zh_templates` 的产物（`templates/zh/roles/*` 已建）、`EN_DISPLAY`、`synth_role_yaml`。
- Produces: `build_en_skeletons(root: Path, *, dry_run: bool) -> dict` — 对每个 `templates/zh/roles/<role>/`，建 `templates/en/roles/<role>/`：`role.yaml`（`display_name=EN_DISPLAY[role]`，沿用 zh 的 avatar/system_prompt 占位）+ 每个 zh skill 对应一个 **占位** `skills/<name>.md`（含 frontmatter + `TODO: English body`）。幂等。返回 `{"built":[...], "dry_run":bool}`。

- [ ] **Step 1: Write the failing test（追加 `TestStepA2`）**

```python
class TestStepA2(unittest.TestCase):
    def test_build_en_skeletons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 先有一个 zh 模板角色
            zsk = root / "templates" / "zh" / "roles" / "系统架构师" / "skills"
            zsk.mkdir(parents=True)
            (zsk / "design-architecture.md").write_text(
                "---\nname: design-architecture\ndescription: x\n---\n中文正文", encoding="utf-8")
            (root / "templates" / "zh" / "roles" / "系统架构师" / "role.yaml").write_text(
                "display_name: 系统架构师\navatar_color: '#8b5cf6'\n", encoding="utf-8")

            M.build_en_skeletons(root, dry_run=False)
            en = root / "templates" / "en" / "roles" / "系统架构师"
            from skills.role_meta import read_role_meta
            self.assertEqual(read_role_meta(en)["display_name"], "System Architect")
            body = (en / "skills" / "design-architecture.md").read_text(encoding="utf-8")
            self.assertIn("name: design-architecture", body)   # frontmatter preserved
            self.assertIn("TODO", body)                        # placeholder body
            self.assertNotIn("中文正文", body)                  # zh body NOT carried over

    def test_dryrun_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "templates" / "zh" / "roles" / "PM" / "skills").mkdir(parents=True)
            M.build_en_skeletons(root, dry_run=True)
            self.assertFalse((root / "templates" / "en").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py::TestStepA2 -v`
Expected: FAIL — `AttributeError: ... has no attribute 'build_en_skeletons'`

- [ ] **Step 3: 加 `build_en_skeletons` + frontmatter 提取 helper**

在 `build_zh_templates` 之后加：

```python
def _frontmatter_only(md_text: str, skill_name: str) -> str:
    """取 zh 技能的 YAML frontmatter，正文换成英文占位（TODO）。

    没有 frontmatter（首行非 '---'）时，合成最小 frontmatter。"""
    lines = md_text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            fm = "\n".join(lines[: end + 1])
            return f"{fm}\n\nTODO: English skill body for '{skill_name}'.\n"
    return f"---\nname: {skill_name}\ndescription: TODO\n---\n\nTODO: English skill body for '{skill_name}'.\n"


def build_en_skeletons(root: Path, *, dry_run: bool) -> dict:
    """Step A2：按 zh 模板套建 en 骨架（en display_name + 占位技能正文）。"""
    zh_root = root / "templates" / "zh" / "roles"
    en_root = root / "templates" / "en" / "roles"
    built: list[str] = []
    if not zh_root.exists():
        return {"built": built, "dry_run": dry_run}

    from skills.role_meta import read_role_meta

    for zdir in sorted(zh_root.iterdir()):
        if not zdir.is_dir():
            continue
        role = zdir.name
        edir = en_root / role
        if not dry_run:
            (edir / "skills").mkdir(parents=True, exist_ok=True)
            zmeta = read_role_meta(zdir) or {}
            synth_role_yaml(
                edir, role,
                {"system_prompt": zmeta.get("system_prompt"),
                 "avatar_color": zmeta.get("avatar_color")},
                display_name=EN_DISPLAY.get(role, role),
            )
            for md in sorted((zdir / "skills").glob("*.md")) if (zdir / "skills").exists() else []:
                (edir / "skills" / md.name).write_text(
                    _frontmatter_only(md.read_text(encoding="utf-8"), md.stem),
                    encoding="utf-8")
        built.append(role)
    return {"built": built, "dry_run": dry_run}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py -v`
Expected: PASS（含 `TestStepA2` 2 条）

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_role_skills.py backend/tests/test_migrate_role_skills.py
git commit -m "feat(migrate): step A2 — build en template skeletons (en display + placeholder bodies)"
```

---

### Task 7: 迁移 step B — 给现有群灌角色

**Files:**
- Modify: `backend/scripts/migrate_role_skills.py`（加 `seed_existing_groups`）
- Test: `backend/tests/test_migrate_role_skills.py`（加 `TestStepB`）

**Interfaces:**
- Consumes: `workspace.role_provision.provision_group_roles`（复用建群拷贝原语）、`workspace.layout`。
- Produces: `seed_existing_groups(root: Path, group_ids: list[int], *, dry_run: bool) -> dict` — 对每个 group_id 按其语言 `provision_group_roles`（幂等）。`dry_run` 时只规划不调用。返回 `{"seeded": {gid: [roles...]}, "dry_run": bool}`。`group_ids` 由 `main` 从 DB / 磁盘 group_* 目录得出（loader 在 Task 9）。

- [ ] **Step 1: Write the failing test（追加 `TestStepB`）**

```python
class TestStepB(unittest.TestCase):
    def test_seed_existing_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                # zh 模板里有一个角色
                tsk = root / "templates" / "zh" / "roles" / "代码助手" / "skills"
                tsk.mkdir(parents=True)
                (tsk / "code-review.md").write_text("---\nname: code-review\n---\nb", encoding="utf-8")
                rep = M.seed_existing_groups(root, [3, 4], dry_run=False)
                from workspace import layout
                self.assertTrue((layout.group_roles_dir(3) / "代码助手" / "skills" / "code-review.md").exists())
                self.assertTrue((layout.group_roles_dir(4) / "代码助手").exists())
                self.assertEqual(rep["seeded"][3], ["代码助手"])

    def test_dryrun_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                (root / "templates" / "zh" / "roles" / "PM" / "skills").mkdir(parents=True)
                M.seed_existing_groups(root, [3], dry_run=True)
                self.assertFalse((root / "group_3").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py::TestStepB -v`
Expected: FAIL — `AttributeError: ... has no attribute 'seed_existing_groups'`

- [ ] **Step 3: 加 `seed_existing_groups`**

在 `build_en_skeletons` 之后加：

```python
def seed_existing_groups(root: Path, group_ids: list[int], *, dry_run: bool) -> dict:
    """Step B：给现有群按语言灌角色（复用 provision_group_roles，幂等）。"""
    seeded: dict[int, list[str]] = {}
    if not dry_run:
        from workspace.role_provision import provision_group_roles
        for gid in group_ids:
            seeded[gid] = provision_group_roles(gid)
    else:
        for gid in group_ids:
            seeded[gid] = []  # dry-run：只列出待灌的群，不动盘
    return {"seeded": seeded, "dry_run": dry_run}
```

> 注：`root` 参数仅用于与其它 step 的签名一致 / 文档化；路径解析全部经 `layout`（实时读 `WORKSPACE_ROOT`）。`provision_group_roles` 自身已幂等，重复运行安全。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py -v`
Expected: PASS（含 `TestStepB` 2 条）

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_role_skills.py backend/tests/test_migrate_role_skills.py
git commit -m "feat(migrate): step B — seed existing groups with role templates (reuses provision)"
```

---

### Task 8: 迁移 step C — 对齐 bot role（不命中建空角色）

**Files:**
- Modify: `backend/scripts/migrate_role_skills.py`（加 `plan_bot_roles` + `align_bot_roles`）
- Test: `backend/tests/test_migrate_role_skills.py`（加 `TestStepC`）

**Interfaces:**
- Consumes: `workspace.layout.group_roles_dir`、`synth_role_yaml`。
- Produces:
  - `plan_bot_roles(root: Path, bots: list[tuple[int,int,str]]) -> dict` — 纯函数（不动盘）。`bots`：`(bot_id, group_id, role)`。返回 `{"ok": [...命中...], "create": [(gid, role)...去重的待建空角色...]}`。命中 = `group_<gid>/roles/<role>/` 已存在。
  - `align_bot_roles(root: Path, bots, *, dry_run: bool) -> dict` — 对 `create` 项在该群建**空角色**（`skills/` 空目录 + `role.yaml` 最小，`display_name=role`）。**非破坏性**：不动 bot 行、不删任何东西。返回报告。

- [ ] **Step 1: Write the failing test（追加 `TestStepC`）**

```python
class TestStepC(unittest.TestCase):
    def test_plan_hits_and_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                from workspace import layout
                (layout.group_roles_dir(3) / "需求分析师").mkdir(parents=True)
                bots = [(101, 3, "需求分析师"), (102, 3, "CEO"), (103, 4, "Tester")]
                plan = M.plan_bot_roles(root, bots)
                self.assertIn((101, 3, "需求分析师"), plan["ok"])
                self.assertIn((3, "CEO"), plan["create"])
                self.assertIn((4, "Tester"), plan["create"])

    def test_align_creates_empty_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                from workspace import layout
                bots = [(102, 3, "CEO")]
                M.align_bot_roles(root, bots, dry_run=False)
                cdir = layout.group_roles_dir(3) / "CEO"
                self.assertTrue((cdir / "skills").is_dir())
                self.assertEqual(list((cdir / "skills").iterdir()), [])   # empty skills
                from skills.role_meta import read_role_meta
                self.assertEqual(read_role_meta(cdir)["display_name"], "CEO")

    def test_align_dryrun_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                from workspace import layout
                M.align_bot_roles(root, [(102, 3, "CEO")], dry_run=True)
                self.assertFalse((layout.group_roles_dir(3) / "CEO").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py::TestStepC -v`
Expected: FAIL — `AttributeError: ... has no attribute 'plan_bot_roles'`

- [ ] **Step 3: 加 `plan_bot_roles` + `align_bot_roles`**

在 `seed_existing_groups` 之后加：

```python
def plan_bot_roles(root: Path, bots: list[tuple[int, int, str]]) -> dict:
    """Step C 规划（纯函数）：命中群角色的 bot 不动；不命中的去重收集为待建空角色。"""
    from workspace import layout
    ok: list[tuple[int, int, str]] = []
    create: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for bot_id, gid, role in bots:
        if (layout.group_roles_dir(gid) / role).exists():
            ok.append((bot_id, gid, role))
        elif (gid, role) not in seen:
            seen.add((gid, role))
            create.append((gid, role))
    return {"ok": ok, "create": create}


def align_bot_roles(root: Path, bots: list[tuple[int, int, str]], *,
                    dry_run: bool) -> dict:
    """Step C 执行：为不命中的 (gid, role) 在该群建空角色（非破坏性）。"""
    from workspace import layout
    plan = plan_bot_roles(root, bots)
    if not dry_run:
        for gid, role in plan["create"]:
            cdir = layout.group_roles_dir(gid) / role
            (cdir / "skills").mkdir(parents=True, exist_ok=True)
            synth_role_yaml(cdir, role, None)
    return {**plan, "dry_run": dry_run}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py -v`
Expected: PASS（含 `TestStepC` 3 条）

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_role_skills.py backend/tests/test_migrate_role_skills.py
git commit -m "feat(migrate): step C — align bot roles, auto-create empty role on miss (non-destructive)"
```

---

### Task 9: 迁移 step D + 串接 main + verify + 端到端

**Files:**
- Modify: `backend/scripts/migrate_role_skills.py`（加 `retire_legacy_roles`、DB loader、串 `main`、`verify`）
- Test: `backend/tests/test_migrate_role_skills.py`（加 `TestStepD`、`TestEndToEnd`）

**Interfaces:**
- Consumes: 前述各 step；`db.connect_sync`（仅 `main` 的 loader 用）。
- Produces:
  - `retire_legacy_roles(root: Path, *, dry_run: bool) -> dict` — 把 `root/roles` 改名 `root/roles.legacy`。幂等（`roles` 不存在或 `roles.legacy` 已存在 → 跳过）。返回 `{"renamed": bool, "dry_run": bool}`。
  - `_load_groups_from_db() -> list[int]` / `_load_bots_from_db() -> list[tuple[int,int,str]]`。
  - `verify(root: Path) -> tuple[bool, list[str]]` — zh 模板存在、`roles.legacy` 已就位且 `roles` 不在。
  - `main(argv)` apply 路径：读 DB role_templates → `role_db_meta`，A → A2 → B（DB groups）→ C（DB bots）→ D，打印报告，`verify`。

- [ ] **Step 1: Write the failing test（追加 `TestStepD` + `TestEndToEnd`）**

```python
class TestStepD(unittest.TestCase):
    def test_rename_roles_to_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "roles" / "代码助手").mkdir(parents=True)
            rep = M.retire_legacy_roles(root, dry_run=False)
            self.assertTrue(rep["renamed"])
            self.assertFalse((root / "roles").exists())
            self.assertTrue((root / "roles.legacy" / "代码助手").exists())

    def test_idempotent_when_already_retired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "roles.legacy").mkdir(parents=True)
            rep = M.retire_legacy_roles(root, dry_run=False)
            self.assertFalse(rep["renamed"])      # nothing to do

    def test_dryrun_no_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "roles" / "x").mkdir(parents=True)
            M.retire_legacy_roles(root, dry_run=True)
            self.assertTrue((root / "roles").exists())
            self.assertFalse((root / "roles.legacy").exists())


class TestEndToEnd(unittest.TestCase):
    def test_apply_pipeline_no_db(self):
        # 直接驱动各 step（绕过 DB loader），验证 A→A2→B→C→D 串起来自洽
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("skills.constants.WORKSPACE_ROOT", root):
                sd = root / "roles" / "系统架构师" / "skills"
                sd.mkdir(parents=True)
                for s in ("design-architecture", "tech-stack-review"):
                    (sd / f"{s}.md").write_text(f"---\nname: {s}\n---\nb", encoding="utf-8")

                M.build_zh_templates(root, {}, dry_run=False)
                M.build_en_skeletons(root, dry_run=False)
                M.seed_existing_groups(root, [3], dry_run=False)
                M.align_bot_roles(root, [(9, 3, "CEO")], dry_run=False)
                M.retire_legacy_roles(root, dry_run=False)

                from workspace import layout
                # 群 3 拿到了系统架构师角色
                self.assertTrue((layout.group_roles_dir(3) / "系统架构师" / "skills" / "design-architecture.md").exists())
                # CEO 空角色建好
                self.assertTrue((layout.group_roles_dir(3) / "CEO" / "skills").is_dir())
                # 老目录退役
                self.assertFalse((root / "roles").exists())
                self.assertTrue((root / "roles.legacy").exists())
                ok, problems = M.verify(root)
                self.assertTrue(ok, problems)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py::TestStepD tests/test_migrate_role_skills.py::TestEndToEnd -v`
Expected: FAIL — `AttributeError: ... has no attribute 'retire_legacy_roles'`

- [ ] **Step 3: 加 step D、loaders、verify，并把 main 串起来**

在 `align_bot_roles` 之后加：

```python
def retire_legacy_roles(root: Path, *, dry_run: bool) -> dict:
    """Step D：roles/ → roles.legacy/（幂等）。"""
    src = root / "roles"
    dst = root / "roles.legacy"
    if dst.exists() or not src.exists():
        return {"renamed": False, "dry_run": dry_run}
    if not dry_run:
        src.rename(dst)
    return {"renamed": True, "dry_run": dry_run}


def verify(root: Path) -> tuple[bool, list[str]]:
    """收尾校验：zh 模板已建、老 roles/ 已退役。"""
    problems: list[str] = []
    if not (root / "templates" / "zh" / "roles").exists():
        problems.append("缺 templates/zh/roles")
    if (root / "roles").exists():
        problems.append("老 roles/ 未退役")
    if not (root / "roles.legacy").exists():
        problems.append("缺 roles.legacy/（未改名）")
    return (len(problems) == 0), problems


def _load_role_db_meta() -> dict[str, dict]:
    """中央 DB role_templates → {role 列: {system_prompt, avatar_color}}。"""
    from db import connect_sync
    out: dict[str, dict] = {}
    with connect_sync() as conn:
        for role, sp, color in conn.execute(
                "SELECT role, system_prompt, avatar_color FROM role_templates").fetchall():
            out[role] = {"system_prompt": sp, "avatar_color": color}
    return out


def _load_groups_from_db() -> list[int]:
    from db import connect_sync
    with connect_sync() as conn:
        return [int(r[0]) for r in conn.execute("SELECT id FROM groups").fetchall()]


def _load_bots_from_db() -> list[tuple[int, int, str]]:
    from db import connect_sync
    with connect_sync() as conn:
        rows = conn.execute(
            "SELECT id, group_id, COALESCE(role, '') FROM members WHERE type = 'bot'").fetchall()
    return [(int(r[0]), int(r[1]), r[2]) for r in rows if r[2]]
```

把 `main` 的 apply 分支（`print("\n[迁移] （步骤尚未接入…")` 那段）替换为：

```python
    role_db_meta = _load_role_db_meta()
    groups = _load_groups_from_db()
    bots = _load_bots_from_db()

    a = build_zh_templates(root, role_db_meta, dry_run=False)
    a2 = build_en_skeletons(root, dry_run=False)
    b = seed_existing_groups(root, groups, dry_run=False)
    c = align_bot_roles(root, bots, dry_run=False)
    d = retire_legacy_roles(root, dry_run=False)

    print(f"  A 建 zh 模板: {a['built']}")
    print(f"  A2 建 en 骨架: {a2['built']}")
    print(f"  B 灌群: {list(b['seeded'].keys())}")
    print(f"  C 对齐 bot：命中 {len(c['ok'])}，新建空角色 {c['create']}")
    print(f"  D 退役 roles/: {'已改名' if d['renamed'] else '跳过'}")

    ok, problems = verify(root)
    print(f"\n[校验] {'通过 ✓' if ok else '发现问题 ✗'}")
    for p in problems:
        print(f"    - {p}")
    return 0 if ok else 1
```

> dry-run 分支保持不变（Task 4 已写，打印计划并 `return 0`，不动盘）。

- [ ] **Step 4: Run test to verify it passes**

Run（先单跑新增，再跑该文件全量）:
`cd backend && venv/bin/python3 -m pytest tests/test_migrate_role_skills.py -v`
Expected: PASS（全部，含 `TestStepD` 3 条 + `TestEndToEnd` 1 条）

相关回归（确认建群拷贝链没被牵动）:
`cd backend && venv/bin/python3 -m pytest tests/test_role_meta.py tests/test_role_provision.py tests/test_migrate_role_skills.py tests/test_skill_store.py tests/test_skill_scope.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_role_skills.py backend/tests/test_migrate_role_skills.py
git commit -m "feat(migrate): step D + wire main pipeline (DB loaders, verify, end-to-end)"
```

---

## Self-Review

**1. Spec coverage（Plan 2 范围 = 模板与迁移）：**
- 全局模板 `templates/<lang>/roles/<role>/{role.yaml, skills}` → Task 1（role.yaml 读写）+ Task 5（zh）+ Task 6（en 骨架）✅
- `role.yaml`（display_name/avatar_color/system_prompt，discovery 不读）→ Task 1 ✅
- 建群拷贝（`SkillStore.copy(Template→Role)` + role.yaml，幂等，System 不拷，挂 init_group_workspace）→ Task 2 + Task 3 ✅
- `migrate_role_skills.py`：A 建模板（含丢 developer/qa/pm、加 Architecture/PM）/ A2 en 骨架 / B 灌老群 / C 对齐 bot（不命中建空角色，非破坏）/ D 退役 roles.legacy → Task 4-9 ✅
- 默认 dry-run、`--apply`、停服务+备份提醒 → Task 4（main）+ 各 step `dry_run` 参数 ✅
- 中英文两套、en 正文为独立后续产出（本期只骨架）→ Task 6 ✅，完整 en 正文显式不在本 Plan（见 Global Constraints）。
- **不在本 Plan**（留 Plan 3）：scope 化 `/api/skills`、`/api/groups/{id}/roles`、`/api/templates/roles`、`add_member` 校验、`MemberList`/`SkillPanel`/`TemplateManager`、i18n key、建 bot 时 system_prompt 快照。`role_templates` 表 DROP 也留下个迁移。

**2. Placeholder scan：** 各 step 均含完整代码与完整测试代码；无 TBD/TODO-in-plan（脚本里 en 骨架正文的字面 `TODO:` 是**产物内容**，非计划占位）。

**3. Type consistency：** `provision_group_roles(group_id, lang=None)->list[str]` 在 Task 2 定义、Task 3/7 复用一致；`synth_role_yaml(dst_dir, role, db_meta, *, display_name=None)` 在 Task 4 定义、Task 5/6/8 复用一致；各 step 统一 `(root, …, *, dry_run)->dict`；`read_role_meta/write_role_meta` 字段集 `(display_name, avatar_color, system_prompt)` 全程一致；`SkillStore.copy(src_scope, name, dst_scope)`、`TemplateScope(lang, role)`、`RoleScope(gid, role)` 与 Plan 1 已落地签名一致。

---

## 后续 Plan（不在本文件）

- **Plan 3 — API / UI / 双语**：`/api/skills?scope=<descriptor>` 统一 CRUD + `/api/skills/copy`；`/api/groups/{id}/roles` 与 `/api/templates/roles` 元数据端点；`add_member` 校验 role ∈ 群角色（422，按群语言文案）；建 bot 时从 role.yaml 拷 system_prompt 快照到 `members.system_prompt`；`MemberList` 下拉、`SkillPanel` 群内整理台、`TemplateManager` 升级；全部新文案挂 i18n key（zh/en）+ 一致性单测。
- **英文技能正文**：把 Task 6 产出的 en 骨架 `TODO` 正文补成真正的英文 SKILL 正文（可借翻译角色或人工，分批）。
- **下个迁移**：`role_templates` 表 DROP；`workspaces/roles.legacy/` 删除。
