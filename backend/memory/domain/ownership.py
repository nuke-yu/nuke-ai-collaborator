"""Ownership and authority rules for canonical Group Facts."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MemoryOwnerType(StrEnum):
    GROUP = "group"
    BOT = "bot"
    PERSONAL = "personal"


class FactAuthority(StrEnum):
    USER_EXPLICIT = "user_explicit"
    PROJECT_AUTHORITATIVE = "project_authoritative"
    SYSTEM_DETERMINISTIC = "system_deterministic"
    BOT_OBSERVATION = "bot_observation"
    BOT_INFERENCE = "bot_inference"


class FactSensitivity(StrEnum):
    PUBLIC = "public"
    GROUP = "group"
    PRIVATE = "private"
    SECRET = "secret"


@dataclass(frozen=True, slots=True)
class FactAdmission:
    authority: FactAuthority
    status: str
    can_activate: bool


_SOURCE_AUTHORITY = {
    "user_explicit": FactAuthority.USER_EXPLICIT,
    "authoritative_project_source": FactAuthority.PROJECT_AUTHORITATIVE,
    "deterministic_system_state": FactAuthority.SYSTEM_DETERMINISTIC,
    "bot_observation": FactAuthority.BOT_OBSERVATION,
    "bot_reply": FactAuthority.BOT_OBSERVATION,
    "bot_inference": FactAuthority.BOT_INFERENCE,
}

_ACTIVE_AUTHORITIES = frozenset(
    {
        FactAuthority.USER_EXPLICIT,
        FactAuthority.PROJECT_AUTHORITATIVE,
        FactAuthority.SYSTEM_DETERMINISTIC,
    }
)


def admit_group_fact(
    source_type: str, sensitivity: FactSensitivity
) -> FactAdmission:
    """Classify a candidate without allowing Bot claims to become active facts."""

    try:
        authority = _SOURCE_AUTHORITY[source_type.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported Group Fact source_type: {source_type}") from exc
    if sensitivity in {FactSensitivity.PRIVATE, FactSensitivity.SECRET}:
        raise ValueError("private or secret content cannot enter Group Facts")
    can_activate = authority in _ACTIVE_AUTHORITIES
    return FactAdmission(
        authority=authority,
        status="active" if can_activate else "provisional",
        can_activate=can_activate,
    )
