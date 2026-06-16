import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestration.plugins.discussion import DiscussionOrchestrator


class TestDiscussionThreadId(unittest.IsolatedAsyncioTestCase):
    """讨论的 topic（人给的首条消息）要成为一个稳定的 thread_id，
    供记忆按 topic 作用域读写。开始前无 thread，给了 topic 后稳定不变。"""

    def _spec(self):
        return {"bots": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], "rounds": 2}

    def test_unknown_group_has_no_thread(self):
        orch = DiscussionOrchestrator()
        self.assertIsNone(orch.current_thread_id(999))

    def test_no_thread_before_topic_given(self):
        orch = DiscussionOrchestrator()
        orch.begin(10, self._spec())
        self.assertIsNone(orch.current_thread_id(10))

    async def test_thread_id_stable_after_topic(self):
        orch = DiscussionOrchestrator()
        orch.begin(10, self._spec())
        await orch.dispatch(10, {"content": "吃芒果坏肚子"}, [], [])
        tid = orch.current_thread_id(10)
        self.assertIsNotNone(tid)
        # 同一讨论内多次查询返回同一个 id（稳定作用域键）
        self.assertEqual(tid, orch.current_thread_id(10))

    async def test_distinct_discussions_get_distinct_threads(self):
        orch = DiscussionOrchestrator()
        orch.begin(10, self._spec())
        await orch.dispatch(10, {"content": "选股票"}, [], [])
        first = orch.current_thread_id(10)
        orch.end(10)
        orch.begin(10, self._spec())
        await orch.dispatch(10, {"content": "吃芒果坏肚子"}, [], [])
        second = orch.current_thread_id(10)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
