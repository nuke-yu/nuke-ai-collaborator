import unittest
from unittest.mock import MagicMock
from executors.plugins.workspace_tools import _handle_signal_stage_done
from executors.plugins.tool_loop_v1_helpers import _extract_completion_signals

class TestSignalStageDonePrConstraint(unittest.IsolatedAsyncioTestCase):

    async def test_signal_stage_done_pr_required_but_missing(self):
        # Create a mock runner with require_pull_request_completion config
        runner = MagicMock()
        runner.bot = {"executor_config": {"require_pull_request_completion": True}}
        # No successful create_pr in tool_records
        runner.tool_records = [
            {"name": "write_file", "is_error": False},
        ]
        context = {"runner": runner}

        result = await _handle_signal_stage_done(reason="completed task", context=context)
        self.assertTrue(result.startswith("[错误]"))
        self.assertIn("create_pr", result)

    async def test_signal_stage_done_pr_required_and_succeeded(self):
        runner = MagicMock()
        runner.bot = {"executor_config": {"require_pull_request_completion": True}}
        # Successful create_pr in tool_records
        runner.tool_records = [
            {"name": "create_pr", "is_error": False},
        ]
        context = {"runner": runner}

        result = await _handle_signal_stage_done(reason="completed task", context=context)
        self.assertFalse(result.startswith("[错误]"))
        self.assertIn("已记录阶段完成信号", result)

    async def test_signal_stage_done_pr_not_required(self):
        runner = MagicMock()
        runner.bot = {"executor_config": {"require_pull_request_completion": False}}
        runner.tool_records = []
        context = {"runner": runner}

        result = await _handle_signal_stage_done(reason="completed task", context=context)
        self.assertFalse(result.startswith("[错误]"))
        self.assertIn("已记录阶段完成信号", result)

    def test_extract_completion_signals_ignores_failed_signal(self):
        # Test tool call matches tool response content starting with [错误]
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "signal_stage_done", "arguments": '{"reason": "done"}'}
                    }
                ]
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "[错误] 在调用 signal_stage_done 之前，必须先成功调用 create_pr 创建 Pull Request。"
            }
        ]
        signals = _extract_completion_signals(messages, [])
        # The failed signal should be ignored
        self.assertEqual(signals, [])

    def test_extract_completion_signals_includes_successful_signal(self):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "signal_stage_done", "arguments": '{"reason": "done"}'}
                    }
                ]
            },
            {
                "role": "tool",
                "tool_call_id": "call_2",
                "content": "[系统] 已记录阶段完成信号。原因: done。正在推进工作流..."
            }
        ]
        signals = _extract_completion_signals(messages, [])
        self.assertEqual(signals, [
            {
                "name": "signal_stage_done",
                "arguments": {"reason": "done"}
            }
        ])
