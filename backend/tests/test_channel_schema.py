import os
import tempfile
import unittest

from channels import initialize_channel_schema
from channels.bridge import ChannelBindingStore, IntegrationMemberStore
from channels.stores import ChannelStore


class TestChannelSchema(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_channel_database_contains_all_store_tables(self):
        tmp = tempfile.TemporaryDirectory(prefix="channel-schema-")
        try:
            path = os.path.join(tmp.name, "bridge.db")
            await initialize_channel_schema(path)
            await ChannelStore(path).get_delivery_health()
            self.assertEqual(await ChannelBindingStore(path).list_active_for_group(1), [])
            self.assertEqual(await IntegrationMemberStore(path).list_for_group(1), [])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
