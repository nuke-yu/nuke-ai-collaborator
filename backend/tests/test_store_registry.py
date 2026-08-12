import unittest

from stores.registry import StoreDescriptor, StoreGovernanceError, StoreRegistry, store_registry


class TestStoreRegistry(unittest.TestCase):
    def test_runtime_domains_are_registered(self):
        self.assertEqual(store_registry.get("group").scope, "group")
        self.assertEqual(store_registry.list(domain="memory")[0].store_id, "personal")
        self.assertEqual(store_registry.get("artifacts").deletion_policy, "soft_delete_then_retention")
        self.assertEqual(store_registry.get("timeline").projection_of, "group")

    def test_governance_report_is_complete_and_stable(self):
        report = store_registry.governance_report()
        self.assertEqual([item["store_id"] for item in report], sorted(item["store_id"] for item in report))
        self.assertTrue(all(item["owner"] and item["migration_id"] for item in report))
        self.assertTrue(all(item["retention_policy"] and item["deletion_policy"] for item in report))

    def test_duplicate_identity_cannot_change_contract(self):
        registry = StoreRegistry()
        registry.register(StoreDescriptor("group", "execution", "group", "strong", migration_id="group_schema_v1"))
        with self.assertRaises(ValueError):
            registry.register(StoreDescriptor("group", "other", "group", "eventual", migration_id="other_schema_v1"))

    def test_unknown_store_is_explicit(self):
        with self.assertRaises(KeyError):
            StoreRegistry().get("missing")

    def test_projection_and_canonical_contracts_are_explicit(self):
        with self.assertRaises(StoreGovernanceError):
            StoreDescriptor("invalid", "test", "group", "strong", migration_id="m1", canonical=True, projection_of="group")
        with self.assertRaises(StoreGovernanceError):
            StoreDescriptor("invalid", "test", "group", "eventual", migration_id="m1", canonical=False)

    def test_operation_requires_bound_executor_and_returns_governed_plan(self):
        registry = StoreRegistry()
        registry.register(StoreDescriptor("demo", "test", "group", "strong", migration_id="demo_v1"))
        with self.assertRaises(StoreGovernanceError):
            import asyncio
            asyncio.run(registry.execute("demo", "backup"))
        registry.bind_executor("demo", backup=lambda destination: {"destination": destination})
        import asyncio
        result = asyncio.run(registry.execute("demo", "backup", destination="/tmp/demo.bak"))
        self.assertEqual(result["migration_id"], "demo_v1")
        self.assertEqual(result["result"]["destination"], "/tmp/demo.bak")

    def test_audit_hold_blocks_delete_plan(self):
        with self.assertRaises(StoreGovernanceError):
            store_registry.operation_plan("model_usage", "delete")


if __name__ == "__main__":
    unittest.main()
