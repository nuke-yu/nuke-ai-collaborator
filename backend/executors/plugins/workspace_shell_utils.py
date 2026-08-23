"""Pure shell command resource helpers."""
from __future__ import annotations

import re
import socket

INTERCEPT_PORTS = {"8000", "8080", "3000", "5000", "5173", "80"}


def allocate_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def intercept_command_ports(cmd: str, env: dict, *, allocate=allocate_free_port):
    allocated = intercepted = None
    for port in sorted(INTERCEPT_PORTS, key=len, reverse=True):
        pattern = r"(?<![\d\-])" + re.escape(port) + r"(?![\d])"
        if re.search(pattern, cmd):
            intercepted, allocated = port, allocate()
            env["APP_PORT"] = env["PORT"] = str(allocated)
            cmd = re.sub(pattern, str(allocated), cmd)
            break
    return cmd, intercepted, allocated


def wrap_command_with_limits(cmd: str, limit_bytes: int, *, is_windows: bool) -> str:
    return cmd if is_windows else f"ulimit -v {limit_bytes // 1024} 2>/dev/null; {cmd}"
