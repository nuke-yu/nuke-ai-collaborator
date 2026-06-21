"""Workspace HTML preview endpoint: serve a group's workspace file rendered in
the browser (correct Content-Type), with relative assets, token-in-path auth,
and strict path sandboxing.
"""
import os
import sys
import shutil
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database
import db.writer as _db_writer
import workspace
import skills.constants as _skill_const
from main import app
from httpx import AsyncClient, ASGITransport
from core.auth import create_token
from api.workspace import _resolve_preview_path
from workspace import group_workspace

_TEST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_preview.db")
_TEST_WS = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "test_preview_ws"

GID = 1


class TestWorkspacePreview(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Save + redirect globals here (NOT at module level) so collection of this
        # file doesn't pollute DB_PATH / WORKSPACE_ROOT for other test modules.
        self._orig = (database.DB_PATH, _db_writer.DB_PATH,
                      workspace.WORKSPACE_ROOT, _skill_const.WORKSPACE_ROOT)
        database.DB_PATH = _TEST_DB
        _db_writer.DB_PATH = _TEST_DB
        workspace.WORKSPACE_ROOT = _TEST_WS
        _skill_const.WORKSPACE_ROOT = _TEST_WS

        for p in (_TEST_DB, _TEST_DB + "-wal", _TEST_DB + "-shm"):
            if os.path.exists(p):
                os.remove(p)
        if _TEST_WS.exists():
            shutil.rmtree(_TEST_WS)
        await database.init_db()
        async with database.get_db() as d:
            await d.execute("INSERT INTO groups (id, name) VALUES (?, 'G')", (GID,))
            await d.commit()
        # a game: index.html that pulls a sibling style.css (relative asset)
        game = group_workspace(GID) / "workspace" / "game"
        game.mkdir(parents=True, exist_ok=True)
        (game / "index.html").write_text(
            "<!doctype html><link rel=stylesheet href=./style.css><h1>MAZE GAME</h1>",
            encoding="utf-8",
        )
        (game / "style.css").write_text("h1{color:red}", encoding="utf-8")
        self.token = create_token(1, "tester")

    async def asyncTearDown(self):
        await database.aclose_writer()
        if _TEST_WS.exists():
            shutil.rmtree(_TEST_WS)
        for p in (_TEST_DB, _TEST_DB + "-wal", _TEST_DB + "-shm"):
            if os.path.exists(p):
                os.remove(p)
        (database.DB_PATH, _db_writer.DB_PATH,
         workspace.WORKSPACE_ROOT, _skill_const.WORKSPACE_ROOT) = self._orig

    def _client(self):
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    async def test_serves_html_rendered(self):
        async with self._client() as c:
            r = await c.get(f"/api/groups/{GID}/workspace/preview/{self.token}/workspace/game/index.html")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/html"))
        self.assertIn("MAZE GAME", r.text)

    async def test_serves_relative_asset(self):
        async with self._client() as c:
            r = await c.get(f"/api/groups/{GID}/workspace/preview/{self.token}/workspace/game/style.css")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/css"))
        self.assertIn("color:red", r.text)

    async def test_invalid_token_rejected(self):
        async with self._client() as c:
            r = await c.get(f"/api/groups/{GID}/workspace/preview/not-a-real-token/workspace/game/index.html")
        self.assertEqual(r.status_code, 401)

    def test_resolve_within_sandbox(self):
        root = group_workspace(GID).resolve()
        ok = _resolve_preview_path(GID, "workspace/game/index.html")
        self.assertIsNotNone(ok)
        self.assertTrue(ok.is_relative_to(root))

    def test_resolve_rejects_traversal(self):
        self.assertIsNone(_resolve_preview_path(GID, "../../../../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
