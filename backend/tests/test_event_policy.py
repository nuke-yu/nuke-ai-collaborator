"""Business-significant event policy classification tests."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observability import (
    EffectClass,
    EventClass,
    PayloadPolicy,
    RetentionPolicy,
    classify_event,
    classify_tool_effect,
    enrich_event_payload,
)


class TestToolEffectPolicy(unittest.TestCase):
    def test_read_file_is_sampled_diagnostic(self):
        policy = classify_tool_effect("read_file", {"path": "README.md"})
        self.assertEqual(policy.event_classes, (EventClass.DIAGNOSTIC,))
        self.assertEqual(policy.effect_classes, (EffectClass.READ,))
        self.assertFalse(policy.business_significant)
        self.assertTrue(policy.allow_sampling)

    def test_write_file_is_durable_audit_timeline(self):
        policy = classify_tool_effect("write_file", {"path": "src/app.py"})
        self.assertIn(EventClass.AUDIT, policy.event_classes)
        self.assertIn(EventClass.TIMELINE, policy.event_classes)
        self.assertEqual(policy.effect_classes, (EffectClass.DURABLE_WRITE,))
        self.assertEqual(policy.retention, RetentionPolicy.GROUP_LIFETIME)
        self.assertTrue(policy.business_significant)
        self.assertFalse(policy.allow_sampling)

    def test_sensitive_read_is_audited(self):
        workspace_secret = classify_tool_effect("read_file", {"path": ".env"})
        host_read = classify_tool_effect("read_local_file", {"path": "/tmp/public.txt"})
        shell_secret = classify_tool_effect("run_shell", {"cmd": "cat ~/.ssh/id_rsa"})
        for policy in (workspace_secret, host_read, shell_secret):
            self.assertEqual(policy.effect_classes, (EffectClass.READ,))
            self.assertIn(EventClass.AUDIT, policy.event_classes)
            self.assertEqual(policy.retention, RetentionPolicy.SECURITY_AUDIT)
            self.assertTrue(policy.business_significant)

    def test_shell_ls_is_read_only_diagnostic(self):
        policy = classify_tool_effect("run_shell", {"cmd": "ls -la"})
        self.assertEqual(policy.effect_classes, (EffectClass.READ,))
        self.assertEqual(policy.event_classes, (EventClass.DIAGNOSTIC,))
        self.assertFalse(policy.business_significant)

    def test_shell_pytest_is_verification_evidence(self):
        policy = classify_tool_effect("run_shell", {"cmd": "python3 -m pytest"})
        self.assertEqual(policy.effect_classes, (EffectClass.VERIFICATION,))
        direct = classify_tool_effect("run_shell", {"cmd": "pytest -q"})
        self.assertEqual(direct.effect_classes, (EffectClass.VERIFICATION,))
        self.assertIn(EventClass.TIMELINE, direct.event_classes)

    def test_package_test_and_build_are_verification(self):
        for command in ("npm test", "npm run build", "pnpm run lint"):
            with self.subTest(command=command):
                policy = classify_tool_effect("run_shell", {"cmd": command})
                self.assertEqual(policy.effect_classes, (EffectClass.VERIFICATION,))

    def test_git_status_is_read_but_git_push_is_external(self):
        status = classify_tool_effect("run_shell", {"cmd": "git status -sb"})
        push = classify_tool_effect("run_shell", {"cmd": "git push origin main"})
        self.assertEqual(status.effect_classes, (EffectClass.READ,))
        self.assertEqual(push.effect_classes, (EffectClass.EXTERNAL_WRITE,))
        self.assertIn(EventClass.AUDIT, push.event_classes)

    def test_write_options_override_read_oriented_command(self):
        sed = classify_tool_effect("run_shell", {"cmd": "sed -i s/a/b/ file.txt"})
        find = classify_tool_effect("run_shell", {"cmd": "find . -name '*.tmp' -delete"})
        self.assertEqual(sed.effect_classes, (EffectClass.DURABLE_WRITE,))
        self.assertEqual(find.effect_classes, (EffectClass.DURABLE_WRITE,))

    def test_compound_shell_is_conservatively_audited(self):
        policy = classify_tool_effect("run_shell", {"cmd": "git status && git push"})
        self.assertEqual(policy.effect_classes, (EffectClass.UNKNOWN,))
        self.assertEqual(policy.event_classes, (EventClass.AUDIT,))
        self.assertTrue(policy.business_significant)

    def test_unknown_plugin_tool_fails_closed_for_audit(self):
        policy = classify_tool_effect("community_magic", {})
        self.assertEqual(policy.effect_classes, (EffectClass.UNKNOWN,))
        self.assertEqual(policy.retention, RetentionPolicy.SECURITY_AUDIT)
        self.assertIn(EventClass.AUDIT, policy.event_classes)
        self.assertFalse(policy.allow_sampling)


class TestEventPolicy(unittest.TestCase):
    def test_session_start_is_lifecycle_timeline(self):
        policy = classify_event("session_start", {"user_content": "hello"})
        self.assertEqual(policy.effect_classes, (EffectClass.LIFECYCLE,))
        self.assertEqual(policy.event_classes, (EventClass.TIMELINE,))
        self.assertTrue(policy.business_significant)

    def test_context_evidence_injection_is_timeline_without_claiming_adoption(self):
        policy = classify_event("context_evidence_injected", {"causal_usage": False})
        self.assertEqual(policy.effect_classes, (EffectClass.LEARNING,))
        self.assertEqual(policy.event_classes, (EventClass.TIMELINE,))
        self.assertEqual(policy.retention, RetentionPolicy.EXECUTION_90_DAYS)

    def test_unknown_event_is_sampled_diagnostic(self):
        policy = classify_event("internal_cache_tick", {})
        self.assertEqual(policy.event_classes, (EventClass.DIAGNOSTIC,))
        self.assertEqual(policy.payload_policy, PayloadPolicy.SUMMARY)
        self.assertFalse(policy.business_significant)
        self.assertTrue(policy.allow_sampling)

    def test_session_recovery_status_is_audit_timeline(self):
        policy = classify_event("session_status", {"status": "needs_review"})
        self.assertEqual(policy.effect_classes, (EffectClass.RECOVERY,))
        self.assertIn(EventClass.AUDIT, policy.event_classes)
        self.assertIn(EventClass.TIMELINE, policy.event_classes)

    def test_permission_decision_is_unsampled_security_audit(self):
        policy = classify_event("permission_approved", {"permission_id": "perm_1"})
        self.assertEqual(policy.effect_classes, (EffectClass.AUTHORIZATION,))
        self.assertEqual(policy.retention, RetentionPolicy.SECURITY_AUDIT)
        self.assertIn(EventClass.AUDIT, policy.event_classes)
        self.assertIn(EventClass.TIMELINE, policy.event_classes)
        self.assertFalse(policy.allow_sampling)

    def test_workflow_gate_and_stage_events_are_business_significant(self):
        gate = classify_event("gate_requested", {})
        stage = classify_event("stage_completed", {})
        self.assertEqual(gate.effect_classes, (EffectClass.AUTHORIZATION,))
        self.assertEqual(gate.retention, RetentionPolicy.SECURITY_AUDIT)
        self.assertFalse(gate.allow_sampling)
        self.assertIn(EffectClass.VERIFICATION, stage.effect_classes)
        self.assertIn(EventClass.METRIC, stage.event_classes)
        self.assertTrue(stage.business_significant)

    def test_enrichment_is_non_mutating_and_idempotent(self):
        original = {"tool_name": "write_file", "arguments": {"path": "x.py"}}
        enriched = enrich_event_payload("tool_call", original, trace_id="trace-1")
        self.assertNotIn("_observability", original)
        metadata = enriched["_observability"]
        self.assertTrue(metadata["event_id"].startswith("evt_"))
        self.assertEqual(metadata["trace_id"], "trace-1")
        self.assertTrue(metadata["business_significant"])
        self.assertIn("audit", metadata["classes"])

        replayed = enrich_event_payload("tool_call", enriched, trace_id="trace-2")
        self.assertEqual(
            replayed["_observability"]["event_id"], metadata["event_id"]
        )
        self.assertEqual(replayed["_observability"]["trace_id"], "trace-1")


if __name__ == "__main__":
    unittest.main()
