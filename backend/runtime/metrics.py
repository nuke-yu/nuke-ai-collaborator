"""DFT-032: Prometheus process monitoring for the Supervisor fleet.

The Supervisor is the single entry process and the sole aggregator of fleet
state (V3 §10.1): it holds the subprocess handles (`_processes`), the live IPC
connections (`_workers`), the worker-pushed app stats (`_worker_stats`), and the
browser registry (`_browsers`). Workers are pure IPC subprocesses with no HTTP
server of their own, so rather than scraping each worker, we expose ONE
`/metrics` endpoint on the Supervisor process and let this collector read that
authoritative state at scrape time (pull-based) — no double bookkeeping.

The only genuinely event-driven metric is the restart counter, which the
Supervisor increments in `_run_process_loop` when a child is restarted.

Process-level RSS/CPU are read via psutil against the PIDs the Supervisor
already tracks; any pid that has gone away is simply skipped.
"""
import logging

from prometheus_client import CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily

try:
    import psutil
except Exception:  # pragma: no cover - psutil is a hard dep, but stay defensive
    psutil = None

log = logging.getLogger(__name__)


def _dig(d, *keys, default=0):
    """Best-effort nested lookup tolerant of missing keys / non-dicts."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


class SupervisorCollector:
    """A prometheus_client custom collector that snapshots a Supervisor.

    Reads only via getattr with defaults so it tolerates a partially-initialised
    Supervisor (e.g. during startup) without raising at scrape time.
    """

    def __init__(self, supervisor):
        self._sup = supervisor

    def collect(self):
        sup = self._sup
        import time

        processes = list(getattr(sup, "_processes", []) or [])
        restart_counts = dict(getattr(sup, "_restart_counts", {}) or {})
        workers = dict(getattr(sup, "_workers", {}) or {})
        worker_stats = dict(getattr(sup, "_worker_stats", {}) or {})
        worker_stats_ts = dict(getattr(sup, "_worker_stats_ts", {}) or {})
        browsers = dict(getattr(sup, "_browsers", {}) or {})

        # ── supervisor liveness ──────────────────────────────────────────
        up = GaugeMetricFamily("nuke_supervisor_up", "Supervisor process is serving (1).")
        up.add_metric([], 1.0)
        yield up

        # ── Group → Channel relay SLO signals ───────────────────────────
        relay = getattr(sup, "_channel_relay", None)
        relay_stats = relay.snapshot() if relay is not None else {}
        relay_cycles = GaugeMetricFamily(
            "nuke_channel_relay_cycles_total",
            "Relay polling cycles since Supervisor start.",
        )
        relay_forwarded = CounterMetricFamily(
            "nuke_channel_relay_forwarded_total",
            "Committed Group events forwarded into the Channel outbox.",
        )
        relay_errors = CounterMetricFamily(
            "nuke_channel_relay_errors_total",
            "Group relay errors isolated by the Supervisor.",
        )
        relay_timeouts = CounterMetricFamily(
            "nuke_channel_relay_timeouts_total",
            "Group relay timeouts isolated by the Supervisor.",
        )
        relay_cycles.add_metric([], float(relay_stats.get("cycles", 0)))
        relay_forwarded.add_metric([], float(relay_stats.get("forwarded", 0)))
        relay_errors.add_metric([], float(relay_stats.get("errors", 0)))
        relay_timeouts.add_metric([], float(relay_stats.get("timeouts", 0)))
        yield relay_cycles
        yield relay_forwarded
        yield relay_errors
        yield relay_timeouts

        # ── process fleet ────────────────────────────────────────────────
        alive = [(label, proc) for label, proc in processes
                 if getattr(proc, "returncode", None) is None]
        nproc = GaugeMetricFamily(
            "nuke_worker_processes", "Number of live child processes (workers + collector).")
        nproc.add_metric([], float(len(alive)))
        yield nproc

        proc_up = GaugeMetricFamily(
            "nuke_process_up", "Child process is alive (1) keyed by label.", labels=["label"])
        rss = GaugeMetricFamily(
            "nuke_process_rss_bytes", "Child process resident set size in bytes.", labels=["label"])
        cpu = GaugeMetricFamily(
            "nuke_process_cpu_percent", "Child process CPU percent (instantaneous).", labels=["label"])
        for label, proc in processes:
            is_alive = getattr(proc, "returncode", None) is None
            proc_up.add_metric([label], 1.0 if is_alive else 0.0)
            pid = getattr(proc, "pid", None)
            if is_alive and pid is not None and psutil is not None:
                try:
                    p = psutil.Process(pid)
                    rss.add_metric([label], float(p.memory_info().rss))
                    cpu.add_metric([label], float(p.cpu_percent(interval=None)))
                except Exception:
                    # pid vanished between snapshot and read; skip resource lines.
                    pass
        yield proc_up
        yield rss
        yield cpu

        # ── restart counter (event-driven, incremented by the supervisor) ─
        restarts = CounterMetricFamily(
            "nuke_process_restarts",
            "Total child-process restarts since supervisor start.", labels=["label"])
        for label, count in restart_counts.items():
            restarts.add_metric([label], float(count))
        yield restarts

        # ── IPC connection layer (distinct from "process alive") ──────────
        connected = GaugeMetricFamily(
            "nuke_worker_connected",
            "Child has an active IPC connection to the supervisor (1).", labels=["worker_id"])
        for wid in workers:
            connected.add_metric([wid], 1.0)
        yield connected

        # ── worker-pushed app stats + heartbeat freshness ────────────────
        bg_tasks = GaugeMetricFamily(
            "nuke_worker_bg_tasks", "Active background tasks per worker.", labels=["worker_id"])
        pending = GaugeMetricFamily(
            "nuke_pending_permissions", "Pending HIL permission requests per worker.", labels=["worker_id"])
        leases = GaugeMetricFamily(
            "nuke_active_leases", "Active group leases per worker.", labels=["worker_id"])
        age = GaugeMetricFamily(
            "nuke_worker_stats_age_seconds",
            "Seconds since the worker last reported stats (detects silent hangs).",
            labels=["worker_id"])
        writer_acquisitions = CounterMetricFamily(
            "nuke_sqlite_writer_acquisitions",
            "Total serialized SQLite writer acquisitions.", labels=["worker_id"])
        writer_contended = CounterMetricFamily(
            "nuke_sqlite_writer_contended_acquisitions",
            "Writer acquisitions that queued behind an in-process writer.", labels=["worker_id"])
        writer_busy = CounterMetricFamily(
            "nuke_sqlite_writer_busy_errors",
            "SQLITE_BUSY or SQLITE_LOCKED errors after native busy timeout.", labels=["worker_id"])
        writer_failures = CounterMetricFamily(
            "nuke_sqlite_writer_transaction_failures",
            "Write scopes that exited with an exception.", labels=["worker_id"])
        writer_wait = CounterMetricFamily(
            "nuke_sqlite_writer_wait_seconds",
            "Total time queued for the in-process writer lock.", labels=["worker_id"])
        writer_tx = CounterMetricFamily(
            "nuke_sqlite_writer_transaction_seconds",
            "Total time spent inside serialized write scopes.", labels=["worker_id"])
        writer_wait_max = GaugeMetricFamily(
            "nuke_sqlite_writer_wait_seconds_max",
            "Longest observed in-process writer queue wait.", labels=["worker_id"])
        writer_tx_max = GaugeMetricFamily(
            "nuke_sqlite_writer_transaction_seconds_max",
            "Longest observed serialized write scope.", labels=["worker_id"])
        writer_busy_timeout = GaugeMetricFamily(
            "nuke_sqlite_writer_busy_timeout_seconds",
            "Configured native SQLite busy timeout.", labels=["worker_id"])
        now = time.time()
        for wid, payload in worker_stats.items():
            bg_tasks.add_metric([wid], float(_dig(payload, "bg", "active")))
            pending.add_metric([wid], float(_dig(payload, "permissions", "pending")))
            leases.add_metric([wid], float(_dig(payload, "lifecycle", "active_leases")))
            writer_acquisitions.add_metric([wid], float(_dig(payload, "sqlite_writer", "acquisitions")))
            writer_contended.add_metric([wid], float(_dig(payload, "sqlite_writer", "contended_acquisitions")))
            writer_busy.add_metric([wid], float(_dig(payload, "sqlite_writer", "busy_errors")))
            writer_failures.add_metric([wid], float(_dig(payload, "sqlite_writer", "transaction_failures")))
            writer_wait.add_metric([wid], float(_dig(payload, "sqlite_writer", "wait_seconds_total")))
            writer_tx.add_metric([wid], float(_dig(payload, "sqlite_writer", "transaction_seconds_total")))
            writer_wait_max.add_metric([wid], float(_dig(payload, "sqlite_writer", "wait_seconds_max")))
            writer_tx_max.add_metric([wid], float(_dig(payload, "sqlite_writer", "transaction_seconds_max")))
            writer_busy_timeout.add_metric(
                [wid], float(_dig(payload, "sqlite_writer", "busy_timeout_ms")) / 1000.0
            )
            ts = worker_stats_ts.get(wid)
            if ts is not None:
                age.add_metric([wid], max(0.0, now - ts))
        yield bg_tasks
        yield pending
        yield leases
        yield age
        yield writer_acquisitions
        yield writer_contended
        yield writer_busy
        yield writer_failures
        yield writer_wait
        yield writer_tx
        yield writer_wait_max
        yield writer_tx_max
        yield writer_busy_timeout

        # ── canonical memory ↔ Chroma shadow audit ──────────────────────
        audit_fields = (
            ("canonical_total", "canonical_records", "Canonical Bot memory records."),
            ("canonical_sampled", "canonical_sampled", "Canonical records deeply compared."),
            ("projected_scanned", "projected_scanned", "Chroma Bot memories scanned."),
            ("matched", "matched", "Canonical projections matching Chroma."),
            ("missing", "missing", "Canonical projections missing from Chroma."),
            ("content_mismatched", "content_mismatches", "Projection content mismatches."),
            ("metadata_mismatched", "metadata_mismatches", "Projection metadata mismatches."),
            ("orphaned", "orphaned", "Chroma projections without canonical records."),
            ("invalid_canonical", "invalid_canonical", "Canonical records that cannot project."),
            ("outbox_pending", "outbox_pending", "Undelivered canonical Bot memory projections."),
            ("consecutive_passes", "rollout_consecutive_passes", "Consecutive qualifying audits."),
            ("required_passes", "rollout_required_passes", "Audits required to retire direct writes."),
            ("direct_write_enabled", "direct_write_enabled", "Whether legacy direct Chroma writes remain enabled."),
            ("last_audit_passed", "last_audit_passed", "Whether the latest audit qualified for rollout."),
            ("errors_total", "audit_errors", "Shadow audit failures since worker start."),
        )
        audit_metrics = []
        for _, suffix, description in audit_fields:
            family_cls = CounterMetricFamily if suffix == "audit_errors" else GaugeMetricFamily
            audit_metrics.append(family_cls(
                f"nuke_memory_projection_{suffix}",
                description,
                labels=["worker_id", "group_id"],
            ))
        audit_truncated = GaugeMetricFamily(
            "nuke_memory_projection_audit_truncated",
            "Whether the bounded shadow audit hit its per-group limit.",
            labels=["worker_id", "group_id"],
        )
        audit_age = GaugeMetricFamily(
            "nuke_memory_projection_audit_age_seconds",
            "Seconds since the last successful projection shadow audit.",
            labels=["worker_id", "group_id"],
        )
        for wid, payload in worker_stats.items():
            audits = _dig(
                payload, "lifecycle", "memory_projection_audits", default={}
            )
            if not isinstance(audits, dict):
                continue
            for group_id, snapshot in audits.items():
                if not isinstance(snapshot, dict):
                    continue
                labels = [wid, str(group_id)]
                for family, (field, _, _) in zip(audit_metrics, audit_fields):
                    family.add_metric(labels, float(snapshot.get(field, 0) or 0))
                audit_truncated.add_metric(
                    labels, 1.0 if snapshot.get("truncated") else 0.0
                )
                audited_at = snapshot.get("last_audited_at")
                if audited_at is not None:
                    audit_age.add_metric(
                        labels, max(0.0, now - float(audited_at))
                    )
        yield from audit_metrics
        yield audit_truncated
        yield audit_age

        # ── browser connections per group ────────────────────────────────
        browsers_g = GaugeMetricFamily(
            "nuke_browsers_connected", "Browser clients connected per group.", labels=["group_id"])
        for gid, clients in browsers.items():
            try:
                n = len(clients)
            except Exception:
                n = 0
            browsers_g.add_metric([str(gid)], float(n))
        yield browsers_g


def render_metrics(supervisor):
    """Render the Prometheus exposition for one supervisor snapshot.

    Uses a fresh registry per call so the pull-based collector reads live state
    and nothing leaks across scrapes. Returns (body_bytes, content_type).
    """
    from observability.prometheus_exporter import get_prometheus_metrics
    registry = CollectorRegistry()
    registry.register(SupervisorCollector(supervisor))
    base_bytes = generate_latest(registry)

    local_prom = get_prometheus_metrics()
    if supervisor is not None and hasattr(supervisor, "_worker_stats"):
        worker_stats = dict(getattr(supervisor, "_worker_stats", {}) or {})
        for w_payload in worker_stats.values():
            if isinstance(w_payload, dict):
                snapshot = w_payload.get("obs_metrics_snapshot")
                if snapshot:
                    local_prom.merge_snapshot(snapshot)

    obs_text = local_prom.generate_exposition_text()

    if obs_text:
        return base_bytes + obs_text.encode("utf-8"), CONTENT_TYPE_LATEST
    return base_bytes, CONTENT_TYPE_LATEST
