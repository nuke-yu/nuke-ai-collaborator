"""Runtime protocol conformance tests verifying all algorithm adapters implement their ISP ports."""
from __future__ import annotations

import inspect
import os
import sys
import types
import typing
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (
    AutoGenFailureAlgorithmAdapter,
    EverOSCaseAlgorithmAdapter,
    EverOSClusteringAlgorithmAdapter,
    EverOSSkillAlgorithmAdapter,
    GraphitiTemporalAlgorithmAdapter,
    HybridRerankAlgorithmAdapter,
    LangGraphDAGAlgorithmAdapter,
    LettaACLAlgorithmAdapter,
    Mem0FactAlgorithmAdapter,
    VoyagerCriticAlgorithmAdapter,
)
from memory.infrastructure import SQLiteMemoryDatabase
from memory.ports.infrastructure import (
    CaseClusteringPort,
    CaseExtractionPort,
    ContextBudgetPort,
    DAGCheckpointPort,
    FactExtractionPort,
    FailureInsightPort,
    MemoryACLPort,
    MemoryAlgorithmPort,
    MemoryDatabasePort,
    RerankPort,
    SkillExtractionPort,
    SuccessCriticPort,
    TemporalGraphPort,
)


def _annotation_is_compatible(protocol_type: object, adapter_type: object) -> bool:
    """Return whether an adapter annotation is a safe specialization of a port."""
    if protocol_type is typing.Any:
        return adapter_type is not inspect.Signature.empty
    if protocol_type == adapter_type:
        return True
    protocol_origin = typing.get_origin(protocol_type)
    adapter_origin = typing.get_origin(adapter_type)
    if protocol_origin in (typing.Union, types.UnionType):
        protocol_args = typing.get_args(protocol_type)
        adapter_args = typing.get_args(adapter_type)
        return bool(adapter_args) and all(
            any(_annotation_is_compatible(expected, actual) for expected in protocol_args)
            for actual in adapter_args
        )
    if protocol_origin is not None and protocol_origin == adapter_origin:
        protocol_args = typing.get_args(protocol_type)
        adapter_args = typing.get_args(adapter_type)
        return len(protocol_args) == len(adapter_args) and all(
            _annotation_is_compatible(expected, actual)
            for expected, actual in zip(protocol_args, adapter_args)
        )
    return False


class TestProtocolConformance(unittest.TestCase):
    def test_all_adapters_implement_memory_algorithm_port(self) -> None:
        adapters = [
            Mem0FactAlgorithmAdapter(),
            EverOSCaseAlgorithmAdapter(),
            EverOSClusteringAlgorithmAdapter(),
            EverOSSkillAlgorithmAdapter(),
            AutoGenFailureAlgorithmAdapter(),
            VoyagerCriticAlgorithmAdapter(),
            HybridRerankAlgorithmAdapter(),
            LangGraphDAGAlgorithmAdapter(),
            LettaACLAlgorithmAdapter(),
            GraphitiTemporalAlgorithmAdapter(),
        ]
        for adapter in adapters:
            self.assertTrue(isinstance(adapter, MemoryAlgorithmPort), f"{adapter.__class__.__name__} failed MemoryAlgorithmPort conformance")
            self.assertIsNotNone(adapter.descriptor.algorithm_id)

    def test_domain_specific_port_conformance(self) -> None:
        self.assertTrue(isinstance(Mem0FactAlgorithmAdapter(), FactExtractionPort))
        self.assertTrue(isinstance(EverOSCaseAlgorithmAdapter(), CaseExtractionPort))
        self.assertTrue(isinstance(EverOSClusteringAlgorithmAdapter(), CaseClusteringPort))
        self.assertTrue(isinstance(EverOSSkillAlgorithmAdapter(), SkillExtractionPort))
        self.assertTrue(isinstance(AutoGenFailureAlgorithmAdapter(), FailureInsightPort))
        self.assertTrue(isinstance(VoyagerCriticAlgorithmAdapter(), SuccessCriticPort))
        self.assertTrue(isinstance(HybridRerankAlgorithmAdapter(), RerankPort))
        self.assertTrue(isinstance(LangGraphDAGAlgorithmAdapter(), DAGCheckpointPort))
        self.assertTrue(isinstance(LettaACLAlgorithmAdapter(), MemoryACLPort))
        self.assertTrue(isinstance(LettaACLAlgorithmAdapter(), ContextBudgetPort))
        self.assertTrue(isinstance(GraphitiTemporalAlgorithmAdapter(), TemporalGraphPort))
        self.assertTrue(isinstance(SQLiteMemoryDatabase(), MemoryDatabasePort))

    def test_strict_signature_and_async_conformance(self) -> None:
        pairs = [
            (FactExtractionPort, Mem0FactAlgorithmAdapter),
            (CaseExtractionPort, EverOSCaseAlgorithmAdapter),
            (CaseClusteringPort, EverOSClusteringAlgorithmAdapter),
            (SkillExtractionPort, EverOSSkillAlgorithmAdapter),
            (FailureInsightPort, AutoGenFailureAlgorithmAdapter),
            (SuccessCriticPort, VoyagerCriticAlgorithmAdapter),
            (RerankPort, HybridRerankAlgorithmAdapter),
            (DAGCheckpointPort, LangGraphDAGAlgorithmAdapter),
            (MemoryACLPort, LettaACLAlgorithmAdapter),
            (ContextBudgetPort, LettaACLAlgorithmAdapter),
            (TemporalGraphPort, GraphitiTemporalAlgorithmAdapter),
        ]
        for port_cls, adapter_cls in pairs:
            port_methods = [
                m for m in dir(port_cls)
                if not m.startswith("_") and callable(getattr(port_cls, m, None))
            ]
            for method_name in port_methods:
                port_method = getattr(port_cls, method_name)
                adapter_method = getattr(adapter_cls, method_name, None)
                self.assertIsNotNone(
                    adapter_method,
                    f"{adapter_cls.__name__} is missing method '{method_name}' from {port_cls.__name__}",
                )
                self.assertEqual(
                    inspect.iscoroutinefunction(adapter_method),
                    inspect.iscoroutinefunction(port_method),
                    f"{adapter_cls.__name__}.{method_name} sync/async kind differs from {port_cls.__name__}.{method_name}",
                )
                port_sig = inspect.signature(port_method)
                adapter_sig = inspect.signature(adapter_method)
                port_params = [p for name, p in port_sig.parameters.items() if name != "self"]
                adapter_params = [p for name, p in adapter_sig.parameters.items() if name != "self"]
                self.assertGreaterEqual(len(adapter_params), len(port_params))
                for expected, actual in zip(port_params, adapter_params):
                    self.assertEqual(actual.name, expected.name)
                    self.assertEqual(actual.kind, expected.kind)
                    self.assertEqual(actual.default, expected.default)
                for extra in adapter_params[len(port_params):]:
                    self.assertNotEqual(
                        extra.default,
                        inspect.Parameter.empty,
                        f"{adapter_cls.__name__}.{method_name} has extra parameter '{extra.name}' without default value",
                    )

                port_hints = typing.get_type_hints(port_method)
                adapter_hints = typing.get_type_hints(adapter_method)
                for expected in port_params:
                    self.assertIn(expected.name, port_hints)
                    self.assertIn(expected.name, adapter_hints)
                    self.assertTrue(
                        _annotation_is_compatible(
                            port_hints[expected.name], adapter_hints[expected.name]
                        ),
                        f"{adapter_cls.__name__}.{method_name} annotation for '{expected.name}' "
                        f"({adapter_hints[expected.name]!r}) is incompatible with "
                        f"{port_cls.__name__} ({port_hints[expected.name]!r})",
                    )
                self.assertIn("return", port_hints)
                self.assertIn("return", adapter_hints)
                self.assertTrue(
                    _annotation_is_compatible(port_hints["return"], adapter_hints["return"]),
                    f"{adapter_cls.__name__}.{method_name} return annotation is incompatible",
                )

    def test_type_hints_introspection_without_name_errors(self) -> None:
        import memory.ports.infrastructure as infra
        import memory.adapters.algorithms.letta_acl_adapter as adapter_mod

        hints_infra = typing.get_type_hints(infra.MemoryACLPort.check_acl)
        self.assertIn("principal", hints_infra)

        hints_adapter = typing.get_type_hints(adapter_mod.LettaACLAlgorithmAdapter.check_acl)
        self.assertIn("principal", hints_adapter)


if __name__ == "__main__":
    unittest.main()
