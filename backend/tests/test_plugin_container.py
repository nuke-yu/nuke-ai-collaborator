import unittest

from executors.container import DependencyContainer, DependencyNotFound, PluginComposition
from executors.base import BotExecutor, ExecutionResult, PluginManifest


class PluginContainerTest(unittest.TestCase):
    def test_resolves_declared_dependencies_and_rejects_missing(self) -> None:
        container = DependencyContainer()
        database = object()
        container.bind("db", database)
        self.assertIs(container.resolve("db"), database)
        self.assertEqual(container.resolve_many(["db"]), {"db": database})
        with self.assertRaises(DependencyNotFound):
            container.resolve("fs")

    def test_manifest_serializes_injection_contract(self) -> None:
        manifest = PluginManifest(description="test", inject=["db", "fs"])
        self.assertEqual(manifest.to_dict()["inject"], ["db", "fs"])

    def test_rejects_duplicate_declarations(self) -> None:
        container = DependencyContainer()
        container.bind("db", object())
        with self.assertRaises(ValueError):
            container.resolve_many(["db", "db"])

    def test_plugin_composition_owns_bindings(self) -> None:
        first_value = object()
        second_value = object()
        first = PluginComposition().bind("db", first_value)
        second = PluginComposition().bind("db", second_value)
        self.assertIs(first.dependencies.resolve("db"), first_value)
        self.assertIs(second.dependencies.resolve("db"), second_value)

    def test_executor_dependencies_are_instance_owned(self) -> None:
        class Executor(BotExecutor):
            async def run(self, _ctx) -> ExecutionResult:
                raise NotImplementedError

        first = Executor()
        second = Executor()
        first.dependencies["db"] = object()
        self.assertNotIn("db", second.dependencies)
        self.assertIsNot(first.dependencies, second.dependencies)


if __name__ == "__main__":
    unittest.main()
