import json
import os
import tempfile
import unittest

from channels.bootstrap import (
    ChannelConnectorConfigError,
    configure_platform_connectors,
    configure_process_connectors,
)
from channels.runtime import ChannelDeliveryService
from channels.secrets import EnvironmentSecretResolver
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

    async def test_native_feishu_and_personal_wechat_are_registered_from_env_names(self):
        with tempfile.TemporaryDirectory(prefix="channel-native-bootstrap-") as directory:
            store = ChannelStore(os.path.join(directory, "channel.db"))
            await store.initialize()
            service = ChannelDeliveryService(store)
            platforms = configure_platform_connectors(
                service,
                store,
                object(),
                json.dumps([
                    {
                        "type": "feishu",
                        "channel_instance_id": "Feishu:Prod",
                        "app_id_env": "FEISHU_APP_ID",
                        "app_secret_env": "FEISHU_APP_SECRET",
                        "verification_token_env": "FEISHU_VERIFY_TOKEN",
                        "encrypt_key_env": "FEISHU_ENCRYPT_KEY",
                    },
                    {
                        "type": "wechat_ilink",
                        "channel_instance_id": "Wechat:Personal",
                        "bot_id_env": "WECHAT_ILINK_BOT_ID",
                        "bot_token_env": "WECHAT_ILINK_BOT_TOKEN",
                    },
                ]),
                secret_resolver=EnvironmentSecretResolver({
                    "FEISHU_APP_ID": "cli_app",
                    "FEISHU_APP_SECRET": "app-secret",
                    "FEISHU_VERIFY_TOKEN": "verify-token",
                    "FEISHU_ENCRYPT_KEY": "encrypt-key",
                    "WECHAT_ILINK_BOT_ID": "wx-bot",
                    "WECHAT_ILINK_BOT_TOKEN": "wx-token",
                }),
            )
            self.assertEqual(
                service.snapshot()["registered_channels"],
                ["feishu:prod", "wechat:personal"],
            )
            self.assertEqual(
                sorted(platforms.snapshot()["instances"]),
                ["feishu:prod", "wechat:personal"],
            )
            await service.stop()

    async def test_native_config_rejects_inline_secrets_and_missing_environment(self):
        with tempfile.TemporaryDirectory(prefix="channel-native-invalid-") as directory:
            store = ChannelStore(os.path.join(directory, "channel.db"))
            service = ChannelDeliveryService(store)
            with self.assertRaises(ChannelConnectorConfigError):
                configure_platform_connectors(
                    service, store, object(),
                    json.dumps([{
                        "type": "wechat_ilink",
                        "channel_instance_id": "wechat:personal",
                        "bot_id_env": "WECHAT_ID",
                        "bot_token_env": "WECHAT_TOKEN",
                        "bot_token": "must-not-be-inline",
                    }]),
                    secret_resolver=EnvironmentSecretResolver({}),
                )
            with self.assertRaisesRegex(ChannelConnectorConfigError, "WECHAT_TOKEN"):
                configure_platform_connectors(
                    ChannelDeliveryService(store), store, object(),
                    json.dumps([{
                        "type": "wechat_ilink",
                        "channel_instance_id": "wechat:personal",
                        "bot_id_env": "WECHAT_ID",
                        "bot_token_env": "WECHAT_TOKEN",
                    }]),
                    secret_resolver=EnvironmentSecretResolver({"WECHAT_ID": "bot-id"}),
                )


if __name__ == "__main__":
    unittest.main()
