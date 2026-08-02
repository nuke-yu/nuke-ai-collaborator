import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.application.references import (
    add_tool_ref_parameter,
    experience_ref,
    file_skill_ref,
    skill_ref,
    validate_tool_refs,
)
from memory.application.causal_usage import (
    collect_causal_usages,
    verification_for_usage,
)
from memory.domain import MemoryScope, UsageKind, UsageState


class TestMemoryRefs(unittest.TestCase):
    def test_stable_refs_are_derived_from_canonical_identity(self):
        self.assertEqual(experience_ref("exp:abc"), "exp:abc")
        self.assertEqual(skill_ref("skill:def", 3), "skill:def@v3")
        with self.assertRaisesRegex(ValueError, "experience"):
            experience_ref('memory_ref="exp:forged"')
        first = file_skill_ref("group", "review", "body")
        self.assertEqual(first, file_skill_ref("group", "review", "body"))
        self.assertNotEqual(first, file_skill_ref("group", "review", "changed"))

    def test_tool_evidence_links_distinguish_memory_and_skill(self):
        from executors.plugins.tool_loop_v1_helpers import _tool_evidence_links

        links = _tool_evidence_links(
            ["exp:abc", "skill:def@v3"],
            {"skill_evidence_link": {
                "kind": "skill", "ref": "skill:file:group:lint@sha256:abc",
                "relation": "invoked", "metadata": {"name": "lint"},
            }},
        )
        self.assertEqual(
            [(link["kind"], link["relation"]) for link in links],
            [("memory", "cited"), ("skill", "cited"), ("skill", "invoked")],
        )

    def test_context_links_mark_availability_not_causal_use(self):
        from executors.plugins.tool_loop_v1_helpers import _context_evidence_links

        links = _context_evidence_links(
            ("exp:abc", "skill:def@v3"),
            [{"evidence_link": {
                "kind": "skill", "ref": "skill:file:system:base@sha256:abc",
                "relation": "injected", "metadata": {"name": "base"},
            }}],
        )
        self.assertTrue(all(link["relation"] == "injected" for link in links))
        self.assertEqual([link["kind"] for link in links], ["memory", "skill", "skill"])

    def test_tool_ref_allowlist_rejects_unknown_and_malformed_values(self):
        self.assertEqual(
            validate_tool_refs(["exp:abc", "exp:abc"], ("exp:abc",)),
            ("exp:abc",),
        )
        with self.assertRaisesRegex(ValueError, "not injected"):
            validate_tool_refs(["exp:other"], ("exp:abc",))
        with self.assertRaisesRegex(ValueError, "array"):
            validate_tool_refs("exp:abc", ("exp:abc",))

    def test_schema_augmentation_is_non_mutating_and_enumerates_refs(self):
        schemas = [{
            "type": "function",
            "function": {
                "name": "read_file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }]
        augmented = add_tool_ref_parameter(
            schemas, ("exp:abc", "skill:def@v3")
        )
        self.assertNotIn(
            "_memory_refs",
            schemas[0]["function"]["parameters"]["properties"],
        )
        ref_schema = augmented[0]["function"]["parameters"]["properties"][
            "_memory_refs"
        ]
        self.assertEqual(
            ref_schema["items"]["enum"], ["exp:abc", "skill:def@v3"]
        )

    def test_causal_usage_requires_allowlisted_ref_and_action_identity(self):
        records = [
            {
                "name": "write_file",
                "attempt_id": "attempt:write",
                "memory_refs": ["exp:abc", "exp:not-injected"],
                "args": {"path": "x.py"},
                "is_error": False,
            },
            {
                "name": "run_shell",
                "attempt_id": "attempt:verify",
                "memory_refs": [],
                "args": {"cmd": "python3 -m pytest -q"},
                "is_error": False,
            },
        ]
        usages = collect_causal_usages(records, ("exp:abc",))
        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0].kind, UsageKind.EXPERIENCE)
        self.assertEqual(usages[0].item_id, "exp:abc")
        self.assertEqual(
            usages[0].action_evidence_ids, ("attempt:write",)
        )
        status, evidence = verification_for_usage(
            usages[0], records, terminal_outcome="completed"
        )
        self.assertEqual(status, UsageState.VERIFIED_SUCCESS)
        self.assertEqual(evidence["adapter"], "pytest")
        self.assertEqual(
            evidence["verifier_attempt_id"], "attempt:verify"
        )

    def test_verifier_before_cited_action_cannot_verify_memory(self):
        records = [
            {
                "name": "run_shell",
                "attempt_id": "attempt:verify",
                "memory_refs": [],
                "args": {"cmd": "pytest -q"},
                "is_error": False,
            },
            {
                "name": "read_file",
                "attempt_id": "attempt:read",
                "memory_refs": ["skill:abc@v2"],
                "args": {"path": "x.py"},
                "is_error": False,
            },
        ]
        usage = collect_causal_usages(
            records, ("skill:abc@v2",)
        )[0]
        self.assertIsNone(
            verification_for_usage(
                usage, records, terminal_outcome="completed"
            )
        )


class TestCausalUsageWiring(unittest.IsolatedAsyncioTestCase):
    @patch(
        "ai.reflexion.record_memory_adoption",
        new_callable=AsyncMock,
        return_value="decision:adoption",
    )
    async def test_finalize_advances_only_cited_memory(self, record_adoption):
        from executors.plugins.tool_loop_v1_helpers import (
            _finalize_causal_memory_usage,
        )

        runner = SimpleNamespace(
            run_id="run:1",
            ctx=SimpleNamespace(group_id=7),
            bot={"id": 3},
            injected_memory_refs=("exp:used", "exp:unused"),
            tool_records=[
                {
                    "name": "write_file",
                    "attempt_id": "attempt:write",
                    "memory_refs": ["exp:used"],
                    "args": {"path": "x.py"},
                    "is_error": False,
                },
                {
                    "name": "run_shell",
                    "attempt_id": "attempt:verify",
                    "memory_refs": [],
                    "args": {"cmd": "pytest -q"},
                    "is_error": False,
                },
            ],
        )
        learning = SimpleNamespace(
            mark_usage_adopted=AsyncMock(return_value=1),
            mark_usage_executed=AsyncMock(return_value=1),
            verify_usage=AsyncMock(return_value=1),
        )
        scope = MemoryScope.bot(
            group_id=7, bot_id=3, actor_id="bot:3"
        )

        await _finalize_causal_memory_usage(
            runner, scope=scope, learning_port=learning
        )

        record_adoption.assert_awaited_once_with(
            run_id="run:1",
            group_id=7,
            bot_id=3,
            evidence_by_ref={"exp:used": ("attempt:write",)},
        )
        adopted = learning.mark_usage_adopted.await_args.args[0]
        self.assertEqual(adopted.item_ids, ("exp:used",))
        executed = learning.mark_usage_executed.await_args.args[0]
        self.assertEqual(
            executed.evidence["evidence_ids"], ["attempt:write"]
        )
        verified = learning.verify_usage.await_args.args[0]
        self.assertEqual(verified.status, UsageState.VERIFIED_SUCCESS)
        self.assertEqual(verified.item_ids, ("exp:used",))


if __name__ == "__main__":
    unittest.main()
