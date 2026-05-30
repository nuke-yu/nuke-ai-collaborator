"""
tests/test_ai_client_pool.py — DFT-033 共享连接池客户端

验证 ai.client 不再 per-call 新建 httpx.AsyncClient，而是复用一个进程级
连接池客户端，并能在应用关闭时显式释放。
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import ai.client as client_mod


class TestSharedClient(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await client_mod.aclose_client()

    async def asyncTearDown(self):
        await client_mod.aclose_client()

    async def test_get_client_reuses_same_instance(self):
        c1 = client_mod._get_client()
        c2 = client_mod._get_client()
        self.assertIs(c1, c2)
        self.assertIsInstance(c1, httpx.AsyncClient)
        self.assertFalse(c1.is_closed)

    async def test_aclose_client_closes_and_resets(self):
        c1 = client_mod._get_client()
        await client_mod.aclose_client()
        self.assertTrue(c1.is_closed)
        c2 = client_mod._get_client()
        self.assertIsNot(c1, c2)
        self.assertFalse(c2.is_closed)

    async def test_get_client_recreates_if_closed(self):
        c1 = client_mod._get_client()
        await c1.aclose()
        c2 = client_mod._get_client()
        self.assertIsNot(c1, c2)
        self.assertFalse(c2.is_closed)

    async def test_shared_client_cm_does_not_close(self):
        async with client_mod._shared_client() as c:
            self.assertIsInstance(c, httpx.AsyncClient)
            self.assertFalse(c.is_closed)
        # 退出 with 块后客户端仍存活（生命周期归 aclose_client 管）
        self.assertFalse(c.is_closed)
        self.assertIs(c, client_mod._get_client())


if __name__ == "__main__":
    unittest.main()
