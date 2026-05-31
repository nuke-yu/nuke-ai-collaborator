"""CELL-08: runtime/ipc transport abstraction.

Exercises the REAL platform-native transport (UDS on this Unix host) end to end
through the shared framing layer, plus the protocol schema. The Windows
named-pipe backend (transport_win) cannot run on Unix CI — it is validated
separately on a Windows runner (see transport_win.py docstring).
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import ipc
from runtime.ipc import framing, protocol


class TestFraming(unittest.IsolatedAsyncioTestCase):
    async def test_length_prefixed_roundtrip_via_pipe(self):
        # os.pipe-backed StreamReader/Writer to test framing without a transport.
        loop = asyncio.get_running_loop()
        rsock, wsock = os.pipe()
        r = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(r), os.fdopen(rsock, "rb", 0))
        w_transport, w_proto = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, os.fdopen(wsock, "wb", 0))
        w = asyncio.StreamWriter(w_transport, w_proto, r, loop)

        msg = {"type": "user_message", "group_id": 7, "content": "héllo 世界", "trace_id": "abc"}
        await framing.send_msg(w, msg)
        got = await asyncio.wait_for(framing.recv_msg(r), timeout=5)
        self.assertEqual(got, msg)
        w.close()

    async def test_oversized_frame_rejected(self):
        r = asyncio.StreamReader()
        # Hand-craft a header claiming a frame larger than the cap.
        r.feed_data((framing._MAX_FRAME + 1).to_bytes(4, "big"))
        r.feed_eof()
        with self.assertRaises(ValueError):
            await framing.recv_msg(r)


class TestNativeTransport(unittest.IsolatedAsyncioTestCase):
    async def test_serve_connect_roundtrip(self):
        addr = ipc.make_addr(f"test_{os.getpid()}")
        server_saw = []

        async def handler(reader, writer):
            req = await ipc.recv_msg(reader)
            server_saw.append(req)
            await ipc.send_msg(writer, ipc.protocol.envelope(
                protocol.BROADCAST, group_id=req["group_id"], trace_id=req.get("trace_id"),
                payload={"type": "typing", "sender_name": "DevBot"},
            ))
            writer.close()

        server = await ipc.serve(addr, handler)
        try:
            reader, writer = await ipc.connect(addr)
            await ipc.send_msg(writer, ipc.protocol.envelope(
                protocol.USER_MESSAGE, group_id=42, trace_id="t-1", content="hi",
            ))
            reply = await asyncio.wait_for(ipc.recv_msg(reader), timeout=5)
            writer.close()
        finally:
            server.close()
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=2)
            except Exception:
                pass
            if not sys.platform == "win32" and os.path.exists(addr):
                os.unlink(addr)

        # downstream reached worker-side handler with routing header intact
        self.assertEqual(server_saw[0]["type"], protocol.USER_MESSAGE)
        self.assertEqual(server_saw[0]["group_id"], 42)
        self.assertEqual(server_saw[0]["trace_id"], "t-1")
        # upstream broadcast came back framed correctly
        self.assertEqual(reply["type"], protocol.BROADCAST)
        self.assertEqual(reply["group_id"], 42)
        self.assertEqual(reply["payload"]["type"], "typing")


class TestProtocolSchema(unittest.TestCase):
    def test_channel_partitions(self):
        self.assertIn(protocol.USER_MESSAGE, protocol.DOWNSTREAM)
        self.assertIn(protocol.BROADCAST, protocol.UPSTREAM)
        self.assertFalse(protocol.DOWNSTREAM & protocol.UPSTREAM)  # disjoint

    def test_envelope_carries_routing_header(self):
        e = protocol.envelope(protocol.USER_MESSAGE, group_id=5, trace_id="x", content="c")
        self.assertEqual(e, {"type": "user_message", "group_id": 5,
                             "trace_id": "x", "content": "c"})


if __name__ == "__main__":
    unittest.main()
