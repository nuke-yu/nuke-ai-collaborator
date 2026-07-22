"""Isolation boundary carried by every Memory command and query."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Sequence


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated caller identity principal for ACL access control."""
    actor_id: str
    user_id: int | None = None
    bot_id: int | None = None
    group_ids: set[int] = field(default_factory=set)

    @classmethod
    def user(cls, user_id: int, group_ids: Sequence[int] | set[int] = ()) -> "Principal":
        return cls(actor_id=f"user:{user_id}", user_id=user_id, group_ids=set(group_ids))

    @classmethod
    def bot(cls, bot_id: int, group_id: int | None = None, group_ids: Sequence[int] | set[int] = ()) -> "Principal":
        gset = set(group_ids)
        if group_id is not None:
            gset.add(group_id)
        return cls(actor_id=f"bot:{bot_id}", bot_id=bot_id, group_ids=gset)


class ScopeKind(StrEnum):
    GROUP = "group"
    BOT = "bot"
    PERSONAL = "personal"


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """Immutable authorization and storage scope.

    ``group_id`` is mandatory for group and bot operations because group
    databases are the physical tenant boundary. Personal knowledge requires an
    authenticated ``user_id`` and may exist outside a group; it can only enter
    one through an explicit projection use case.
    """

    kind: ScopeKind
    group_id: int | None
    actor_id: str
    bot_id: int | None = None
    user_id: int | None = None
    thread_id: str | None = None
    run_id: str | None = None
    purpose: str = "task_execution"

    def __post_init__(self) -> None:
        if self.kind in (ScopeKind.GROUP, ScopeKind.BOT) and (
            not isinstance(self.group_id, int)
            or isinstance(self.group_id, bool)
            or self.group_id <= 0
        ):
            raise ValueError("group and bot scopes require a positive group_id")
        if self.group_id is not None and (
            not isinstance(self.group_id, int)
            or isinstance(self.group_id, bool)
            or self.group_id <= 0
        ):
            raise ValueError("group_id must be a positive integer when provided")
        if not self.actor_id.strip():
            raise ValueError("actor_id is required")
        if not self.purpose.strip():
            raise ValueError("purpose is required")
        if self.kind is ScopeKind.BOT and (
            not isinstance(self.bot_id, int)
            or isinstance(self.bot_id, bool)
            or self.bot_id <= 0
        ):
            raise ValueError("bot scope requires a positive bot_id")
        if self.kind is ScopeKind.PERSONAL and (
            not isinstance(self.user_id, int)
            or isinstance(self.user_id, bool)
            or self.user_id <= 0
        ):
            raise ValueError("personal scope requires a positive user_id")

    @classmethod
    def group(cls, *, group_id: int, actor_id: str, **context: object) -> "MemoryScope":
        return cls(kind=ScopeKind.GROUP, group_id=group_id, actor_id=actor_id, **context)

    @classmethod
    def bot(
        cls, *, group_id: int, bot_id: int, actor_id: str, **context: object
    ) -> "MemoryScope":
        return cls(
            kind=ScopeKind.BOT,
            group_id=group_id,
            bot_id=bot_id,
            actor_id=actor_id,
            **context,
        )

    @classmethod
    def personal(
        cls, *, user_id: int, actor_id: str, group_id: int | None = None, **context: object
    ) -> "MemoryScope":
        return cls(
            kind=ScopeKind.PERSONAL,
            group_id=group_id,
            user_id=user_id,
            actor_id=actor_id,
            **context,
        )

    def storage_partition(self) -> tuple[int | None, ScopeKind, int | None]:
        """Return a non-ambiguous partition key for adapters and audit logs."""
        subject_id = self.bot_id if self.kind is ScopeKind.BOT else self.user_id
        return self.group_id, self.kind, subject_id
