import unittest
from unittest.mock import AsyncMock

from ws_manager import WSManager


class TestWSManagerCursor(unittest.IsolatedAsyncioTestCase):
    async def test_replays_events_after_cursor(self):
        manager = WSManager()
        first = AsyncMock()
        second = AsyncMock()
        await manager.broadcast(7, {"event_id": "evt_1", "type": "message", "id": 1})
        await manager.broadcast(7, {"event_id": "evt_2", "type": "message", "id": 2})
        await manager.connect(second, 7, 2, cursor="evt_1")
        replayed = [call.args[0] for call in second.send_json.await_args_list]
        self.assertEqual([event["event_id"] for event in replayed], ["evt_2"])
        await manager.connect(first, 7, 1, cursor="evt_missing")
        self.assertEqual(first.send_json.await_count, 0)

    async def test_broadcast_assigns_event_id_for_legacy_payload(self):
        manager = WSManager()
        await manager.broadcast(1, {"type": "presence"})
        self.assertTrue(manager.history[1][0]["event_id"].startswith("evt_"))


if __name__ == "__main__":
    unittest.main()
