"""Runtime protocol conformance tests verifying all algorithm adapters implement their ISP ports."""
from __future__ import annotations

import inspect
import os
import sys
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
from memory.ports.infrastructure import (
    CaseClusteringPort,
    CaseExtractionPort,
    ContextBudgetPort,
    DAGCheckpointPort,
    FactExtractionPort,
    FailureInsightPort,
    MemoryACLPort,
    MemoryAlgorithmPort,
    RerankPort,
    SkillExtractionPort,
    SuccessCriticPort,
    TemporalGraphPort,
)


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
                if inspect.iscoroutinefunction(port_method):
                    self.assertTrue(
                        inspect.iscoroutinefunction(adapter_method),
                        f"{adapter_cls.__name__}.{method_name} must be an async coroutine function matching {port_cls.__name__}.{method_name}",
                    )
                port_sig = inspect.signature(port_method)
                adapter_sig = inspect.signature(adapter_method)
                port_params = set(port_sig.parameters.keys()) - {"self"}
                adapter_params = set(adapter_sig.parameters.keys()) - {"self"}
                self.assertTrue(
                    port_params.issubset(adapter_params),
                    f"{adapter_cls.__name__}.{method_name} parameter set {adapter_params} missing protocol parameters {port_params}",
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
