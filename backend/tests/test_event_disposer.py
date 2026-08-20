from __future__ import annotations

import unittest
from dataclasses import dataclass

from bus.engine import EventBus
from executors import tool_executor as te


@dataclass
class _Event:
    type = "disposer.test"


class EventDisposerTest(unittest.IsolatedAsyncioTestCase):
    async def test_subscription_close_is_idempotent_and_stops_delivery(self) -> None:
        bus = EventBus()
        sub = bus.subscribe(_Event)
        await bus.publish(_Event())
        self.assertIsNotNone(sub._queue.get_nowait())
        sub.close()
        sub.close()
        await bus.publish(_Event())
        self.assertTrue(sub._queue.empty())

    async def test_active_plugin_disposer_closes_subscription(self) -> None:
        bus = EventBus()
        disposer = te.Disposer()
        sub = bus.subscribe(_Event)
        with te.registration_scope(disposer):
            te.track_disposable(sub)
        disposer.dispose()
        await bus.publish(_Event())
        self.assertTrue(sub._queue.empty())

    def test_non_disposable_resource_is_rejected(self) -> None:
        with te.registration_scope(te.Disposer()):
            with self.assertRaises(TypeError):
                te.track_disposable(object())


if __name__ == "__main__":
    unittest.main()
