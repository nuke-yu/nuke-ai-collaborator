"""signal_stage_done / signal_rework 的 handler 必须遵循 tool_executor 的调用约定：
handler(**arguments, context=...)（见 tool_executor.execute）。曾因签名写成
(arguments, ctx) 位置参，导致 bot 调 signal_stage_done(reason=...) 时报
'unexpected keyword argument reason'，整个阶段完成信号失效。
"""
import asyncio
import inspect
import unittest

from executors.plugins import workspace_tools as wt


class TestSignalHandlersDispatch(unittest.TestCase):
    def _call_like_executor(self, handler, arguments, context):
        # 复刻 tool_executor.execute 的调用方式
        sig = inspect.signature(handler)
        if "context" in sig.parameters:
            return asyncio.run(handler(**arguments, context=context))
        return asyncio.run(handler(**arguments))

    def test_stage_done_accepts_reason_kwarg(self):
        out = self._call_like_executor(
            wt._handle_signal_stage_done, {"reason": "需求已澄清"}, {"group_id": 3}
        )
        self.assertIn("阶段完成", out)
        self.assertIn("需求已澄清", out)

    def test_rework_accepts_target_and_reason_kwargs(self):
        out = self._call_like_executor(
            wt._handle_signal_rework,
            {"target_stage": "Dev", "reason": "AC3 未通过"},
            {"group_id": 3},
        )
        self.assertIn("返工", out)
        self.assertIn("Dev", out)
        self.assertIn("AC3 未通过", out)


if __name__ == "__main__":
    unittest.main()
