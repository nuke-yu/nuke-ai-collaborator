import asyncio
import json
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

from executors.plugin_ipc import (
    PluginCapability,
    PluginManifest,
    PluginProcessClient,
    PluginProcessError,
)


class TestPluginIPC(unittest.IsolatedAsyncioTestCase):
    async def test_request_contains_capability_and_result_is_redacted(self):
        class FakeReader:
            async def readline(self):
                return json.dumps({"ok": True, "result": "Authorization: Bearer " + "a" * 48}).encode() + b"\n"

        class FakeProcess:
            returncode = None
            class Stdin:
                def __init__(self):
                    self.write = Mock()
                    self.drain = AsyncMock()
            stdin = Stdin()
            stdout = FakeReader()

        process = FakeProcess()
        client = PluginProcessClient(
            [sys.executable, "plugin_worker.py"],
            PluginManifest("high-risk", "1", PluginCapability(filesystem_scope=("/tmp/work",))),
        )
        with patch("executors.plugin_ipc.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
            response = await client.call("run", {"x": 1})
        self.assertTrue(response["ok"])
        self.assertNotIn("a" * 48, response["result"])
        request = json.loads(process.stdin.write.call_args.args[0])
        self.assertEqual(request["capability"]["filesystem_scope"], ["/tmp/work"])

    async def test_timeout_cancels_plugin(self):
        class HangingReader:
            async def readline(self):
                await asyncio.sleep(10)

        class Stdin:
            def write(self, _data):
                return None
            async def drain(self):
                return None

        process = type("Process", (), {
            "returncode": None, "stdin": Stdin(), "stdout": HangingReader(),
            "terminate": lambda self: setattr(self, "returncode", -15),
            "wait": AsyncMock(), "kill": lambda self: setattr(self, "returncode", -9),
        })()
        client = PluginProcessClient(
            [sys.executable, "plugin_worker.py"],
            PluginManifest("slow", "1", PluginCapability(max_seconds=0.01)),
        )
        with patch("executors.plugin_ipc.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
            with self.assertRaises(PluginProcessError):
                await client.call("run")

    async def test_manifest_limits_and_hil_are_enforced(self):
        manifest = PluginManifest(
            "governed", "2", PluginCapability(
                max_input_bytes=8,
                allowed_methods=("read", "write"),
                write_methods=("write",),
            )
        )
        client = PluginProcessClient([sys.executable, "plugin_worker.py"], manifest)
        with self.assertRaisesRegex(PluginProcessError, "human approval"):
            await client.call("write")
        with self.assertRaisesRegex(PluginProcessError, "input limit"):
            await client.call("read", {"too_large": True})
        with self.assertRaisesRegex(PluginProcessError, "not allowed"):
            await client.call("unknown")

    async def test_manifest_hash_and_status_are_exposed(self):
        manifest = PluginManifest("status", "1")
        client = PluginProcessClient([sys.executable, "plugin_worker.py"], manifest)
        status = client.status()
        self.assertEqual(status["plugin_id"], "status")
        self.assertEqual(status["manifest_hash"], manifest.canonical_hash)
        self.assertEqual(status["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
