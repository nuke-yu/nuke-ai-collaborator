"""Composition adapter for the host Personal Vault policy store."""
from __future__ import annotations


class LegacyPersonalVaultPolicyAdapter:
    async def evaluate_rule(self, **kwargs):
        from ai.personal_vault import evaluate_access_control_rule
        return await evaluate_access_control_rule(**kwargs)

    async def record_audit(self, **kwargs) -> None:
        from ai.personal_vault import record_acl_audit_event
        await record_acl_audit_event(**kwargs)
