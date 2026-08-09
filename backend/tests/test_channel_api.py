import os
import tempfile
import unittest
from unittest.mock import patch

from channels.core import ChannelConversation, ChannelIdentity, OutboundEnvelope
from channels.stores import ChannelStore


class TestChannelApi(unittest.IsolatedAsyncioTestCase):
    async def test_operator_control_handlers_pause_and_replay(self):
        from api.channels import channel_health, pause_channel, replay_channel_delivery, resume_channel

        tmp = tempfile.TemporaryDirectory(prefix="channel-api-")
        try:
            path = os.path.join(tmp.name, "bridge.db")
            store = ChannelStore(path)
            await store.initialize()
            await store.enqueue_outbound(OutboundEnvelope(
                identity=ChannelIdentity("slack", "tenant"), conversation=ChannelConversation("chat"),
                event_type="task_stuck", payload={}, idempotency_key="api-event",
            ))
            await store.claim_due_delivery()
            await store.mark_failed("api-event", "permanent")
            request = type("Request", (), {"url": type("URL", (), {"path": "/api/channels"})(), "method": "POST"})()
            user = {"uid": 1, "sub": "operator"}
            with patch("api.channels.channel_bridge_db_path", return_value=path):
                await pause_channel("slack", request, user)
                health = await channel_health(request, user)
                self.assertEqual(health["paused_channels"], ["slack"])
                await resume_channel("slack", request, user)
                response = type("Request", (), {"json": lambda self: _json({"idempotency_key": "api-event"}), "url": request.url, "method": "POST"})()
                result = await replay_channel_delivery(response, user)
                self.assertTrue(result["replayed"])
        finally:
            tmp.cleanup()


async def _json(value):
    return value


if __name__ == "__main__":
    unittest.main()
