from __future__ import annotations

import unittest

from executors import tool_executor as te
from executors.base import ToolDef


class ToolExecutorMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.registry = dict(te._registry)
        self.middlewares = list(te._middlewares)
        te._registry.clear()
        te.clear_middlewares()

    async def asyncTearDown(self) -> None:
        te._registry.clear()
        te._registry.update(self.registry)
        te._middlewares[:] = self.middlewares

    async def test_middleware_wraps_core_in_onion_order(self) -> None:
        events: list[str] = []

        async def handler(**_kwargs):
            events.append("handler")
            return "ok"

        te.register(ToolDef(name="middleware_test", description="test", parameters={}), handler)

        async def outer(name, arguments, context, next_call):
            events.append("outer-before")
            result = await next_call()
            events.append("outer-after")
            return result

        async def inner(name, arguments, context, next_call):
            events.append("inner-before")
            result, is_error = await next_call()
            events.append("inner-after")
            return result + "!", is_error

        te.add_middleware(outer)
        te.add_middleware(inner)
        result = await te.execute("middleware_test", {}, {})

        self.assertEqual(result, ("ok!", False))
        self.assertEqual(
            events,
            ["outer-before", "inner-before", "handler", "inner-after", "outer-after"],
        )

    async def test_middleware_can_short_circuit_without_running_core(self) -> None:
        called = False

        async def handler(**_kwargs):
            nonlocal called
            called = True
            return "should not run"

        te.register(ToolDef(name="middleware_block", description="test", parameters={}), handler)

        async def block(name, arguments, context, next_call):
            return "blocked", True

        te.add_middleware(block)
        self.assertEqual(await te.execute("middleware_block", {}, {}), ("blocked", True))
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
