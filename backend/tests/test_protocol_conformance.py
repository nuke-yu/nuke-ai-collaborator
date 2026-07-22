"""Runtime protocol conformance tests verifying all 8 algorithm adapters implement their ISP ports."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.adapters.algorithms import (
    AutoGenFailureAlgorithmAdapter,
    EverOSCaseAlgorithmAdapter,
    EverOSSkillAlgorithmAdapter,
    GraphitiTemporalAlgorithmAdapter,
    HybridRerankAlgorithmAdapter,
    LangGraphDAGAlgorithmAdapter,
    LettaACLAlgorithmAdapter,
    Mem0FactAlgorithmAdapter,
)
from memory.ports.infrastructure import (
    CaseExtractionPort,
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
from memory.adapters.algorithms import VoyagerCriticAlgorithmAdapter


class TestProtocolConformance(unittest.TestCase):
    def test_all_adapters_implement_memory_algorithm_port(self) -> None:
        adapters = [
            Mem0FactAlgorithmAdapter(),
            EverOSCaseAlgorithmAdapter(),
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
        self.assertTrue(isinstance(EverOSSkillAlgorithmAdapter(), SkillExtractionPort))
        self.assertTrue(isinstance(AutoGenFailureAlgorithmAdapter(), FailureInsightPort))
        self.assertTrue(isinstance(VoyagerCriticAlgorithmAdapter(), SuccessCriticPort))
        self.assertTrue(isinstance(HybridRerankAlgorithmAdapter(), RerankPort))
        self.assertTrue(isinstance(LangGraphDAGAlgorithmAdapter(), DAGCheckpointPort))
        self.assertTrue(isinstance(LettaACLAlgorithmAdapter(), MemoryACLPort))
        self.assertTrue(isinstance(GraphitiTemporalAlgorithmAdapter(), TemporalGraphPort))


if __name__ == "__main__":
    unittest.main()
