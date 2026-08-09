"""Executable metadata registry for persistence stores and their boundaries."""
from __future__ import annotations

from dataclasses import dataclass


class StoreGovernanceError(ValueError):
    """Store metadata violates the persistence governance contract."""


@dataclass(frozen=True, slots=True)
class StoreDescriptor:
    store_id: str
    domain: str
    scope: str
    consistency: str
    version: str = "1"
    owner: str = "platform"
    canonical: bool = True
    projection_of: str | None = None
    migration_id: str = ""
    retention_policy: str = "group_lifetime"
    deletion_policy: str = "owner_lifecycle"
    backup_policy: str = "standard"

    def __post_init__(self) -> None:
        if not self.store_id.strip() or not self.domain.strip() or not self.scope.strip():
            raise ValueError("store_id, domain, and scope are required")
        if not self.owner.strip() or not self.migration_id.strip():
            raise StoreGovernanceError("owner and migration_id are required")
        if not self.retention_policy.strip() or not self.deletion_policy.strip() or not self.backup_policy.strip():
            raise StoreGovernanceError("retention, deletion, and backup policies are required")
        if self.canonical and self.projection_of is not None:
            raise StoreGovernanceError("canonical stores cannot declare projection_of")
        if not self.canonical and not (self.projection_of or "").strip():
            raise StoreGovernanceError("projection stores must declare projection_of")

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "store_id": self.store_id,
            "domain": self.domain,
            "scope": self.scope,
            "consistency": self.consistency,
            "version": self.version,
            "owner": self.owner,
            "canonical": self.canonical,
            "projection_of": self.projection_of,
            "migration_id": self.migration_id,
            "retention_policy": self.retention_policy,
            "deletion_policy": self.deletion_policy,
            "backup_policy": self.backup_policy,
        }


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

    def governance_report(self) -> list[dict[str, str | bool | None]]:
        """Return a stable audit view suitable for startup checks and admin tooling."""
        return [store.to_dict() for store in sorted(self._stores.values(), key=lambda item: item.store_id)]


store_registry = StoreRegistry()
for _descriptor in (
    StoreDescriptor("central", "identity", "central", "strong", migration_id="central_schema_v1", deletion_policy="account_lifecycle"),
    StoreDescriptor("group", "execution", "group", "strong", migration_id="group_schema_v1"),
    StoreDescriptor("personal", "memory", "personal", "strong", migration_id="personal_vault_v2", deletion_policy="user_requested"),
    StoreDescriptor("artifacts", "artifact", "group", "strong", migration_id="group_artifacts_v1", retention_policy="artifact_policy", deletion_policy="soft_delete_then_retention"),
    StoreDescriptor("timeline", "observability", "group", "eventual", migration_id="timeline_projection_v1", canonical=False, projection_of="group"),
    StoreDescriptor("model_usage", "billing", "group", "strong", migration_id="model_usage_ledger_v1", retention_policy="usage_90_days", deletion_policy="audit_hold"),
):
    store_registry.register(_descriptor)
