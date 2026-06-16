"""chromadb 的 Rust 绑定打不开 DB 时抛 pyo3_runtime.PanicException（BaseException 子类）。
内存层所有 fail-soft 都是 except Exception，接不住 BaseException，会把整轮 bot 干掉
（recall 在 setup_session 里）。_get_collection 在唯一入口把这类库级崩溃转成普通
Exception，让下游 fail-soft 正常降级；真正的控制流异常（中断/取消/退出）仍原样抛出。
"""
import asyncio
import unittest
from unittest.mock import patch

import ai.memory as memory


class _FakePanic(BaseException):
    """模拟 pyo3_runtime.PanicException：BaseException 子类，非 Exception。"""
    __module__ = "pyo3_runtime"
    __qualname__ = "PanicException"


class TestChromaPanicFailSoft(unittest.TestCase):
    def setUp(self):
        memory._chroma_client = None
        memory._chroma_collection = None

    def tearDown(self):
        memory._chroma_client = None
        memory._chroma_collection = None

    def test_panic_converted_to_exception(self):
        # PersistentClient panic（BaseException）→ _get_collection 抛普通 Exception（可被下游接住）
        with patch("chromadb.PersistentClient", side_effect=_FakePanic("range start index 10 ...")):
            with self.assertRaises(Exception) as ctx:
                memory._get_collection()
        self.assertIsInstance(ctx.exception, Exception)  # 而非 BaseException-only
        self.assertNotIsInstance(ctx.exception, _FakePanic)
        # 失败后不缓存坏状态，下次可重试
        self.assertIsNone(memory._chroma_collection)

    def test_retrieve_relevant_failsoft_on_panic(self):
        # 召回路径遇到 panic → 返回 []（bot 照常回话，只是没有长期记忆），不冒泡
        with patch("chromadb.PersistentClient", side_effect=_FakePanic("boom")):
            out = asyncio.run(memory.retrieve_relevant(7, 3, "hello"))
        self.assertEqual(out, [])

    def test_control_flow_exceptions_not_swallowed(self):
        for exc in (KeyboardInterrupt, SystemExit):
            with self.subTest(exc=exc):
                memory._chroma_client = None
                memory._chroma_collection = None
                with patch("chromadb.PersistentClient", side_effect=exc()):
                    with self.assertRaises(exc):
                        memory._get_collection()


if __name__ == "__main__":
    unittest.main()
