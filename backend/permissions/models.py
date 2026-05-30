from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio


@dataclass
class Rule:
    tool_pattern: str
    args_pattern: str = ""
    action: str = "allow"   # "allow" | "deny"
    id: int | None = None   # DB row id; None for in-memory once-rules


@dataclass
class Ruleset:
    rules: list[Rule] = field(default_factory=list)
    mode: str = "default"   # "default" | "bypassPermissions" | "dontAsk"


@dataclass
class _PendingRequest:
    future: "asyncio.Future[tuple[bool, str]]"
    bot_id: int
    group_id: int
    tool_name: str
    arguments: dict
