"""Unit tests for Low-Cardinality Prometheus Exporter."""

import unittest

from observability.event_policy import classify_event
from observability.prometheus_exporter import (
    CounterMetric,
    GaugeMetric,
    HistogramMetric,
    LowCardinalityMetricsCollector,
    get_prometheus_metrics,
    sanitize_label_value,
)


class TestPrometheusExporter(unittest.TestCase):
    def test_sanitize_label_value(self):
        self.assertEqual(sanitize_label_value("status", None), "none")
        self.assertEqual(sanitize_label_value("status", "  Success  "), "success")
        self.assertEqual(sanitize_label_value("status", ""), "none")
        self.assertEqual(sanitize_label_value("event_type", "unknown_custom"), "unknown")

    def test_counter_metric(self):
        counter = CounterMetric("test_counter", "help", ("status", "component"))
        counter.inc({"status": "success", "component": "worker"})
        counter.inc({"status": "success", "component": "worker"}, 2.0)
        counter.inc({"status": "error", "component": "worker"})

        data = dict(counter.collect())
        self.assertEqual(data[("success", "worker")], 3.0)
        self.assertEqual(data[("error", "worker")], 1.0)

    def test_gauge_metric(self):
        gauge = GaugeMetric("test_gauge", "help", ("component",))
        gauge.set({"component": "worker"}, 5.0)
        gauge.inc({"component": "worker"}, 2.0)
        gauge.dec({"component": "worker"}, 1.0)

        data = dict(gauge.collect())
        self.assertEqual(data[("worker",)], 6.0)

    def test_histogram_metric(self):
        hist = HistogramMetric("test_hist", "help", ("event_type",), buckets=(0.1, 0.5, 1.0))
        hist.observe({"event_type": "session_start"}, 0.05)
        hist.observe({"event_type": "session_start"}, 0.3)

        data = dict(hist.collect())
        entry = data[("session_start",)]
        self.assertEqual(entry["count"], 2)
        self.assertAlmostEqual(entry["sum"], 0.35)
        # In Prometheus Histogram, bucket counts are cumulative (le <= X)
        self.assertEqual(entry["buckets"][0], 1)
        self.assertEqual(entry["buckets"][1], 2)

    def test_record_event_policy_metric(self):
        collector = LowCardinalityMetricsCollector()
        policy = classify_event("permission_requested", {"tool_name": "run_shell"})

        collector.record_event_policy(
            event_type="permission_requested",
            policy=policy,
            duration_s=0.25,
            status="success",
        )

        text = collector.generate_exposition_text()
        self.assertIn('nuke_agent_events_total{event_type="permission_requested"', text)
        self.assertIn("nuke_agent_event_duration_seconds_sum", text)
        self.assertIn("nuke_agent_tool_effects_total", text)

        # Confirm no high-cardinality label leak
        self.assertNotIn("group_id", text)
        self.assertNotIn("bot_id", text)
        self.assertNotIn("user_id", text)

    def test_global_singleton(self):
        m1 = get_prometheus_metrics()
        m2 = get_prometheus_metrics()
        self.assertIs(m1, m2)


if __name__ == "__main__":
    unittest.main()
