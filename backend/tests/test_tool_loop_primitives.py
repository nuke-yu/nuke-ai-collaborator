import unittest

from executors.plugins.tool_loop_primitives import tool_loop_core


class ToolLoopPrimitiveContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_injected_model_and_executor_are_used(self):
        calls = []

        async def model(*_args, **_kwargs):
            calls.append("model")
            if len(calls) == 1:
                return {
                    "type": "tool_calls",
                    "calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "a"}}],
                    "assistant_message": {"role": "assistant", "tool_calls": []},
                }
            return {"type": "text", "content": "done"}

        async def execute(name, arguments, *, context):
            calls.append((name, arguments, context))
            return "ok"

        result = await tool_loop_core(
            "system", [], "provider", "model", 0.0, 128, [],
            call_ai_once=model, execute_tool_call=execute,
        )

        self.assertEqual(result, "done")
        self.assertEqual(calls[0], "model")
        self.assertEqual(calls[1], ("read_file", {"path": "a"}, {}))
        self.assertEqual(calls[2], "model")

    async def test_max_iterations_is_enforced(self):
        count = 0

        async def model(*_args, **_kwargs):
            nonlocal count
            count += 1
            return {
                "type": "tool_calls",
                "calls": [{"id": str(count), "name": "noop", "arguments": {}}],
                "assistant_message": {"role": "assistant", "tool_calls": []},
            }

        async def execute(*_args, **_kwargs):
            return "ok"

        result = await tool_loop_core(
            "system", [], "provider", "model", 0.0, 128, [], 2,
            call_ai_once=model, execute_tool_call=execute,
        )

        self.assertEqual(result, "[达到最大工具调用次数，任务未完成]")
        self.assertEqual(count, 2)
