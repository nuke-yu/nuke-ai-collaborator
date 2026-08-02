"""Central observability payload policy enforcement."""

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observability.event_policy import (
    EffectClass,
    EventClass,
    EventPolicy,
    PayloadPolicy,
    RetentionPolicy,
)
from observability.payload_policy import ARTIFACT_KEY, SUMMARY_KEY, prepare_payload


class TestPayloadPolicy(unittest.TestCase):
    def test_redacts_nested_secrets_before_inline_storage(self):
        prepared = prepare_payload("permission_requested", {
            "permission_id": "perm_1",
            "nested": {
                "authorization": "Bearer abcdefghijklmnopqrstuvwxyz123456",
                "api_key": "plain-secret-value",
            },
        })
        self.assertIsNone(prepared.artifact)
        self.assertIn("[REDACTED]", prepared.payload["nested"]["authorization"])
        self.assertEqual(prepared.payload["nested"]["api_key"], "[REDACTED]")

    def test_large_summary_payload_becomes_bounded_artifact_reference(self):
        prepared = prepare_payload("llm_response", {"content": "x" * 10_000})
        self.assertIsNotNone(prepared.artifact)
        self.assertIn(ARTIFACT_KEY, prepared.payload)
        self.assertIn(SUMMARY_KEY, prepared.payload)
        self.assertNotIn("content", prepared.payload)
        self.assertLess(len(prepared.payload[SUMMARY_KEY]), 2_100)
        self.assertEqual(
            prepared.payload[ARTIFACT_KEY]["sha256"],
            prepared.artifact.content_sha256,
        )

    def test_reference_only_policy_never_inlines_payload(self):
        policy = EventPolicy(
            event_classes=(EventClass.AUDIT,),
            effect_classes=(EffectClass.DURABLE_WRITE,),
            retention=RetentionPolicy.GROUP_LIFETIME,
            payload_policy=PayloadPolicy.REFERENCE_ONLY,
            business_significant=True,
            allow_sampling=False,
            reason="test reference-only enforcement",
        )
        prepared = prepare_payload("custom_artifact", {"value": "small"}, policy=policy)
        self.assertIsNotNone(prepared.artifact)
        self.assertNotIn("value", prepared.payload)
        self.assertEqual(prepared.artifact.payload_policy, "reference_only")
