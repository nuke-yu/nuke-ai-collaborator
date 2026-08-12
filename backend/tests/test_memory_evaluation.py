import unittest

from memory.evaluation import MemoryEvaluation


class TestMemoryEvaluation(unittest.TestCase):
    def test_quality_and_reuse_metrics(self):
        metrics = MemoryEvaluation()
        metrics.record_graph_resolution(["n1"], ["n1"])
        metrics.record_graph_resolution(["n2"], ["n1"])
        metrics.record_retrieval(["a", "b"], ["b", "c"])
        metrics.record_skill_reuse(True)
        metrics.record_skill_reuse(False)
        self.assertEqual(metrics.snapshot()["graph_resolution_accuracy"], 0.5)
        self.assertEqual(metrics.snapshot()["retrieval_recall"], 0.5)
        self.assertEqual(metrics.snapshot()["skill_reuse_success_rate"], 0.5)

    def test_operation_latency_and_cost_are_bounded(self):
        metrics = MemoryEvaluation()
        metrics.start_operation("op")
        metrics.finish_operation("op", cost=0.02)
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["operation_count"], 1)
        self.assertGreaterEqual(snapshot["operation_latency_ms_avg"], 0)
        self.assertEqual(snapshot["operation_cost_total"], 0.02)


if __name__ == "__main__":
    unittest.main()
