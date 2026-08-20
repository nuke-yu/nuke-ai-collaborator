from __future__ import annotations

import unittest

from executors import tool_executor as te
from executors.base import ToolDef


class PluginDisposerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = dict(te._registry)
        self.before = list(te._before_hooks)
        self.after = list(te._after_hooks)
        self.middlewares = list(te._middlewares)
        te._registry.clear()
        te.clear_before_hooks()
        te.clear_after_hooks()
        te.clear_middlewares()

    def tearDown(self) -> None:
        te._registry.clear()
        te._registry.update(self.registry)
        te._before_hooks[:] = self.before
        te._after_hooks[:] = self.after
        te._middlewares[:] = self.middlewares

    def test_dispose_removes_tools_hooks_and_middlewares_in_reverse_order(self) -> None:
        disposer = te.Disposer()
        events: list[str] = []

        async def handler(**_kwargs):
            return "ok"

        async def before(*_args):
            return None

        async def after(*_args):
            return None

        async def middleware(*_args):
            return "ok", False

        with te.registration_scope(disposer):
            te.register(ToolDef("owned_tool", "test", {}), handler)
            te.add_before_hook(before)
            te.add_after_hook(after)
            te.add_middleware(middleware)
            disposer.add(lambda: events.append("first"))
            disposer.add(lambda: events.append("second"))

        self.assertIn("owned_tool", te._registry)
        self.assertEqual(len(te._before_hooks), 1)
        disposer.dispose()
        self.assertNotIn("owned_tool", te._registry)
        self.assertEqual(te._before_hooks, [])
        self.assertEqual(te._after_hooks, [])
        self.assertEqual(te._middlewares, [])
        self.assertEqual(events, ["second", "first"])
        disposer.dispose()
        self.assertEqual(events, ["second", "first"])

    def test_nested_registration_scopes_restore_outer_disposer(self) -> None:
        outer = te.Disposer()
        inner = te.Disposer()
        events: list[str] = []

        with te.registration_scope(outer):
            outer.add(lambda: events.append("outer"))
            with te.registration_scope(inner):
                inner.add(lambda: events.append("inner"))
            outer.add(lambda: events.append("outer-after-inner"))

        inner.dispose()
        outer.dispose()
        self.assertEqual(events, ["inner", "outer-after-inner", "outer"])


if __name__ == "__main__":
    unittest.main()
