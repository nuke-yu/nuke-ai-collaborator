"""长度前缀 JSON 分帧 —— 跨平台、依赖纯净（仅 stdlib，不 import 任何项目模块）。

线格式：4 字节大端无符号长度 + UTF-8 JSON 体。这是每条 IPC 隧道的共享内核；
平台只在底层传输（UDS vs named pipe）上不同，分帧/序列化完全一致。
"""
import asyncio
import json

# 防御一个损坏的长度前缀把内存撑爆。够大以容纳压缩前的上下文快照。
_MAX_FRAME = 64 * 1024 * 1024  # 64 MiB


async def send_msg(writer: asyncio.StreamWriter, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    writer.write(len(body).to_bytes(4, "big") + body)
    await writer.drain()


async def recv_msg(reader: asyncio.StreamReader) -> dict:
    header = await reader.readexactly(4)
    n = int.from_bytes(header, "big")
    if n > _MAX_FRAME:
        raise ValueError(f"IPC frame too large: {n} bytes (max {_MAX_FRAME})")
    body = await reader.readexactly(n)
    return json.loads(body)
