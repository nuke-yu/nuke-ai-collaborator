import os
import tempfile
import unittest

from channels.core import ChannelConversation, ChannelIdentity, DeliveryReceipt, OutboundEnvelope
from channels.runtime import ChannelDeliveryService
from channels.stores import ChannelStore


class _Connector:
    def __init__(self):
        self.envelopes = []

    async def send(self, envelope):
        self.envelopes.append(envelope)
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
                source_event_id="workflow-source-1",
            ))
            service = ChannelDeliveryService(store, poll_interval=0.01)
            connector = _Connector()
            service.register("slack", connector)
            await service.run_once()
            await service.start()
            self.assertTrue(service.snapshot()["running"])
            self.assertTrue(service.snapshot()["delivery_up"])
            await service.stop()
            self.assertFalse(service.snapshot()["running"])
            self.assertFalse(service.snapshot()["delivery_up"])
            self.assertEqual((await store.get_delivery("event-1"))["state"], "sent")
            self.assertEqual(connector.envelopes[0].source_event_id, "workflow-source-1")
            self.assertEqual(
                (await store.list_audit("event-1"))[-1]["details"]["source_event_id"],
                "workflow-source-1",
            )
            self.assertEqual(service.snapshot()["registered_channels"], ["slack"])
        finally:
            tmp.cleanup()

    async def test_start_fails_when_open_delivery_has_no_connector(self):
        with tempfile.TemporaryDirectory(prefix="channel-delivery-orphan-") as directory:
            store = ChannelStore(os.path.join(directory, "channel.db"))
            await store.initialize()
            await store.enqueue_outbound(OutboundEnvelope(
                identity=ChannelIdentity("slack", "tenant"),
                conversation=ChannelConversation("chat"),
                event_type="task_stuck",
                payload={},
                idempotency_key="orphan-event",
                channel_instance_id="slack:prod",
            ))
            service = ChannelDeliveryService(store)
            with self.assertRaisesRegex(RuntimeError, "slack:prod"):
                await service.start()
            self.assertEqual((await store.get_delivery("orphan-event"))["state"], "pending")
