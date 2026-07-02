"""Tests for MCPTokenStorage (persistent per-server OAuth token store)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.providers.mcp_oauth_store import MCPTokenStorage


class TestMCPTokenStorage(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name

    async def asyncTearDown(self):
        from executors.providers.mcp_oauth_store import aclose_all
        await aclose_all()                 # close the shared connection bound to this test's loop
        try:
            os.unlink(self.db)
        except OSError:
            pass

    async def test_tokens_roundtrip(self):
        from mcp.shared.auth import OAuthToken
        s = MCPTokenStorage("github", self.db)
        self.assertIsNone(await s.get_tokens())          # empty
        tok = OAuthToken(access_token="abc123", token_type="Bearer", refresh_token="r1")
        await s.set_tokens(tok)
        got = await s.get_tokens()
        self.assertEqual(got.access_token, "abc123")
        self.assertEqual(got.refresh_token, "r1")

    async def test_per_server_isolation(self):
        from mcp.shared.auth import OAuthToken
        a = MCPTokenStorage("a", self.db)
        b = MCPTokenStorage("b", self.db)
        await a.set_tokens(OAuthToken(access_token="A", token_type="Bearer"))
        self.assertIsNone(await b.get_tokens())          # b unaffected
        self.assertEqual((await a.get_tokens()).access_token, "A")

    async def test_set_tokens_upsert(self):
        from mcp.shared.auth import OAuthToken
        s = MCPTokenStorage("x", self.db)
        await s.set_tokens(OAuthToken(access_token="v1", token_type="Bearer"))
        await s.set_tokens(OAuthToken(access_token="v2", token_type="Bearer"))
        self.assertEqual((await s.get_tokens()).access_token, "v2")

    async def test_connection_reused_per_path(self):
        from executors.providers.mcp_oauth_store import _get_conn, aclose_all
        c1 = await _get_conn(self.db)
        c2 = await _get_conn(self.db)
        self.assertIs(c1, c2)              # one shared connection — no per-op churn
        await aclose_all()
        c3 = await _get_conn(self.db)
        self.assertIsNot(c3, c1)           # reopened after close

    async def test_aclose_all_logs_close_failures_and_clears_cache(self):
        from executors.providers.mcp_oauth_store import _conns, aclose_all

        class BadConn:
            async def close(self):
                raise RuntimeError("close failed")

        _conns["bad.db"] = BadConn()

        with self.assertLogs("executors.providers.mcp_oauth_store", level="ERROR") as logs:
            await aclose_all()

        self.assertEqual(_conns, {})
        self.assertTrue(any("failed to close cached connection for bad.db" in line for line in logs.output))

    async def test_clear(self):
        from mcp.shared.auth import OAuthToken
        s = MCPTokenStorage("x", self.db)
        await s.set_tokens(OAuthToken(access_token="v", token_type="Bearer"))
        await s.clear()
        self.assertIsNone(await s.get_tokens())


if __name__ == "__main__":
    unittest.main()
