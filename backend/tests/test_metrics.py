"""DFT-032: Prometheus process monitoring for the Supervisor fleet.

The collector reads the Supervisor's authoritative in-memory state at scrape
time (pull-based custom collector), so these tests feed a lightweight fake
Supervisor and assert on the rendered exposition text — no real subprocesses,
no IPC.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import metrics


class _FakeProc:
    def __init__(self, pid, returncode=None):
        self.pid = pid
        self.returncode = returncode


class _FakeSupervisor:
    """Mimics the Supervisor attributes the collector reads."""
    def __init__(self):
        self._stopping = False
        self._processes = []                     # list[(label, proc)]
        self._restart_counts = {}                # label -> int
        self._workers = {}                       # worker_id -> writer (presence only)
        self._worker_stats = {}                  # worker_id -> payload
        self._worker_stats_ts = {}               # worker_id -> float epoch
        self._browsers = {}                      # group_id -> set


class TestMetricsCollector(unittest.TestCase):
    def _render(self, sup):
        body, content_type = metrics.render_metrics(sup)
        self.assertIn("text/plain", content_type)
        return body.decode() if isinstance(body, bytes) else body

    def test_supervisor_up_always_one(self):
        sup = _FakeSupervisor()
        out = self._render(sup)
        self.assertIn("nuke_supervisor_up 1.0", out)

    def test_process_count_and_per_label_up(self):
        sup = _FakeSupervisor()
        sup._processes = [("w0", _FakeProc(101)), ("mcp-collector", _FakeProc(102))]
        out = self._render(sup)
        self.assertIn("nuke_worker_processes 2.0", out)
        self.assertIn('nuke_process_up{label="w0"} 1.0', out)
        self.assertIn('nuke_process_up{label="mcp-collector"} 1.0', out)

    def test_restart_counter_exposed(self):
        sup = _FakeSupervisor()
        sup._restart_counts = {"w0": 3, "w1": 0}
        out = self._render(sup)
        self.assertIn('nuke_process_restarts_total{label="w0"} 3.0', out)
        self.assertIn('nuke_process_restarts_total{label="w1"} 0.0', out)

    def test_worker_connected_from_ipc_presence(self):
        sup = _FakeSupervisor()
        sup._workers = {"w0": object(), "mcp-collector": object()}
        out = self._render(sup)
        self.assertIn('nuke_worker_connected{worker_id="w0"} 1.0', out)
        self.assertIn('nuke_worker_connected{worker_id="mcp-collector"} 1.0', out)

    def test_worker_app_stats_projected(self):
        sup = _FakeSupervisor()
        sup._worker_stats = {
            "w0": {
                "bg": {"active": 4},
                "permissions": {"pending": 2},
                "lifecycle": {"active_leases": 5},
                "sqlite_writer": {
                    "acquisitions": 12,
                    "contended_acquisitions": 3,
                    "busy_errors": 1,
                    "transaction_failures": 2,
                    "wait_seconds_total": 1.25,
                    "wait_seconds_max": 0.5,
                    "transaction_seconds_total": 4.5,
                    "transaction_seconds_max": 1.5,
                    "busy_timeout_ms": 5000,
                },
            }
        }
        out = self._render(sup)
        self.assertIn('nuke_worker_bg_tasks{worker_id="w0"} 4.0', out)
        self.assertIn('nuke_pending_permissions{worker_id="w0"} 2.0', out)
        self.assertIn('nuke_active_leases{worker_id="w0"} 5.0', out)
        self.assertIn('nuke_sqlite_writer_acquisitions_total{worker_id="w0"} 12.0', out)
        self.assertIn('nuke_sqlite_writer_contended_acquisitions_total{worker_id="w0"} 3.0', out)
        self.assertIn('nuke_sqlite_writer_busy_errors_total{worker_id="w0"} 1.0', out)
        self.assertIn('nuke_sqlite_writer_wait_seconds_total{worker_id="w0"} 1.25', out)
        self.assertIn('nuke_sqlite_writer_transaction_seconds_max{worker_id="w0"} 1.5', out)
        self.assertIn('nuke_sqlite_writer_busy_timeout_seconds{worker_id="w0"} 5.0', out)

    def test_stats_age_seconds_from_timestamp(self):
        sup = _FakeSupervisor()
        sup._worker_stats = {"w0": {}}
        sup._worker_stats_ts = {"w0": time.time() - 45.0}
        out = self._render(sup)
        # age should be ~45s; assert the metric exists and is >= 40
        line = next(l for l in out.splitlines()
                    if l.startswith('nuke_worker_stats_age_seconds{worker_id="w0"}'))
        age = float(line.rsplit(" ", 1)[1])
        self.assertGreaterEqual(age, 40.0)

    def test_browsers_per_group(self):
        sup = _FakeSupervisor()
        sup._browsers = {7: {object(), object()}, 9: {object()}}
        out = self._render(sup)
        self.assertIn('nuke_browsers_connected{group_id="7"} 2.0', out)
        self.assertIn('nuke_browsers_connected{group_id="9"} 1.0', out)

    def test_missing_attrs_do_not_crash(self):
        # A bare object missing optional dicts must still render the base gauge.
        class Bare:
            _processes = []
        out = self._render(Bare())
        self.assertIn("nuke_supervisor_up 1.0", out)


if __name__ == "__main__":
    unittest.main()
