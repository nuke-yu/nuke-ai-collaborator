"""Opt-in smoke test against the REAL GitHub MCP server (stdio + PAT).

Validates the MCP machinery end-to-end against a real server — the part that
unit tests with mocks can't cover (real handshake, real schema parsing, real
tool call, our result fence + redaction). Does NOT exercise OAuth (PAT is a
static token).

Run it by exporting a GitHub token; it SKIPS otherwise (so normal CI is unaffected):

    export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxx          # a read-scoped PAT is enough
    python3 -m pytest tests/test_mcp_github_smoke.py -v -s

Server command defaults to `npx -y @modelcontextprotocol/server-github`; override
with GITHUB_MCP_COMMAND / GITHUB_MCP_ARGS (e.g. a local github-mcp-server binary).
First run may be slow (npx download) — timeouts are generous.
"""
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TOKEN = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
_CMD = os.environ.get("GITHUB_MCP_COMMAND", "npx")
_ARGS = (os.environ["GITHUB_MCP_ARGS"].split()
         if os.environ.get("GITHUB_MCP_ARGS")
         else ["-y", "@modelcontextprotocol/server-github"])


@unittest.skipUnless(_TOKEN, "set GITHUB_PERSONAL_ACCESS_TOKEN to run the GitHub MCP smoke test")
class TestGithubProviderSmoke(unittest.IsolatedAsyncioTestCase):
    """Level 1: McpClientToolProvider directly against the real server."""

    async def test_handshake_discover_and_call(self):
        from executors.providers.mcp_client import McpClientToolProvider
        p = McpClientToolProvider(
            server_name="github", command=_CMD, args=_ARGS,
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": _TOKEN}, call_timeout=60,
        )
        await p.initialize()
        try:
            tools = p.discover_tools()
            self.assertTrue(tools, "expected the GitHub MCP server to advertise tools")
            names = {t.name for t in tools}
            print(f"\n[smoke] GitHub MCP exposed {len(names)} tools, e.g. {sorted(names)[:8]}")

            # Best-effort real read call (validates execution + result fence/redaction).
            target = next((n for n in names if n.endswith("__search_repositories")), None)
            if target:
                result, is_error = await p.execute(
                    target, {"query": "modelcontextprotocol"}, {"_pre_authorized": True})
                self.assertFalse(is_error, f"search_repositories errored: {result[:200]}")
                self.assertIn("不可信", result)   # our untrusted-result fence applied
                print(f"[smoke] {target} returned {len(result)} chars (fenced)")
        finally:
            await p.close()


@unittest.skipUnless(_TOKEN, "set GITHUB_PERSONAL_ACCESS_TOKEN to run the GitHub MCP smoke test")
class TestGithubCollectorSmoke(unittest.IsolatedAsyncioTestCase):
    """Level 2: full collector → bus → real server round-trip via an in-test supervisor."""

    async def test_collector_roundtrip_against_real_server(self):
        import asyncio
        from runtime import ipc
        from runtime.mcp_collector import MCPCollector

        cfg = {"mcpServers": {"github": {
            "command": _CMD, "args": _ARGS,
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": _TOKEN},
            "call_timeout": 60, "enabled": True,
        }}}
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(cfg, tf); tf.close()
        os.environ["MCP_SERVERS_CONFIG"] = tf.name

        addr = ipc.make_addr(f"gh_smoke_{os.getpid()}")
        schemas_got = asyncio.Event()
        result_got = asyncio.Event()
        got = {"schemas": None, "result": None}
        coll_writer = {}

        async def on_conn(reader, writer):
            await ipc.recv_msg(reader)              # HELLO
            coll_writer["w"] = writer
            try:
                while True:
                    f = await ipc.recv_msg(reader)
                    if f is None:
                        break
                    if f.get("type") == ipc.protocol.MCP_SCHEMAS:
                        got["schemas"] = f["payload"]["schemas"]; schemas_got.set()
                    elif f.get("type") == ipc.protocol.MCP_RESULT:
                        got["result"] = f; result_got.set()
            except (asyncio.IncompleteReadError, ConnectionResetError):
                pass

        server = await ipc.serve(addr, on_conn)
        coll = MCPCollector(addr)
        run_task = asyncio.create_task(coll.run())
        try:
            await asyncio.wait_for(schemas_got.wait(), 90)   # first run may npx-download
            names = [s["function"]["name"] for s in got["schemas"]]
            self.assertTrue(names)
            target = next((n for n in names if n.endswith("__search_repositories")), names[0])
            await ipc.send_msg(coll_writer["w"], ipc.protocol.envelope(
                ipc.protocol.MCP_CALL, group_id=1, request_id="g1", origin_worker_id="w0",
                tool=target, arguments={"query": "modelcontextprotocol"}))
            await asyncio.wait_for(result_got.wait(), 60)
            self.assertEqual(got["result"]["request_id"], "g1")
            print(f"\n[smoke] collector round-trip via {target}: is_error={got['result']['is_error']}")
        finally:
            run_task.cancel()
            try:
                await run_task
            except BaseException:
                pass
            server.close()
            os.environ.pop("MCP_SERVERS_CONFIG", None)
            os.unlink(tf.name)


if __name__ == "__main__":
    unittest.main()
