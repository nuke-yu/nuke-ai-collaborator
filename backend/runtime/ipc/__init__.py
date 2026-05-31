"""IPC 传输抽象（V3 §10.4）。

平台原生传输藏在薄接口后：Unix → UDS，Windows → Named Pipe。
只有 serve / connect / make_addr 三个函数按平台分叉；framing 与 protocol 跨平台共享。

业务侧（supervisor / worker）只 import 本包，永远不碰平台细节：

    addr = ipc.make_addr("worker_0")
    server = await ipc.serve(addr, on_conn)        # on_conn: async def(reader, writer)
    reader, writer = await ipc.connect(addr)
    await ipc.send_msg(writer, payload)
    msg = await ipc.recv_msg(reader)
"""
import sys

from .framing import send_msg, recv_msg
from . import protocol

if sys.platform == "win32":
    from .transport_win import serve, connect, make_addr
else:
    from .transport_unix import serve, connect, make_addr

__all__ = ["send_msg", "recv_msg", "protocol", "serve", "connect", "make_addr"]
