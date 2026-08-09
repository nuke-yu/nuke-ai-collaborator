"""Executable metadata registry for persistence stores and their boundaries."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StoreDescriptor:
    store_id: str
    domain: str
    scope: str
    consistency: str
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.store_id.strip() or not self.domain.strip() or not self.scope.strip():
            raise ValueError("store_id, domain, and scope are required")


class StoreRegistry:
    def __init__(self) -> None:
        self._stores: dict[str, StoreDescriptor] = {}

    def register(self, descriptor: StoreDescriptor) -> None:
        existing = self._stores.get(descriptor.store_id)
        if existing is not None and existing != descriptor:
            raise ValueError(f"store already registered: {descriptor.store_id}")
        self._stores[descriptor.store_id] = descriptor

    def get(self, store_id: str) -> StoreDescriptor:
        try:
            return self._stores[store_id]
        except KeyError as exc:
            raise KeyError(f"unknown store: {store_id}") from exc

    def list(self, *, domain: str | None = None, scope: str | None = None) -> tuple[StoreDescriptor, ...]:
        return tuple(
            store for store in self._stores.values()
            if (domain is None or store.domain == domain)
            and (scope is None or store.scope == scope)
        )


store_registry = StoreRegistry()
for _descriptor in (
    StoreDescriptor("central", "identity", "central", "strong"),
    StoreDescriptor("group", "execution", "group", "strong"),
    StoreDescriptor("personal", "memory", "personal", "strong"),
):
    store_registry.register(_descriptor)
