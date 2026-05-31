r"""Windows 传输 —— 命名管道（Named Pipe），经 Proactor 事件循环。

镜像 transport_unix 的接口（serve / connect / make_addr），但走 \\.\pipe\... 命名管道
（Windows 原生 IPC）。同样返回 asyncio StreamReader/StreamWriter，使分帧层与业务代码
与 Unix 路径完全一致。由 runtime.ipc 在 win32 平台选用。

⚠️ 验证：本路径无法在 Unix CI 上运行，**必须**在 Windows runner 上作为 CELL-08 的
验收项跑通。命名管道接线若不顺，文档化的逃生口是 loopback TCP（127.0.0.1 + 启动 token）——
该替换**只动本文件**。Windows 默认即 ProactorEventLoop（命名管道所需），无需手动设置。
"""
import asyncio


def make_addr(name: str) -> str:
    return rf"\\.\pipe\nuke_{name}"


class _PipeServer:
    """对 start_serving_pipe() 的最小 asyncio.Server 风格包装。"""

    def __init__(self, servers):
        self._servers = servers

    def close(self):
        for s in self._servers:
            s.close()

    async def wait_closed(self):
        # 命名管道 server 无 awaitable 关闭；best-effort no-op。
        return None


async def serve(addr: str, handler):
    """handler: async def(reader, writer)。返回带 close() 的 server 包装。"""
    loop = asyncio.get_running_loop()

    def factory():
        reader = asyncio.StreamReader()
        # StreamReaderProtocol 在每条连接建立时调用 handler(reader, writer)。
        return asyncio.StreamReaderProtocol(reader, handler)

    servers = await loop.start_serving_pipe(factory, addr)
    return _PipeServer(servers)


async def connect(addr: str):
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.create_pipe_connection(lambda: protocol, addr)
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return reader, writer
