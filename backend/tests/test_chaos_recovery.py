import asyncio
import os
import sys
import time
import tempfile
import shutil
import unittest
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import json
from unittest.mock import patch

# Ensure the backend directory is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as _db_mod
import db.writer as _writer_mod
from db.schema import init_db
from runtime import ipc
from runtime.supervisor import Supervisor

# Mock LLM HTTP server handler
class MockLLMHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length)
        req = json.loads(body)
        print(f"\n[MockLLMServer] Received POST request: {req}\n", flush=True)
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        
        messages = req.get("messages", [])
        # If the last message is from a tool or we have tool history, finish the workflow
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        
        if has_tool_result:
            response = {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "完毕"
                    },
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}
            }
        else:
            response = {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "mock_blocking_tool",
                                "arguments": "{}"
                            }
                        }]
                    },
                    "finish_reason": "tool_calls"
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}
            }
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def log_message(self, format, *args):
        print(f"[MockLLMServer] Log: {format % args}", flush=True)

class TestChaosRecovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 1. Setup temp dirs and environment variables
        self.tmpdir = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.test_db = os.path.realpath(os.path.join(self.tmpdir, "test_chaos_recovery.db"))
        self.workspace_root = os.path.realpath(os.path.join(self.tmpdir, "workspaces"))
        self.plugins_dir = Path(self.tmpdir) / "plugins"
        self.flags_dir = Path(self.tmpdir) / "flags"
        self.config_file = Path(self.tmpdir) / "app_config.json"
        os.makedirs(self.workspace_root, exist_ok=True)
        self.plugins_dir.mkdir()
        self.flags_dir.mkdir()
        
        self._orig_db = _db_mod.DB_PATH
        self._orig_writer = _writer_mod.DB_PATH
        self.addCleanup(self._restore_db_paths)

        self._env = patch.dict(os.environ, {
            "NUKE_DB_PATH": self.test_db,
            "NUKE_WORKSPACE_ROOT": self.workspace_root,
            "NUKE_EXTERNAL_PLUGINS_DIR": str(self.plugins_dir),
            "NUKE_APP_CONFIG_PATH": str(self.config_file),
            "NUKE_CHAOS_FLAG_DIR": str(self.flags_dir),
            "NUKE_IDEMPOTENT_TOOLS": "mock_blocking_tool",
            "NUKE_GITHUB_ENABLED": "false",
            "MCP_SERVERS_CONFIG": str(Path(self.tmpdir) / "missing-mcp-servers.json"),
        })
        self._env.start()
        self.addCleanup(self._env.stop)

        _db_mod.DB_PATH = self.test_db
        _writer_mod.DB_PATH = self.test_db
        
        # 2. Init DB schemas and seed data
        await init_db()
        async with _db_mod.connect() as db:
            from db.migrations import run_migrations
            await run_migrations(db)
            # Seed group, member (bot), human, and mock workflow state
            await db.execute("INSERT INTO groups (id, name, assigned_worker_id) VALUES (9, 'ChaosGroup', 'w0')")
            await db.execute(
                "INSERT INTO members (id, group_id, name, type, role, auto_reply, model_provider, model_name, executor_config) "
                "VALUES (1, 9, 'ChaosBot', 'bot', 'BA', 0, 'ollama', 'ollama-model', '{\"permission_mode\": \"bypassPermissions\"}')"
            )
            await db.execute(
                "INSERT INTO members (id, group_id, name, type, role, auto_reply) "
                "VALUES (2, 9, 'Human', 'human', 'User', 0)"
            )
            await db.commit()
            
        # 3. Write temp plugin file to register mock_blocking_tool
        self.plugin_file = self.plugins_dir / "chaos_mock_tool.py"
        plugin_code = """import os
import time
from pathlib import Path
from executors import tool_executor
from executors.base import ToolDef

def mock_blocking_tool(arguments: dict = None, context: dict = None) -> tuple[str, bool]:
    flag_dir = Path(os.environ["NUKE_CHAOS_FLAG_DIR"])
    started_flag = flag_dir / "tool_started.txt"
    second_run_flag = flag_dir / "second_run.txt"
    if not second_run_flag.exists():
        started_flag.write_text("started", encoding="utf-8")
        second_run_flag.write_text("run2", encoding="utf-8")
        time.sleep(30)
        return "blocked_and_done", False
    return "completed_successfully", False

tool_executor.register(
    ToolDef(name="mock_blocking_tool", description="Mock blocking tool"),
    mock_blocking_tool
)
"""
        self.plugin_file.write_text(plugin_code, encoding="utf-8")

        # 4. Start a mock LLM server on an OS-assigned port, then point the
        # isolated app config at it.
        self.http_server = HTTPServer(('127.0.0.1', 0), MockLLMHandler)
        self.addCleanup(self.http_server.server_close)
        port = self.http_server.server_address[1]
        self.config_file.write_text(
            json.dumps({"ollama_base_url": f"http://127.0.0.1:{port}"}),
            encoding="utf-8",
        )
        self.http_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.http_thread.start()
        self.addCleanup(self._stop_http_server)

        # Also monkeypatch the local process for recovery calls running in-process
        import sessions.recovery
        self._orig_idempotent_tools = sessions.recovery.IDEMPOTENT_TOOLS
        self.addCleanup(
            setattr,
            sessions.recovery,
            "IDEMPOTENT_TOOLS",
            self._orig_idempotent_tools,
        )
        sessions.recovery.IDEMPOTENT_TOOLS = sessions.recovery.IDEMPOTENT_TOOLS.union({"mock_blocking_tool"})

    def _stop_http_server(self):
        self.http_server.shutdown()
        self.http_thread.join(timeout=2)

    def _restore_db_paths(self):
        _db_mod.DB_PATH = self._orig_db
        _writer_mod.DB_PATH = self._orig_writer

    async def test_worker_sigkill_recovery(self):
        addr = ipc.make_addr(f"chaos_recovery_{os.getpid()}")
        
        # Start Supervisor with 1 worker
        sup = Supervisor(addr, num_workers=1)
        await sup.start()
        
        class BrowserSim:
            def __init__(self):
                self.messages = []
                self.done_event = asyncio.Event()
                
            async def send(self, payload):
                self.messages.append(payload)
                if payload.get("type") == "workflow_update" and payload.get("done") is True:
                    self.done_event.set()
                elif payload.get("type") == "message" and "完毕" in (payload.get("content") or ""):
                    self.done_event.set()
        
        browser = BrowserSim()
        sup.register_browser(9, browser)
        
        try:
            # Wait for worker w0 to register HELLO
            for _ in range(200):
                if "w0" in sup._workers:
                    break
                await asyncio.sleep(0.02)
            self.assertIn("w0", sup._workers)
            
            # Start workflow
            body = {
                "orchestrator_id": "workflow_v1",
                "spec": {
                    "stages": [{
                        "id": 1,
                        "name": "ChaosBot",
                        "avatar_color": "#111",
                        "role": "BA",
                        "stage_type": "single",
                        "gate": False,
                        "instruction": "Test instruction",
                        "done_keyword": "完毕",
                        "executor_id": "tool_loop_v1"
                    }]
                }
            }
            
            await sup.send_to_worker(9, ipc.protocol.envelope(
                ipc.protocol.START_WORKFLOW, group_id=9, body=body, lang="zh"
            ))
            
            # Send USER_MESSAGE to kick off the workflow stage 0 execution
            await sup.send_to_worker(9, ipc.protocol.envelope(
                ipc.protocol.USER_MESSAGE, group_id=9, content="Please start", member_id=2, trace_id="tr-user", online_ids=[2]
            ))
            
            started_flag = self.flags_dir / "tool_started.txt"

            # Wait for mock_blocking_tool to write tool_started.txt
            for _ in range(150):
                if started_flag.exists():
                    break
                await asyncio.sleep(0.1)
                
            if not started_flag.exists():
                print(f"\n[TestChaosRecovery] tool_started.txt not found. browser.messages: {browser.messages}\n", flush=True)
            self.assertTrue(started_flag.exists(), "mock_blocking_tool failed to start")
            
            # SIGKILL the worker process!
            worker_proc = None
            for label, proc in list(sup._processes):
                if label == "w0":
                    worker_proc = proc
                    break
            
            self.assertIsNotNone(worker_proc, "Could not find worker process handle in supervisor")
            
            worker_proc.kill()
            
            started_flag.unlink(missing_ok=True)
                
            # Wait for completion (allow time for process detect + restart + recovery)
            await asyncio.wait_for(browser.done_event.wait(), 15)
            self.assertTrue(browser.done_event.is_set())
            
        finally:
            await sup.stop()
            if os.path.exists(addr):
                os.unlink(addr)

if __name__ == "__main__":
    unittest.main()
