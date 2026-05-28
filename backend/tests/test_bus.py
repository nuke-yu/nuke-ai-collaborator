"""
tests/test_bus.py — bus 模块单元测试

覆盖：
  - bus/events.py  : 事件注册表、类型属性、dataclass 序列化
  - bus/engine.py  : typed / wildcard 订阅、fan-out、cleanup、兼容 broadcast()
  - bus/adapter.py : WS 推送、group_id 路由、异常隔离
"""
import asyncio
import dataclasses
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── events ──────────────────────────────────────────────────────────────────

from bus.events import (
    _registry, event,
    StreamStart, StreamChunk, StreamError, StreamEnd, StreamAborted,
    Message, Read, Presence, Typing, Error,
    SteerQueued, FollowupStart, SteerInjected, RewakeInjected,
    ToolCall, ToolResult,
    ReactThought, ReactAction, ReactObservation,
    Compaction, SkillsLoaded, SkillForkStart, SkillForkEnd, SkillDraftAdded,
    BeforeFinalizeReview, BeforeFinalizeApproved, BeforeFinalizeRejected,
    PermissionAsked,
)
from bus.engine import EventBus, Subscription
from bus.adapter import ws_adapter


# ═══════════════════════════════════════════════════════════════════════════════
# 一、events.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestEventRegistry(unittest.TestCase):

    def test_all_events_registered(self):
        """每个 @event 装饰的类都必须出现在 _registry 中。"""
        expected = {
            "stream_start", "stream_chunk", "stream_error", "stream_end", "stream_aborted",
            "message", "read", "presence", "typing", "error",
            "steer_queued", "followup_start", "steer_injected", "rewake_injected",
            "tool_call", "tool_result",
            "react_thought", "react_action", "react_observation",
            "compaction", "skills_loaded",
            "skill_fork_start", "skill_fork_end", "skill_draft_added",
            "before_finalize_review", "before_finalize_approved", "before_finalize_rejected",
            "permission_asked",
        }
        self.assertEqual(expected, set(_registry.keys()))

    def test_event_type_attribute(self):
        """每个事件类必须有 .type 类属性，且与注册 key 一致。"""
        for type_name, cls in _registry.items():
            self.assertEqual(cls.type, type_name, f"{cls.__name__}.type mismatch")

    def test_events_are_dataclasses(self):
        """所有事件类都应该是 dataclass，支持 asdict。"""
        event = StreamStart(
            group_id=1, temp_id="t1", member_id=10,
            sender_name="Bot", sender_type="bot", avatar_color="#fff",
        )
        d = dataclasses.asdict(event)
        self.assertEqual(d["group_id"], 1)
        self.assertEqual(d["temp_id"], "t1")
        self.assertNotIn("type", d)  # type 是类属性，不是实例字段

    def test_custom_event_decorator(self):
        """@event 装饰器能给任意类注册 .type 并使其成为 dataclass。"""
        @event("test.custom_event_do_not_use")
        class _TestEvent:
            group_id: int
            value: str

        self.assertEqual(_TestEvent.type, "test.custom_event_do_not_use")
        self.assertIn("test.custom_event_do_not_use", _registry)
        inst = _TestEvent(group_id=99, value="hello")
        self.assertEqual(inst.value, "hello")

        # 清理，避免污染其他测试
        del _registry["test.custom_event_do_not_use"]

    def test_message_optional_fields(self):
        """Message 的可选字段有默认值，不需要全部传入。"""
        msg = Message(
            group_id=1, id=42, member_id=5,
            sender_name="Alice", sender_type="human",
            content="hello", created_at="2026-01-01 00:00:00",
        )
        self.assertIsNone(msg.reply_to_id)
        self.assertIsNone(msg.file_url)
        self.assertFalse(msg.is_auto_reply)
        self.assertFalse(msg.is_deleted)


# ═══════════════════════════════════════════════════════════════════════════════
# 二、engine.py — EventBus
# ═══════════════════════════════════════════════════════════════════════════════

class TestEventBusTyped(unittest.IsolatedAsyncioTestCase):

    async def test_typed_subscriber_receives_matching_event(self):
        bus = EventBus()
        sub = bus.subscribe(StreamChunk)
        await bus.publish(StreamChunk(group_id=1, temp_id="t", delta="hello"))
        payload = await asyncio.wait_for(sub._queue.get(), timeout=1)
        self.assertEqual(payload["type"], "stream_chunk")
        self.assertEqual(payload["delta"], "hello")
        self.assertEqual(payload["group_id"], 1)

    async def test_typed_subscriber_ignores_other_events(self):
        bus = EventBus()
        sub = bus.subscribe(StreamChunk)
        await bus.publish(StreamStart(
            group_id=1, temp_id="t", member_id=1,
            sender_name="Bot", sender_type="bot", avatar_color="#000",
        ))
        self.assertTrue(sub._queue.empty(), "StreamChunk 订阅不应收到 StreamStart")

    async def test_multiple_typed_subscribers_fan_out(self):
        """同一 type 的多个订阅者都应收到事件（fan-out）。"""
        bus = EventBus()
        sub1 = bus.subscribe(StreamChunk)
        sub2 = bus.subscribe(StreamChunk)
        await bus.publish(StreamChunk(group_id=2, temp_id="t", delta="world"))
        p1 = await asyncio.wait_for(sub1._queue.get(), timeout=1)
        p2 = await asyncio.wait_for(sub2._queue.get(), timeout=1)
        self.assertEqual(p1["delta"], "world")
        self.assertEqual(p2["delta"], "world")

    async def test_typed_cleanup_removes_subscriber(self):
        """Subscription.cleanup() 后，该订阅者不再收到事件。"""
        bus = EventBus()
        sub = bus.subscribe(StreamChunk)
        sub._cleanup()  # 手动取消订阅
        await bus.publish(StreamChunk(group_id=1, temp_id="t", delta="gone"))
        self.assertTrue(sub._queue.empty())

    async def test_publish_no_subscribers_no_error(self):
        """没有订阅者时 publish 不应抛出异常。"""
        bus = EventBus()
        try:
            await bus.publish(StreamEnd(
                group_id=1, temp_id="t", id=1,
                member_id=1, sender_name="Bot",
                preview="hi", created_at="2026-01-01",
            ))
        except Exception as e:
            self.fail(f"publish with no subscribers raised: {e}")


class TestEventBusWildcard(unittest.IsolatedAsyncioTestCase):

    async def test_wildcard_receives_all_events(self):
        bus = EventBus()
        sub = bus.subscribe_all()
        await bus.publish(StreamChunk(group_id=1, temp_id="t", delta="a"))
        await bus.publish(Presence(group_id=1, member_id=5, online=True))
        types = []
        for _ in range(2):
            p = await asyncio.wait_for(sub._queue.get(), timeout=1)
            types.append(p["type"])
        self.assertIn("stream_chunk", types)
        self.assertIn("presence", types)

    async def test_wildcard_cleanup_removes_subscriber(self):
        bus = EventBus()
        sub = bus.subscribe_all()
        sub._cleanup()
        await bus.publish(Presence(group_id=1, member_id=5, online=False))
        self.assertTrue(sub._queue.empty())

    async def test_typed_and_wildcard_both_receive(self):
        """同一事件同时推送给 typed 和 wildcard 订阅者。"""
        bus = EventBus()
        typed_sub = bus.subscribe(Read)
        wild_sub = bus.subscribe_all()
        await bus.publish(Read(group_id=3, member_id=7, last_read_id=99))
        p_typed = await asyncio.wait_for(typed_sub._queue.get(), timeout=1)
        p_wild = await asyncio.wait_for(wild_sub._queue.get(), timeout=1)
        self.assertEqual(p_typed["last_read_id"], 99)
        self.assertEqual(p_wild["last_read_id"], 99)


class TestEventBusBroadcastCompat(unittest.IsolatedAsyncioTestCase):

    async def test_broadcast_compat_reaches_wildcard(self):
        """broadcast(group_id, dict) 兼容接口应推送到 wildcard 订阅者。"""
        bus = EventBus()
        sub = bus.subscribe_all()
        await bus.broadcast(5, {"type": "tool_call", "tool_name": "bash", "tool_input": "ls"})
        p = await asyncio.wait_for(sub._queue.get(), timeout=1)
        self.assertEqual(p["type"], "tool_call")
        self.assertEqual(p["group_id"], 5)
        self.assertEqual(p["tool_name"], "bash")

    async def test_broadcast_compat_reaches_typed(self):
        """broadcast(group_id, dict) 应同样推送给 typed 订阅者。"""
        bus = EventBus()
        sub = bus.subscribe(ToolCall)
        await bus.broadcast(1, {"type": "tool_call", "tool_name": "read", "tool_input": {}})
        p = await asyncio.wait_for(sub._queue.get(), timeout=1)
        self.assertEqual(p["group_id"], 1)
        self.assertEqual(p["tool_name"], "read")

    async def test_broadcast_injects_group_id(self):
        """broadcast 必须把 group_id 注入到 payload 中。"""
        bus = EventBus()
        sub = bus.subscribe_all()
        await bus.broadcast(42, {"type": "error", "message": "oops"})
        p = await asyncio.wait_for(sub._queue.get(), timeout=1)
        self.assertEqual(p["group_id"], 42)

    async def test_broadcast_does_not_duplicate_group_id(self):
        """payload 原来不含 group_id，broadcast 注入后只有一个。"""
        bus = EventBus()
        sub = bus.subscribe_all()
        await bus.broadcast(7, {"type": "typing", "sender_name": "X", "avatar_color": "#f00"})
        p = await asyncio.wait_for(sub._queue.get(), timeout=1)
        # dict key 唯一，检查值正确
        self.assertEqual(p["group_id"], 7)


class TestSubscriptionContextManager(unittest.IsolatedAsyncioTestCase):

    async def test_async_context_manager_cleanup(self):
        """async with Subscription 退出时自动取消订阅。"""
        bus = EventBus()
        async with bus.subscribe_all() as sub:
            self.assertIn(sub._queue, bus._wildcard)
        # 退出后应从 wildcard 列表移除
        self.assertNotIn(sub._queue, bus._wildcard)

    async def test_async_iterator(self):
        """Subscription Queue 能按顺序取出事件。"""
        bus = EventBus()
        sub = bus.subscribe(Read)

        for i in range(3):
            await bus.publish(Read(group_id=1, member_id=i, last_read_id=i))

        collected = []
        for _ in range(3):
            p = await asyncio.wait_for(sub._queue.get(), timeout=1)
            collected.append(p["member_id"])
        sub._cleanup()

        self.assertEqual(collected, [0, 1, 2])


# ═══════════════════════════════════════════════════════════════════════════════
# 三、adapter.py — WS 推送适配
# ═══════════════════════════════════════════════════════════════════════════════

class TestWSAdapter(unittest.IsolatedAsyncioTestCase):

    async def test_adapter_broadcasts_to_correct_group(self):
        """adapter 应把 bus 事件路由到对应 group_id 的 WS clients。"""
        bus = EventBus()
        mock_manager = MagicMock()
        mock_manager.broadcast = AsyncMock()

        with patch("bus.adapter.manager", mock_manager):
            task = asyncio.create_task(ws_adapter(bus))
            await asyncio.sleep(0)  # 让 adapter 启动并订阅

            await bus.publish(StreamChunk(group_id=3, temp_id="t", delta="hi"))
            await asyncio.sleep(0.05)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_manager.broadcast.assert_called_once()
        call_group_id, call_payload = mock_manager.broadcast.call_args[0]
        self.assertEqual(call_group_id, 3)
        self.assertEqual(call_payload["type"], "stream_chunk")
        self.assertEqual(call_payload["delta"], "hi")

    async def test_adapter_strips_group_id_from_payload(self):
        """adapter 推送给客户端的 payload 不应包含 group_id。"""
        bus = EventBus()
        mock_manager = MagicMock()
        mock_manager.broadcast = AsyncMock()

        with patch("bus.adapter.manager", mock_manager):
            task = asyncio.create_task(ws_adapter(bus))
            await asyncio.sleep(0)

            await bus.publish(Presence(group_id=1, member_id=5, online=True))
            await asyncio.sleep(0.05)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        _, call_payload = mock_manager.broadcast.call_args[0]
        self.assertNotIn("group_id", call_payload)

    async def test_adapter_skips_event_without_group_id(self):
        """group_id 缺失的事件应被跳过，不调用 manager.broadcast。"""
        bus = EventBus()
        mock_manager = MagicMock()
        mock_manager.broadcast = AsyncMock()

        with patch("bus.adapter.manager", mock_manager):
            task = asyncio.create_task(ws_adapter(bus))
            await asyncio.sleep(0)

            # 直接往 wildcard 推一个没有 group_id 的 payload
            for q in bus._wildcard:
                await q.put({"type": "orphan_event"})
            await asyncio.sleep(0.05)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_manager.broadcast.assert_not_called()

    async def test_adapter_continues_after_broadcast_error(self):
        """manager.broadcast 抛出异常时，adapter 不应崩溃，继续处理后续事件。"""
        bus = EventBus()
        mock_manager = MagicMock()
        call_count = 0

        async def flaky_broadcast(group_id, payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("WS connection broken")

        mock_manager.broadcast = AsyncMock(side_effect=flaky_broadcast)

        with patch("bus.adapter.manager", mock_manager):
            task = asyncio.create_task(ws_adapter(bus))
            await asyncio.sleep(0)

            # 第一个事件触发异常
            await bus.publish(StreamChunk(group_id=1, temp_id="t1", delta="fail"))
            await asyncio.sleep(0.05)
            # 第二个事件应正常处理
            await bus.publish(StreamChunk(group_id=2, temp_id="t2", delta="ok"))
            await asyncio.sleep(0.05)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.assertEqual(call_count, 2)

    async def test_adapter_routes_multiple_groups(self):
        """同一批事件中不同 group_id 应各自路由到对应 group。"""
        bus = EventBus()
        mock_manager = MagicMock()
        mock_manager.broadcast = AsyncMock()

        with patch("bus.adapter.manager", mock_manager):
            task = asyncio.create_task(ws_adapter(bus))
            await asyncio.sleep(0)

            await bus.publish(Typing(group_id=1, sender_name="A", avatar_color="#f00"))
            await bus.publish(Typing(group_id=2, sender_name="B", avatar_color="#00f"))
            await asyncio.sleep(0.05)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        calls = mock_manager.broadcast.call_args_list
        self.assertEqual(len(calls), 2)
        groups = {c[0][0] for c in calls}
        self.assertEqual(groups, {1, 2})


# ═══════════════════════════════════════════════════════════════════════════════
# 四、集成：bus.publish → adapter → manager.broadcast
# ═══════════════════════════════════════════════════════════════════════════════

class TestBusAdapterIntegration(unittest.IsolatedAsyncioTestCase):

    async def test_full_stream_lifecycle(self):
        """完整流式输出事件序列能按顺序抵达 mock manager。"""
        from bus import bus as global_bus
        # 用独立实例避免污染全局 bus
        local_bus = EventBus()
        mock_manager = MagicMock()
        received = []

        async def capture_broadcast(group_id, payload):
            received.append((group_id, payload["type"]))

        mock_manager.broadcast = AsyncMock(side_effect=capture_broadcast)

        with patch("bus.adapter.manager", mock_manager):
            task = asyncio.create_task(ws_adapter(local_bus))
            await asyncio.sleep(0)

            await local_bus.publish(StreamStart(
                group_id=1, temp_id="t", member_id=9,
                sender_name="Bot", sender_type="bot", avatar_color="#abc",
            ))
            await local_bus.publish(StreamChunk(group_id=1, temp_id="t", delta="hello "))
            await local_bus.publish(StreamChunk(group_id=1, temp_id="t", delta="world"))
            await local_bus.publish(StreamEnd(
                group_id=1, temp_id="t", id=100, member_id=9,
                sender_name="Bot", preview="hello world",
                created_at="2026-01-01 00:00:00",
            ))
            await asyncio.sleep(0.05)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.assertEqual(len(received), 4)
        self.assertEqual(received[0], (1, "stream_start"))
        self.assertEqual(received[1], (1, "stream_chunk"))
        self.assertEqual(received[2], (1, "stream_chunk"))
        self.assertEqual(received[3], (1, "stream_end"))

    async def test_compat_broadcast_reaches_manager(self):
        """executor 风格的 bus.broadcast(group_id, dict) 也能抵达 manager。"""
        local_bus = EventBus()
        mock_manager = MagicMock()
        mock_manager.broadcast = AsyncMock()

        with patch("bus.adapter.manager", mock_manager):
            task = asyncio.create_task(ws_adapter(local_bus))
            await asyncio.sleep(0)

            await local_bus.broadcast(7, {
                "type": "tool_result",
                "temp_id": "t",
                "tool_name": "bash",
                "result": "done",
                "error": False,
            })
            await asyncio.sleep(0.05)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_manager.broadcast.assert_called_once()
        gid, payload = mock_manager.broadcast.call_args[0]
        self.assertEqual(gid, 7)
        self.assertEqual(payload["tool_name"], "bash")
        self.assertNotIn("group_id", payload)


if __name__ == "__main__":
    unittest.main()
