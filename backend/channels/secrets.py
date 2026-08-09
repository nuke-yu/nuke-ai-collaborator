"""Explicit secret/environment boundary for connector subprocesses."""
from __future__ import annotations

import os
import re
from typing import Mapping, Protocol, Sequence


class ChannelSecretResolver(Protocol):
    def resolve(self, channel_instance_id: str, names: Sequence[str]) -> Mapping[str, str]: ...


class EnvironmentSecretResolver:
    """Resolve only explicitly named environment variables.

    The child never receives the parent environment wholesale.  Deployments can
    replace this resolver with a vault-backed implementation without changing
    the process protocol.
    """

    def __init__(self, values: Mapping[str, str] | None = None):
        self._values = dict(os.environ if values is None else values)

    def resolve(self, channel_instance_id: str, names: Sequence[str]) -> Mapping[str, str]:
        del channel_instance_id
        return {name: self._values[name] for name in names if name in self._values}


def validate_env_names(names: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(str(name).strip() for name in names)
    if len(normalized) > 64 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) for name in normalized):
        raise ValueError("manifest contains invalid environment variable names")
    if len(set(normalized)) != len(normalized):
        raise ValueError("manifest contains duplicate environment variable names")
    return normalized
