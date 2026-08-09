import os
import asyncio
import tempfile
import unittest

from channels.core import ChannelConversation, ChannelIdentity, OutboundEnvelope
from channels.runtime import GroupChannelRelayService
from channels.stores import ChannelStore
from channels.bridge.group_outbox import GroupChannelOutboxWriter, initialize_group_channel_outbox
import aiosqlite


class TestGroupChannelRelayService(unittest.IsolatedAsyncioTestCase):
    async def test_relay_service_forwards_committed_events_and_stops(self):
        tmp = tempfile.TemporaryDirectory(prefix="channel-relay-service-")
        try:
            channel_path = os.path.join(tmp.name, "channel.db")
            group_path = os.path.join(tmp.name, "group.db")
            store = ChannelStore(channel_path)
            envelope = OutboundEnvelope(
                identity=ChannelIdentity("slack", "tenant"),
                conversation=ChannelConversation("conversation"),
                event_type="workflow.completed",
                payload={"event": {"summary": "done"}},
                idempotency_key="workflow-event-1",
                group_id=7,
            )
            async with aiosqlite.connect(group_path) as db:
                await initialize_group_channel_outbox(db)
                await db.execute("BEGIN")
                self.assertTrue(await GroupChannelOutboxWriter.append(db, envelope))
                await db.commit()

            service = GroupChannelRelayService(
                store,
                lambda: _groups(7),
                lambda _group_id: group_path,
                poll_interval=0.01,
                relay_timeout=1,
                owner_id="test-relay",
            )
            await service.start()
            for _ in range(100):
                if service.snapshot()["forwarded"]:
                    break
                await __import__("asyncio").sleep(0.01)
            await service.stop()
            self.assertEqual(service.snapshot()["forwarded"], 1)
            async with aiosqlite.connect(channel_path) as db:
                async with db.execute("SELECT state FROM channel_delivery_outbox WHERE idempotency_key=?", ("workflow-event-1",)) as cursor:
                    self.assertEqual((await cursor.fetchone())[0], "pending")
        finally:
            tmp.cleanup()

    async def test_health_recovers_and_partial_failure_is_not_success(self):
        with tempfile.TemporaryDirectory(prefix="channel-relay-health-") as directory:
            calls = 0

            async def groups():
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("central db unavailable")
                return []

            service = GroupChannelRelayService(
                ChannelStore(os.path.join(directory, "channel.db")),
                groups,
                lambda _group_id: os.path.join(directory, "group.db"),
                poll_interval=0.01,
            )
            await service.start()
            for _ in range(100):
                snapshot = service.snapshot()
                if snapshot["errors"] == 1 and snapshot["cycles"] >= 1:
                    break
                await asyncio.sleep(0.01)
            recovered = service.snapshot()
            self.assertTrue(recovered["relay_up"])
            self.assertIsNotNone(recovered["last_success_at"])
            self.assertIsNone(recovered["last_error"])
            await service.stop()
            snapshot = service.snapshot()
            self.assertEqual(snapshot["errors"], 1)

    async def test_group_error_does_not_advance_last_success(self):
        with tempfile.TemporaryDirectory(prefix="channel-relay-partial-") as directory:
            service = GroupChannelRelayService(
                ChannelStore(os.path.join(directory, "channel.db")),
                lambda: _groups(7),
                lambda _group_id: os.path.join(directory, "missing", "group.db"),
            )
            await service.start()
            await asyncio.sleep(0.02)
            await service.stop()
            snapshot = service.snapshot()
            self.assertIsNone(snapshot["last_success_at"])
            self.assertIsNotNone(snapshot["last_error"])


async def _groups(group_id: int):
    return [group_id]


if __name__ == "__main__":
    unittest.main()
