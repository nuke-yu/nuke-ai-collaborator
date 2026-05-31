"""Unix 传输 —— AF_UNIX 域套接字（Mac / Linux）。

返回 asyncio StreamReader/StreamWriter，与 transport_win 接口一致，使分帧层与所有
业务代码跨平台无差异。由 runtime.ipc 在非 Windows 平台选用。
"""
import asyncio
import os
import tempfile

# 默认 /tmp；可用 NUKE_IPC_DIR 覆盖（容器/受限环境）。
_SOCK_DIR = os.environ.get("NUKE_IPC_DIR", tempfile.gettempdir())


def make_addr(name: str) -> str:
    return os.path.join(_SOCK_DIR, f"nuke_{name}.sock")


async def serve(addr: str, handler):
    """handler: async def(reader, writer)。返回 asyncio server（有 close/wait_closed）。"""
    if os.path.exists(addr):
        os.unlink(addr)
    return await asyncio.start_unix_server(handler, path=addr)


async def connect(addr: str):
    return await asyncio.open_unix_connection(path=addr)
