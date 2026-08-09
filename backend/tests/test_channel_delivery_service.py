import os
import tempfile
import unittest

from channels.core import ChannelConversation, ChannelIdentity, DeliveryReceipt, OutboundEnvelope
from channels.runtime import ChannelDeliveryService
from channels.stores import ChannelStore


class _Connector:
    async def send(self, envelope):
        return DeliveryReceipt(envelope.identity.channel, envelope.idempotency_key, "sent", "remote")


class TestChannelDeliveryService(unittest.IsolatedAsyncioTestCase):
    async def test_registered_connector_drains_only_its_channel(self):
        tmp = tempfile.TemporaryDirectory(prefix="channel-delivery-service-")
        try:
            store = ChannelStore(os.path.join(tmp.name, "channel.db"))
            await store.initialize()
            await store.enqueue_outbound(OutboundEnvelope(
                identity=ChannelIdentity("slack", "tenant"), conversation=ChannelConversation("chat"),
                event_type="task_stuck", payload={}, idempotency_key="event-1",
            ))
            service = ChannelDeliveryService(store, poll_interval=0.01)
            service.register("slack", _Connector())
            await service.run_once()
            await service.start()
            await service.stop()
            self.assertEqual((await store.get_delivery("event-1"))["state"], "sent")
            self.assertEqual(service.snapshot()["registered_channels"], ["slack"])
        finally:
            tmp.cleanup()
