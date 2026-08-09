import unittest
from unittest.mock import AsyncMock, Mock, patch

from runtime.supervisor import Supervisor


class TestSupervisorChannelRelay(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_starts_stops_and_exposes_relay_stats(self):
        relay = AsyncMock()
        relay.snapshot = Mock(return_value={"cycles": 1})
        platform = AsyncMock()
        platform.snapshot = Mock(return_value={"instances": {"wechat:personal": {}}})
        supervisor = Supervisor(
            "unused", num_workers=0, channel_relay=relay, channel_platform=platform
        )
        fake_server = Mock()
        fake_server.wait_closed = AsyncMock()
        with patch("runtime.supervisor.ipc.serve", new=AsyncMock(return_value=fake_server)):
            await supervisor.start()
        relay.start.assert_awaited_once()
        platform.start.assert_awaited_once()
        self.assertEqual(supervisor.get_stats()["channel_relay"], {"cycles": 1})
        self.assertIn("wechat:personal", supervisor.get_stats()["channel_platform"]["instances"])
        await supervisor.stop()
        relay.stop.assert_awaited_once()
        platform.stop.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
