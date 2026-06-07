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

    def tearDown(self):
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

    async def test_clear(self):
        from mcp.shared.auth import OAuthToken
        s = MCPTokenStorage("x", self.db)
        await s.set_tokens(OAuthToken(access_token="v", token_type="Bearer"))
        await s.clear()
        self.assertIsNone(await s.get_tokens())


if __name__ == "__main__":
    unittest.main()
