import ast
import os
import sys
import unittest
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.contracts import ObserveMemory, RecallMemory
from memory.domain import (
    MemoryScope,
    ScopeKind,
    UsageState,
    can_transition_usage,
    require_adoption_evidence,
    require_execution_evidence,
    require_usage_transition,
)
from memory.ports import MemoryCommandPort, MemoryQueryPort


class TestMemoryScope(unittest.TestCase):
    def test_scope_partition_is_explicit_and_immutable(self):
        scope = MemoryScope.bot(group_id=9, bot_id=5, actor_id="worker:3", run_id="run:1")
        self.assertEqual(scope.storage_partition(), (9, ScopeKind.BOT, 5))
        with self.assertRaisesRegex(Exception, "cannot assign"):
            scope.group_id = 10

    def test_bot_scope_requires_bot_and_personal_scope_requires_user(self):
        with self.assertRaisesRegex(ValueError, "bot scope"):
            MemoryScope(kind=ScopeKind.BOT, group_id=9, actor_id="worker")
        with self.assertRaisesRegex(ValueError, "personal scope"):
            MemoryScope(kind=ScopeKind.PERSONAL, group_id=9, actor_id="user")

    def test_group_and_identity_are_never_implicit(self):
        with self.assertRaisesRegex(ValueError, "group.*group_id"):
            MemoryScope.group(group_id=0, actor_id="worker")
        with self.assertRaisesRegex(ValueError, "actor_id"):
            MemoryScope.group(group_id=9, actor_id=" ")

    def test_personal_scope_exists_outside_group_until_explicit_projection(self):
        scope = MemoryScope.personal(user_id=7, actor_id="user:7")
        self.assertEqual(scope.storage_partition(), (None, ScopeKind.PERSONAL, 7))

    def test_usage_evidence_lifecycle_is_monotonic(self):
        self.assertTrue(
            can_transition_usage(UsageState.INJECTED, UsageState.ADOPTED)
        )
        self.assertTrue(
            can_transition_usage(UsageState.EXECUTED, UsageState.VERIFIED_SUCCESS)
        )
        self.assertFalse(
            can_transition_usage(UsageState.INJECTED, UsageState.EXECUTED)
        )
        with self.assertRaisesRegex(ValueError, "invalid usage transition"):
            require_usage_transition(
                UsageState.VERIFIED_SUCCESS, UsageState.EXECUTED
            )
        with self.assertRaisesRegex(ValueError, "decision_trace"):
            require_adoption_evidence("model_self_report", {"decision_id": "d:1"})
        with self.assertRaisesRegex(ValueError, "matching action"):
            require_execution_evidence(
                {"action_match": False, "evidence_ids": ["event:1"]}
            )


class _MemoryClient:
    async def observe(self, command):
        return None

    async def forget(self, command):
        return None

    async def recall(self, query):
        raise NotImplementedError


class TestPublicContracts(unittest.TestCase):
    def test_commands_validate_content_and_query_limits(self):
        scope = MemoryScope.group(group_id=9, actor_id="worker:3")
        with self.assertRaisesRegex(ValueError, "content"):
            ObserveMemory(scope=scope, source_id="m:1", content=" ")
        with self.assertRaisesRegex(ValueError, "limit"):
            RecallMemory(scope=scope, query="architecture", limit=0)

    def test_ports_are_structural_and_runtime_checkable(self):
        client = _MemoryClient()
        self.assertIsInstance(client, MemoryCommandPort)
        self.assertIsInstance(client, MemoryQueryPort)


class TestMemoryArchitecture(unittest.TestCase):
    def test_domain_and_application_do_not_import_runtime_or_adapters(self):
        root = Path(__file__).resolve().parents[1] / "memory"
        forbidden = (
            "ai",
            "api",
            "executors",
            "runtime",
            "fastapi",
            "chromadb",
            "graphiti_core",
            "mem0",
            "memory.adapters",
        )
        violations = []
        for layer in ("domain", "application", "infrastructure"):
            for path in (root / layer).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    modules = []
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        modules = [node.module]
                    for module in modules:
                        if any(module == name or module.startswith(name + ".") for name in forbidden):
                            violations.append(f"{path.relative_to(root)} imports {module}")
        self.assertEqual(violations, [])

    def test_projection_outbox_implementation_is_owned_by_memory(self):
        backend = Path(__file__).resolve().parents[1]
        compatibility = (backend / "ai" / "projection_outbox.py").read_text(
            encoding="utf-8"
        )
        implementation = (
            backend / "memory" / "infrastructure" / "projection_outbox.py"
        ).read_text(encoding="utf-8")
        self.assertIn("memory.bootstrap", compatibility)
        self.assertNotIn("INSERT INTO memory_projection_outbox", compatibility)
        self.assertIn("INSERT INTO memory_projection_outbox", implementation)

    def test_host_group_schema_reuses_memory_manifest(self):
        backend = Path(__file__).resolve().parents[1]
        host_schema = (backend / "db" / "schema_split.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("MEMORY_GROUP_DDL", host_schema)
        self.assertIn("MEMORY_GROUP_TABLES", host_schema)
        self.assertNotIn(
            "CREATE TABLE IF NOT EXISTS memory_records", host_schema
        )

    def test_tool_loop_depends_on_module_contract_not_legacy_provider(self):
        backend = Path(__file__).resolve().parents[1]
        paths = (
            backend / "executors" / "plugins" / "tool_loop_v1.py",
            backend / "executors" / "plugins" / "tool_loop_v1_helpers.py",
        )
        imports = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
                elif isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
        self.assertNotIn("ai.memory_provider", imports)
        self.assertNotIn("ai.pipeline", imports)
        self.assertNotIn("ai.cases", imports)
        self.assertNotIn("ai.experiences", imports)
        self.assertNotIn("ai.skill_learning", imports)
        self.assertNotIn("ai.personal_vault", imports)
        self.assertIn("memory.bootstrap", imports)
        self.assertIn("memory.contracts", imports)

    def test_bot_context_deletion_uses_memory_module_contract(self):
        backend = Path(__file__).resolve().parents[1]
        path = backend / "db" / "queries.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
        self.assertNotIn("ai.memory_provider", imports)
        self.assertIn("memory.bootstrap", imports)
        self.assertIn("memory.contracts", imports)

    def test_personal_api_has_no_direct_vault_dependency(self):
        backend = Path(__file__).resolve().parents[1]
        path = backend / "api" / "personal_memory.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules = [node.module for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module]
        self.assertNotIn("ai.personal_vault", modules)
        self.assertIn("memory.bootstrap", modules)


if __name__ == "__main__":
    unittest.main()
