"""Per-agent file observations for read-before-mutate protection.

The store is intentionally process-local: an Agent session and its workspace
tools run in one Worker.  Group and session are part of the key so an
observation can never authorize another tenant or another Agent run.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path


class UnobservedFileMutationError(RuntimeError):
    """The Agent has not observed the current file version before mutation."""


@dataclass(frozen=True, slots=True)
class FileObservation:
    session_id: str
    group_id: int | None
    path: str
    version: str | None
    exists: bool


def file_version(path: Path) -> str:
    """Return a content version without exposing file contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ObservationStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._observations: dict[tuple[str, int | None, str], FileObservation] = {}

    @staticmethod
    def _key(session_id: str, group_id: int | None, path: Path) -> tuple[str, int | None, str]:
        return session_id, group_id, str(path.resolve())

    def record(
        self, *, session_id: str, group_id: int | None, path: Path,
        version: str | None, exists: bool,
    ) -> FileObservation:
        observation = FileObservation(
            session_id=session_id,
            group_id=group_id,
            path=str(path.resolve()),
            version=version,
            exists=exists,
        )
        with self._lock:
            self._observations[self._key(session_id, group_id, path)] = observation
        return observation

    def get(self, *, session_id: str, group_id: int | None, path: Path) -> FileObservation | None:
        with self._lock:
            return self._observations.get(self._key(session_id, group_id, path))

    def assert_mutation_allowed(
        self, *, session_id: str | None, group_id: int | None, path: Path,
        current_version: str | None, exists: bool, allow_create: bool,
    ) -> None:
        # Direct/manual callers without an Agent session predate this policy.
        # They have no safe owner key, so the compatibility path remains
        # intentionally unchanged; all tool-loop calls carry session_id.
        if not session_id:
            return
        observation = self.get(session_id=session_id, group_id=group_id, path=path)
        if not exists and allow_create:
            if observation is not None and observation.exists:
                raise UnobservedFileMutationError(
                    f"cannot create {path}: it was observed as present; read it again"
                )
            return
        if observation is None or not observation.exists:
            raise UnobservedFileMutationError(
                f'edit requires reading "{path}" first'
            )
        if observation.version != current_version:
            raise UnobservedFileMutationError(
                f'file "{path}" changed after it was observed; read it again'
            )

    def clear_session(self, session_id: str, group_id: int | None = None) -> None:
        with self._lock:
            self._observations = {
                key: value for key, value in self._observations.items()
                if value.session_id != session_id
                or (group_id is not None and value.group_id != group_id)
            }


store = ObservationStore()
