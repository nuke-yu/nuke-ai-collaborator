"""Tests for MCPAuthFlows — the collector-side OAuth flow orchestrator."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.providers.mcp_auth_flows import MCPAuthFlows


class TestMCPAuthFlows(unittest.IsolatedAsyncioTestCase):

    async def test_full_flow(self):
        f = MCPAuthFlows()
        url_fut = f.begin("github")

        # SDK calls redirect_handler with the auth URL (carries state)
        redirect = f.redirect_handler_for("github")
        await redirect("https://auth.example.com/authorize?client_id=x&state=ST123&code=ignored")
        # the URL is surfaced to the caller (→ returned to the bot)
        self.assertEqual(await url_fut, "https://auth.example.com/authorize?client_id=x&state=ST123&code=ignored")

        # SDK awaits callback_handler; resolve it via the bus callback
        callback = f.callback_handler_for("github")
        cb_task = asyncio.create_task(callback())
        await asyncio.sleep(0)
        self.assertTrue(f.resolve_callback("ST123", "AUTHCODE"))
        code, state = await cb_task
        self.assertEqual((code, state), ("AUTHCODE", "ST123"))

    async def test_resolve_unknown_state(self):
        f = MCPAuthFlows()
        self.assertFalse(f.resolve_callback("nope", "c"))

    async def test_callback_without_redirect_raises(self):
        f = MCPAuthFlows()
        with self.assertRaises(RuntimeError):
            await f.callback_handler_for("github")()

    async def test_fail_aborts_pending(self):
        f = MCPAuthFlows()
        url_fut = f.begin("github")
        await f.redirect_handler_for("github")("https://a/x?state=S1")
        # url already resolved; now a second begin + fail
        url_fut2 = f.begin("gitlab")
        f.fail("gitlab", "timeout")
        with self.assertRaises(RuntimeError):
            await url_fut2

    async def test_state_isolation_between_servers(self):
        f = MCPAuthFlows()
        f.begin("a"); f.begin("b")
        await f.redirect_handler_for("a")("https://x?state=SA")
        await f.redirect_handler_for("b")("https://x?state=SB")
        ca = asyncio.create_task(f.callback_handler_for("a")())
        cb = asyncio.create_task(f.callback_handler_for("b")())
        await asyncio.sleep(0)
        f.resolve_callback("SB", "codeB")
        f.resolve_callback("SA", "codeA")
        self.assertEqual((await ca)[0], "codeA")
        self.assertEqual((await cb)[0], "codeB")

    async def test_begin_replaces_previous_pending_flow(self):
        f = MCPAuthFlows()
        first = f.begin("github")
        second = f.begin("github")

        with self.assertRaises(RuntimeError):
            await first

        self.assertFalse(second.done())
        await f.redirect_handler_for("github")("https://auth.example.com/authorize?state=ST2")
        self.assertEqual(await second, "https://auth.example.com/authorize?state=ST2")


if __name__ == "__main__":
    unittest.main()
