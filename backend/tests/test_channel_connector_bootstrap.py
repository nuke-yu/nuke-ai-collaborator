import json
import os
import tempfile
import unittest

from channels.bootstrap import ChannelConnectorConfigError, configure_process_connectors
from channels.runtime import ChannelDeliveryService
from channels.stores import ChannelStore


class TestChannelConnectorBootstrap(unittest.IsolatedAsyncioTestCase):
    async def test_process_connector_is_registered_for_production_delivery(self):
        with tempfile.TemporaryDirectory(prefix="channel-bootstrap-") as directory:
            service = ChannelDeliveryService(ChannelStore(os.path.join(directory, "channel.db")))
            registered = configure_process_connectors(service, json.dumps([{
                "channel_instance_id": "Slack:Prod",
                "argv": ["/opt/nuke/connectors/slack"],
                "env_keys": ["SLACK_BOT_TOKEN"],
            }]))

            self.assertEqual(registered, ("slack:prod",))
            self.assertEqual(service.snapshot()["registered_channels"], ["slack:prod"])
            service.require_registered_instances(["SLACK:PROD"])

    async def test_active_binding_without_connector_fails_startup_validation(self):
        with tempfile.TemporaryDirectory(prefix="channel-bootstrap-") as directory:
            service = ChannelDeliveryService(ChannelStore(os.path.join(directory, "channel.db")))
            with self.assertRaisesRegex(RuntimeError, "slack:prod"):
                service.require_registered_instances(["slack:prod"])

    async def test_invalid_or_secret_bearing_shape_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="channel-bootstrap-") as directory:
            service = ChannelDeliveryService(ChannelStore(os.path.join(directory, "channel.db")))
            with self.assertRaises(ChannelConnectorConfigError):
                configure_process_connectors(service, '{"channel_instance_id":"slack:prod"}')
            with self.assertRaises(ChannelConnectorConfigError):
                configure_process_connectors(service, json.dumps([{
                    "channel_instance_id": "slack:prod",
                    "argv": ["connector"],
                    "secret": "must-not-live-in-descriptor",
                }]))


if __name__ == "__main__":
    unittest.main()
