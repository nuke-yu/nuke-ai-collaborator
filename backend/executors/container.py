"""Small explicit dependency container for executor plugins."""
from __future__ import annotations


class DependencyNotFound(RuntimeError):
    pass


class DependencyContainer:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def bind(self, name: str, value: object) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("dependency name is required")
        if value is None:
            raise ValueError(f"dependency {name!r} cannot be None")
        self._values[name] = value

    def unbind(self, name: str) -> None:
        self._values.pop(name, None)

    def resolve(self, name: str) -> object:
        try:
            return self._values[name]
        except KeyError as exc:
            raise DependencyNotFound(f"plugin dependency {name!r} is not configured") from exc

    def resolve_many(self, names) -> dict[str, object]:
        names = tuple(names or ())
        if len(set(names)) != len(names):
            raise ValueError("plugin dependency declarations must be unique")
        return {name: self.resolve(name) for name in names}
