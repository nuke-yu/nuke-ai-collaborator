import unittest

from stores.registry import StoreDescriptor, StoreRegistry, store_registry


class TestStoreRegistry(unittest.TestCase):
    def test_runtime_domains_are_registered(self):
        self.assertEqual(store_registry.get("group").scope, "group")
        self.assertEqual(store_registry.list(domain="memory")[0].store_id, "personal")

    def test_duplicate_identity_cannot_change_contract(self):
        registry = StoreRegistry()
        registry.register(StoreDescriptor("group", "execution", "group", "strong"))
        with self.assertRaises(ValueError):
            registry.register(StoreDescriptor("group", "other", "group", "eventual"))

    def test_unknown_store_is_explicit(self):
        with self.assertRaises(KeyError):
            StoreRegistry().get("missing")


if __name__ == "__main__":
    unittest.main()
