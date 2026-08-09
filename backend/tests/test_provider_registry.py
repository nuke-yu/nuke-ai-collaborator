import unittest

from ai.providers import (
    ProviderDescriptor,
    ProviderGovernanceError,
    ProviderRegistry,
    enforce_provider_governance,
    resolve_provider_descriptor,
)


class TestProviderDescriptor(unittest.TestCase):
    def test_known_model_is_canonical(self):
        descriptor = resolve_provider_descriptor("OpenAI", "gpt-4.1")
        self.assertEqual(descriptor.provider_id, "openai")
        self.assertEqual(descriptor.max_output_tokens, 32768)
        self.assertTrue(descriptor.supports_tools)
        self.assertGreater(descriptor.pricing_version, 0)

    def test_family_model_keeps_provider_capabilities(self):
        descriptor = resolve_provider_descriptor("claude", "claude-sonnet-4-6")
        self.assertEqual(descriptor.model_id, "claude-sonnet-4-6")
        self.assertEqual(descriptor.max_output_tokens, 64000)
        self.assertTrue(descriptor.supports_thinking)

    def test_unknown_model_gets_runtime_descriptor(self):
        descriptor = resolve_provider_descriptor("ollama", "local-qwen")
        self.assertEqual(descriptor.model_id, "local-qwen")
        self.assertIsNone(descriptor.context_window)
        self.assertTrue(descriptor.supports_tools)

    def test_conflicting_registration_is_rejected(self):
        registry = ProviderRegistry()
        first = ProviderDescriptor("test", "model", 1000, 100, True, False, False, 1)
        registry.register(first)
        with self.assertRaises(ValueError):
            registry.register(ProviderDescriptor("test", "model", 2000, 100, True, False, False, 1))

    def test_descriptor_rejects_invalid_identity(self):
        with self.assertRaises(ValueError):
            ProviderDescriptor("", "model", None, 100, True, False, False, 1)

    def test_governance_rejects_unsupported_capability_and_budget(self):
        descriptor = resolve_provider_descriptor("openai", "gpt-4o-mini")
        with self.assertRaises(ProviderGovernanceError):
            enforce_provider_governance(descriptor, require_thinking=True)
        with self.assertRaises(ProviderGovernanceError):
            enforce_provider_governance(descriptor, estimated_cost_usd=1.1, budget_usd=1.0)


if __name__ == "__main__":
    unittest.main()
