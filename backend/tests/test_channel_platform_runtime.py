import asyncio
import unittest

from channels.connectors import FeishuWebhookResult, WechatPollResult
from channels.platform_runtime import ChannelPlatformService


class _Inbound:
    def __init__(self):
        self.calls = []

    async def ingest(self, instance_id, envelope):
        self.calls.append((instance_id, envelope))


class _Wechat:
    def __init__(self):
        self.started = False
        self.polled = asyncio.Event()
        self.block = asyncio.Event()

    async def start(self):
        self.started = True

    async def poll_once(self):
        self.polled.set()
        await self.block.wait()
        return WechatPollResult(0, 0, 0)


class _Feishu:
    def __init__(self, result):
        self.result = result

    def handle_webhook(self, payload, *, raw_body, headers):
        return self.result


class TestChannelPlatformRuntime(unittest.IsolatedAsyncioTestCase):
    async def test_poll_lifecycle_and_feishu_challenge(self):
        inbound = _Inbound()
        service = ChannelPlatformService(inbound, retry_delay=0)
        wechat = _Wechat()
        service.register_wechat("Wechat:Personal", wechat)
        service.register_feishu(
            "Feishu:Prod", _Feishu(FeishuWebhookResult(challenge="challenge"))
        )
        await service.start()
        await asyncio.wait_for(wechat.polled.wait(), 1)
        result = await service.ingest_feishu(
            "feishu:prod", {}, raw_body=b"{}", headers={}
        )
        self.assertEqual(result.challenge, "challenge")
        self.assertTrue(wechat.started)
        self.assertTrue(service.snapshot()["running"])
        await service.stop()
        self.assertFalse(service.snapshot()["running"])


if __name__ == "__main__":
    unittest.main()
