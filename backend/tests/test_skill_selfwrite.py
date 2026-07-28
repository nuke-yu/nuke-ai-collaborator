"""
tests/test_skill_selfwrite.py — Bot 自写 Skill 审批闭环端对端测试

覆盖完整流程：
  1. 创建 Bot（tool_loop_v1）
  2. Bot 通过 write_file API 写入 skills/learned/draft/<name>.md
  3. GET /api/members/{id}/skills 返回 status=draft 的条目
  4. POST .../approve → 文件移到 learned/active/，GET skills 返回 active
  5. 再写一个草稿 → POST .../reject → 文件删除，GET skills 不含该条目
  6. write_file 写 learned/active/ 路径被重定向到 draft（防绕过测试）
  7. 写入非法路径被拦截
"""
import os
import sys
import shutil
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
import db.writer as _writer_mod
import workspace as _workspace_mod
import skills.constants as _skill_const

_HERE = Path(__file__).parent.parent
_TEST_DB_PATH = str(_HERE / "test_chat_skillsw.db")
_TEST_WS_ROOT = _HERE / "test_ws_skillsw"
_GROUP_ID = 1

from main import app
from httpx import AsyncClient


# ─── helpers ─────────────────────────────────────────────────────────────────

_SKILL_FRONTMATTER = """\
---
name: {name}
description: {desc}
layer: learned
status: draft
---
{body}
"""


async def _create_bot(ac: AsyncClient, name: str = "TestBot") -> int:
    r = await ac.post(f"/api/groups/{_GROUP_ID}/members", json={
        "name": name, "type": "bot",
        "executor_id": "tool_loop_v1",
        "system_prompt": "You are a test bot.",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _write_draft(ac: AsyncClient, bot_id: int, skill_name: str,
                       body: str = "技能内容") -> dict:
    content = _SKILL_FRONTMATTER.format(
        name=skill_name, desc="auto-learned skill", body=body
    )
    r = await ac.put(f"/api/members/{bot_id}/workspace/file", json={
        "path": f"skills/learned/draft/{skill_name}.md",
        "content": content,
    })
    return {"status": r.status_code, "body": r.json() if r.status_code < 300 else r.text}


# ═══════════════════════════════════════════════════════════════════════════════

class TestSkillSelfWrite(unittest.IsolatedAsyncioTestCase):
    """端对端测试，每个 test 方法前后保存/恢复全局路径，避免与其他测试文件交叉污染。"""

    async def asyncSetUp(self):
        # Bypass DFT-082 auth so these tests exercise route logic (cleared in tearDown).
        from core import auth as _auth
        app.dependency_overrides[_auth.get_current_user] = lambda: {"uid": 1, "sub": "test"}
        # 保存各模块当前的全局路径（其他测试文件可能已经修改过它们）
        self._orig_db      = database.DB_PATH
        self._orig_writer  = _writer_mod.DB_PATH
        self._orig_ws      = _workspace_mod.WORKSPACE_ROOT
        self._orig_sc_root = _skill_const.WORKSPACE_ROOT
        self._orig_sc_sys  = _skill_const.SYSTEM_SKILLS_ROOT
        self._orig_sc_role = _skill_const.ROLES_ROOT

        # 切换到本文件专用的隔离路径
        database.DB_PATH             = _TEST_DB_PATH
        _writer_mod.DB_PATH          = _TEST_DB_PATH  # add_member writes via write_connect
        _workspace_mod.WORKSPACE_ROOT = _TEST_WS_ROOT
        _skill_const.WORKSPACE_ROOT   = _TEST_WS_ROOT
        _skill_const.SYSTEM_SKILLS_ROOT = _TEST_WS_ROOT / "system" / "skills"
        _skill_const.ROLES_ROOT         = _TEST_WS_ROOT / "roles"

        # 清理残留
        if os.path.exists(_TEST_DB_PATH):
            os.remove(_TEST_DB_PATH)
        if _TEST_WS_ROOT.exists():
            shutil.rmtree(_TEST_WS_ROOT)

        await database.init_db()
        await database.init_central_db(_TEST_DB_PATH)
        async with database.get_db() as db:
            await db.execute(
                """INSERT INTO users
                   (id,username,password_hash) VALUES (1,'test','x')"""
            )
            await db.execute(f"INSERT INTO groups (id, name) VALUES ({_GROUP_ID}, 'TestGroup')")
            await db.execute(
                """INSERT INTO group_memberships(user_id,group_id,role)
                   VALUES (1,?,'owner')""",
                (_GROUP_ID,),
            )
            await db.commit()

    async def asyncTearDown(self):
        from core import auth as _auth
        app.dependency_overrides.pop(_auth.get_current_user, None)
        # 还原全局路径，不干扰后续测试
        database.DB_PATH              = self._orig_db
        _writer_mod.DB_PATH           = self._orig_writer
        _workspace_mod.WORKSPACE_ROOT  = self._orig_ws
        _skill_const.WORKSPACE_ROOT    = self._orig_sc_root
        _skill_const.SYSTEM_SKILLS_ROOT = self._orig_sc_sys
        _skill_const.ROLES_ROOT         = self._orig_sc_role

        if os.path.exists(_TEST_DB_PATH):
            try:
                os.remove(_TEST_DB_PATH)
            except Exception:
                pass
        if _TEST_WS_ROOT.exists():
            try:
                shutil.rmtree(_TEST_WS_ROOT)
            except Exception:
                pass

    # ── 1. 写入草稿，GET skills 返回 draft 状态 ──────────────────────────────

    async def test_write_draft_appears_in_skills_list(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            bot_id = await _create_bot(ac)
            result = await _write_draft(ac, bot_id, "jira-reader")

        self.assertEqual(result["status"], 200)

        draft_path = _TEST_WS_ROOT / f"group_{_GROUP_ID}" / "bots" / f"bot_{bot_id}" / "skills" / "learned" / "draft" / "jira-reader.md"
        self.assertTrue(draft_path.exists(), f"draft 文件不存在: {draft_path}")

        async with AsyncClient(app=app, base_url="http://test") as ac:
            r = await ac.get(f"/api/members/{bot_id}/skills")
        self.assertEqual(r.status_code, 200)
        skills = r.json()["skills"]
        drafts = [s for s in skills if s["status"] == "draft"]
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["name"], "jira-reader")
        self.assertEqual(drafts[0]["layer"], "learned")

    # ── 2. 审批通过：draft → active ───────────────────────────────────────────

    async def test_approve_moves_draft_to_active(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            bot_id = await _create_bot(ac)
            await _write_draft(ac, bot_id, "code-reviewer")
            r = await ac.post(f"/api/members/{bot_id}/skills/learned/code-reviewer/approve")
        self.assertEqual(r.status_code, 200)

        draft_path  = _TEST_WS_ROOT / f"group_{_GROUP_ID}" / "bots" / f"bot_{bot_id}" / "skills" / "learned" / "draft"  / "code-reviewer.md"
        active_path = _TEST_WS_ROOT / f"group_{_GROUP_ID}" / "bots" / f"bot_{bot_id}" / "skills" / "learned" / "active" / "code-reviewer.md"
        self.assertFalse(draft_path.exists(),  "draft 文件应已移走")
        self.assertTrue(active_path.exists(),  "active 文件应存在")

        async with AsyncClient(app=app, base_url="http://test") as ac:
            r = await ac.get(f"/api/members/{bot_id}/skills")
        skill = next((s for s in r.json()["skills"] if s["name"] == "code-reviewer"), None)
        self.assertIsNotNone(skill)
        self.assertEqual(skill["status"], "active")

    async def test_canonical_candidate_requires_group_member_approval(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            bot_id = await _create_bot(ac)
        skill_id = "skill:canonical-review"
        declaration = (
            '{"allowed_tools":[],"limitations":"","procedure":["review"],'
            '"risk_level":"S0","trigger":"schema repair","verification":[]}'
        )
        now = 1_700_000_000_000
        async with database.get_db() as db:
            await db.execute(
                """INSERT INTO skills
                   (skill_id,group_id,bot_id,name,maturity,risk_level,
                    current_version,created_at,updated_at)
                   VALUES (?,?,?,'canonical-review','trial','S0',1,?,?)""",
                (skill_id, _GROUP_ID, bot_id, now, now),
            )
            await db.execute(
                """INSERT INTO skill_versions
                   (skill_id,version,declaration_json,content_hash,
                    evidence_ids,created_at)
                   VALUES (?,1,?,'hash','[]',?)""",
                (skill_id, declaration, now),
            )
            await db.commit()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            candidates = await ac.get(
                f"/api/members/{bot_id}/memory-skills/candidates"
            )
            approved = await ac.post(
                f"/api/members/{bot_id}/memory-skills/{skill_id}/approve",
                json={"reason": "Reviewed execution evidence"},
            )
        self.assertEqual(candidates.status_code, 200)
        self.assertEqual(
            candidates.json()["candidates"][0]["skill_id"], skill_id
        )
        self.assertEqual(approved.status_code, 200)

        async with database.get_db() as db:
            async with db.execute(
                "SELECT maturity FROM skills WHERE skill_id=?",
                (skill_id,),
            ) as cur:
                maturity = (await cur.fetchone())[0]
            async with db.execute(
                """SELECT actor_id,reason FROM skill_promotion_audit
                   WHERE skill_id=?""",
                (skill_id,),
            ) as cur:
                audit = await cur.fetchone()
        self.assertEqual(maturity, "active")
        self.assertEqual(
            audit,
            ("user:1", "Reviewed execution evidence"),
        )

    async def test_canonical_candidates_are_hidden_from_non_members(self):
        from core import auth as _auth

        async with AsyncClient(app=app, base_url="http://test") as ac:
            bot_id = await _create_bot(ac)
        async with database.get_db() as db:
            await db.execute(
                """INSERT INTO users
                   (id,username,password_hash) VALUES (2,'outsider','x')"""
            )
            await db.commit()
        app.dependency_overrides[_auth.get_current_user] = (
            lambda: {"uid": 2, "sub": "outsider"}
        )
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/members/{bot_id}/memory-skills/candidates"
            )
        self.assertEqual(response.status_code, 404)

    # ── 3. 拒绝：删除草稿 ────────────────────────────────────────────────────

    async def test_reject_deletes_draft(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            bot_id = await _create_bot(ac)
            await _write_draft(ac, bot_id, "bad-skill")
            r = await ac.post(f"/api/members/{bot_id}/skills/learned/bad-skill/reject")
        self.assertEqual(r.status_code, 200)

        draft_path = _TEST_WS_ROOT / f"group_{_GROUP_ID}" / "bots" / f"bot_{bot_id}" / "skills" / "learned" / "draft" / "bad-skill.md"
        self.assertFalse(draft_path.exists(), "拒绝后草稿文件应被删除")

        async with AsyncClient(app=app, base_url="http://test") as ac:
            r = await ac.get(f"/api/members/{bot_id}/skills")
        names = [s["name"] for s in r.json()["skills"]]
        self.assertNotIn("bad-skill", names)

    # ── 4. 写 learned/active/ 路径被重定向到 draft（防绕过）──────────────────

    async def test_write_to_active_redirected_to_draft(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            bot_id = await _create_bot(ac)
            content = _SKILL_FRONTMATTER.format(
                name="bypass-attempt", desc="tries to skip approval",
                body="should land in draft"
            )
            r = await ac.put(f"/api/members/{bot_id}/workspace/file", json={
                "path": "skills/learned/active/bypass-attempt.md",
                "content": content,
            })
        self.assertEqual(r.status_code, 200)

        active_path = _TEST_WS_ROOT / f"group_{_GROUP_ID}" / "bots" / f"bot_{bot_id}" / "skills" / "learned" / "active" / "bypass-attempt.md"
        draft_path  = _TEST_WS_ROOT / f"group_{_GROUP_ID}" / "bots" / f"bot_{bot_id}" / "skills" / "learned" / "draft"  / "bypass-attempt.md"
        self.assertFalse(active_path.exists(), "绕过写入 active 应被阻止")
        self.assertTrue(draft_path.exists(),   "应重定向写入 draft")

    # ── 5. 多个草稿：各自独立审批 ────────────────────────────────────────────

    async def test_multiple_drafts_independent_approval(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            bot_id = await _create_bot(ac)
            await _write_draft(ac, bot_id, "skill-a")
            await _write_draft(ac, bot_id, "skill-b")
            r = await ac.post(f"/api/members/{bot_id}/skills/learned/skill-a/approve")
            self.assertEqual(r.status_code, 200)
            r = await ac.get(f"/api/members/{bot_id}/skills")

        skills = {s["name"]: s["status"] for s in r.json()["skills"]}
        self.assertEqual(skills.get("skill-a"), "active", "skill-a 应为 active")
        self.assertEqual(skills.get("skill-b"), "draft",  "skill-b 应仍为 draft")

    # ── 6. 审批不存在的草稿返回 4xx ──────────────────────────────────────────

    async def test_approve_nonexistent_skill_returns_error(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            bot_id = await _create_bot(ac)
            r = await ac.post(f"/api/members/{bot_id}/skills/learned/ghost-skill/approve")
        self.assertIn(r.status_code, (400, 404, 500))

    # ── 7. 审批后 active 文件内容与草稿一致 ─────────────────────────────────

    async def test_approve_preserves_content(self):
        body = "当用户提到 Jira 时，调用 Jira REST API 获取 issue 详情。"
        async with AsyncClient(app=app, base_url="http://test") as ac:
            bot_id = await _create_bot(ac)
            await _write_draft(ac, bot_id, "jira-helper", body=body)
            await ac.post(f"/api/members/{bot_id}/skills/learned/jira-helper/approve")

        active_path = _TEST_WS_ROOT / f"group_{_GROUP_ID}" / "bots" / f"bot_{bot_id}" / "skills" / "learned" / "active" / "jira-helper.md"
        content = active_path.read_text(encoding="utf-8")
        self.assertIn(body, content)

    # ── 8. 非法路径写入被拦截 ────────────────────────────────────────────────

    async def test_write_illegal_path_rejected(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            bot_id = await _create_bot(ac)
            r = await ac.put(f"/api/members/{bot_id}/workspace/file", json={
                "path": "../../../etc/passwd",
                "content": "evil",
            })
        self.assertIn(r.status_code, (400, 403, 422))


if __name__ == "__main__":
    unittest.main()
