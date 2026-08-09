import os
import tempfile
import unittest

from channels.connectors import ConnectorHttpClient, ConnectorHttpError, ConnectorHttpResponse
from channels.stores import ChannelStore


class _Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, *, headers, json_body, timeout):
        self.calls.append((method, url, json_body))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class TestConnectorHttp(unittest.IsolatedAsyncioTestCase):
    async def test_only_idempotent_requests_retry(self):
        retrying = _Transport([
            ConnectorHttpResponse(500, {}, {}),
            ConnectorHttpResponse(200, {"ok": True}, {}),
        ])
        client = ConnectorHttpClient("feishu", retrying, retry_delay=0)
        response = await client.request_json(
            "token", "POST", "https://example.test/token", idempotent=True
        )
        self.assertTrue(response.body["ok"])
        self.assertEqual(len(retrying.calls), 2)

        sending = _Transport([ConnectorHttpResponse(500, {}, {})])
        client = ConnectorHttpClient("wechat", sending, retry_delay=0)
        with self.assertRaises(ConnectorHttpError):
            await client.request_json("send", "POST", "https://example.test/send")
        self.assertEqual(len(sending.calls), 1)

    async def test_connector_cursor_state_is_instance_scoped(self):
        with tempfile.TemporaryDirectory(prefix="connector-state-") as directory:
            store = ChannelStore(os.path.join(directory, "channel.db"))
            await store.initialize()
            await store.set_connector_state("Wechat:One", "sync_cursor", {"cursor": "a"})
            await store.set_connector_state("wechat:two", "sync_cursor", {"cursor": "b"})
            self.assertEqual(
                await store.get_connector_state("WECHAT:ONE", "sync_cursor"),
                {"cursor": "a"},
            )
            self.assertEqual(
                await store.get_connector_state("wechat:two", "sync_cursor"),
                {"cursor": "b"},
            )


if __name__ == "__main__":
    unittest.main()
