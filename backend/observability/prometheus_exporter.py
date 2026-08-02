"""Low-cardinality Prometheus Metrics Collector for business-significant events.

Enforces strict low-cardinality labels to prevent Prometheus TSDB metric explosion.
Disallows raw high-cardinality IDs (group_id, bot_id, session_id, user_id, run_id)
as labels, keeping metrics safe for large-scale multi-tenant production.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .event_policy import _EVENT_POLICIES, EffectClass, EventClass, EventPolicy, RetentionPolicy

log = logging.getLogger(__name__)

# Default histogram latency buckets (in seconds)
DEFAULT_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0)

# Allowed low-cardinality label keys
ALLOWED_LABEL_KEYS = {
    "event_type",
    "event_class",
    "effect_class",
    "retention",
    "payload_policy",
    "status",
    "component",
    "severity",
}

# Strict Enum Allow lists for label values
STATUS_ALLOWLIST = {"success", "error", "cancelled", "running", "unknown", "export_error"}
COMPONENT_ALLOWLIST = {"worker", "supervisor", "mcp_collector", "scheduler", "unknown"}
SEVERITY_ALLOWLIST = {"info", "warning", "error", "critical", "unknown"}


def escape_prometheus_string(s: str) -> str:
    """Escape backslashes, double quotes, and newlines in Prometheus label values."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def sanitize_label_value(key: str, val: Any) -> str:
    """Sanitize label value and enforce strict low-cardinality allowlists."""
    if key not in ALLOWED_LABEL_KEYS:
        return "unknown"

    if val is None:
        return "none"

    s = str(val).strip().lower()
    if not s:
        return "none"

    # Strict allowlists per label key
    if key == "event_type":
        if s not in _EVENT_POLICIES and s != "unknown":
            return "unknown"
    elif key == "event_class":
        if s not in {c.value for c in EventClass} and s != "unknown":
            return "unknown"
    elif key == "effect_class":
        if s not in {e.value for e in EffectClass} and s != "unknown":
            return "unknown"
    elif key == "retention":
        if s not in {r.value for r in RetentionPolicy} and s != "unknown":
            return "unknown"
    elif key == "status":
        if s not in STATUS_ALLOWLIST and not s.startswith("http_status_"):
            return "unknown"
    elif key == "component":
        if s not in COMPONENT_ALLOWLIST:
            return "unknown"
    elif key == "severity":
        if s not in SEVERITY_ALLOWLIST:
            return "unknown"

    if len(s) > 64:
        return "truncated"

    return escape_prometheus_string(s)


class CounterMetric:
    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...]) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._counts: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, labels: dict[str, str], amount: float = 1.0) -> None:
        key = tuple(sanitize_label_value(lbl, labels.get(lbl, "unknown")) for lbl in self.label_names)
        with self._lock:
            self._counts[key] = self._counts.get(key, 0.0) + amount

    def collect(self) -> list[tuple[tuple[str, ...], float]]:
        with self._lock:
            return list(self._counts.items())


class GaugeMetric:
    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...]) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def set(self, labels: dict[str, str], value: float) -> None:
        key = tuple(sanitize_label_value(lbl, labels.get(lbl, "unknown")) for lbl in self.label_names)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, labels: dict[str, str], amount: float = 1.0) -> None:
        key = tuple(sanitize_label_value(lbl, labels.get(lbl, "unknown")) for lbl in self.label_names)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, labels: dict[str, str], amount: float = 1.0) -> None:
        self.inc(labels, -amount)

    def collect(self) -> list[tuple[tuple[str, ...], float]]:
        with self._lock:
            return list(self._values.items())


class HistogramMetric:
    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...],
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self.buckets = sorted(buckets)
        self._data: dict[tuple[str, ...], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def observe(self, labels: dict[str, str], amount: float) -> None:
        key = tuple(sanitize_label_value(lbl, labels.get(lbl, "unknown")) for lbl in self.label_names)
        with self._lock:
            if key not in self._data:
                self._data[key] = {
                    "buckets": [0] * len(self.buckets),
                    "sum": 0.0,
                    "count": 0,
                }
            entry = self._data[key]
            entry["sum"] += amount
            entry["count"] += 1
            for idx, le in enumerate(self.buckets):
                if amount <= le:
                    entry["buckets"][idx] += 1

    def collect(self) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
        with self._lock:
            return [(k, dict(v)) for k, v in self._data.items()]


class LowCardinalityMetricsCollector:
    """Registry and aggregator for low-cardinality execution metrics."""

    def __init__(self) -> None:
        self.events_total = CounterMetric(
            "nuke_agent_events_total",
            "Total count of business-significant Agent events",
            ("event_type", "event_class", "effect_class", "status"),
        )
        self.event_duration_seconds = HistogramMetric(
            "nuke_agent_event_duration_seconds",
            "Execution duration of Agent events in seconds",
            ("event_type", "effect_class"),
        )
        self.active_runs = GaugeMetric(
            "nuke_agent_active_runs",
            "Number of active Agent execution runs currently running",
            ("component", "status"),
        )
        self.retention_cleanups_total = CounterMetric(
            "nuke_agent_retention_cleanups_total",
            "Total count of retention policy cleanup executions",
            ("retention", "status"),
        )
        self.tool_effects_total = CounterMetric(
            "nuke_agent_tool_effects_total",
            "Total count of executed tool effects by classification",
            ("effect_class", "status"),
        )
        self.spans_dropped_total = CounterMetric(
            "nuke_agent_otel_spans_dropped_total",
            "Total count of OpenTelemetry spans dropped due to export failure",
            ("status",),
        )
        self._lock = threading.Lock()

    def record_event_policy(
        self,
        event_type: str,
        policy: EventPolicy,
        duration_s: float = 0.0,
        status: str = "success",
    ) -> None:
        """Record metrics from an EventPolicy classification safely."""
        while isinstance(policy, (tuple, list)):
            policy = policy[0]
        while isinstance(event_type, (tuple, list)):
            event_type = event_type[0]
        event_type_str = str(event_type)

        event_class = policy.event_classes[0].value if policy.event_classes else "unknown"
        effect_class = policy.effect_classes[0].value if policy.effect_classes else "unknown"

        labels = {
            "event_type": event_type_str,
            "event_class": event_class,
            "effect_class": effect_class,
            "status": status,
        }
        self.events_total.inc(labels)

        if duration_s > 0:
            self.event_duration_seconds.observe(
                {"event_type": event_type, "effect_class": effect_class},
                duration_s,
            )

        if effect_class != "unknown":
            self.tool_effects_total.inc({"effect_class": effect_class, "status": status})

    def record_spans_dropped(self, count: int = 1, reason: str = "export_error") -> None:
        self.spans_dropped_total.inc({"status": reason}, amount=float(count))

    def record_retention_cleanup(self, retention_policy: str, status: str = "success") -> None:
        self.retention_cleanups_total.inc({"retention": retention_policy, "status": status})

    def set_active_runs(self, component: str, count: int, status: str = "running") -> None:
        self.active_runs.set({"component": component, "status": status}, float(count))

    def get_metrics_snapshot(self) -> dict[str, Any]:
        """Return structured metric data snapshot for IPC transport across worker/supervisor processes."""
        with self._lock:
            return {
                "events_total": self.events_total.collect(),
                "event_duration_seconds": self.event_duration_seconds.collect(),
                "active_runs": self.active_runs.collect(),
                "retention_cleanups": self.retention_cleanups_total.collect(),
                "tool_effects": self.tool_effects_total.collect(),
                "spans_dropped": self.spans_dropped_total.collect(),
            }

    def merge_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Merge a worker process's metric snapshot into this collector instance."""
        if not isinstance(snapshot, dict):
            return
        with self._lock:
            for key_tuple, val in snapshot.get("events_total", []):
                labels = dict(zip(self.events_total.label_names, key_tuple))
                self.events_total.inc(labels, val)
            for key_tuple, val in snapshot.get("tool_effects", []):
                labels = dict(zip(self.tool_effects_total.label_names, key_tuple))
                self.tool_effects_total.inc(labels, val)
            for key_tuple, val in snapshot.get("retention_cleanups", []):
                labels = dict(zip(self.retention_cleanups_total.label_names, key_tuple))
                self.retention_cleanups_total.inc(labels, val)
            for key_tuple, val in snapshot.get("spans_dropped", []):
                labels = dict(zip(self.spans_dropped_total.label_names, key_tuple))
                self.spans_dropped_total.inc(labels, val)

    def generate_exposition_text(self) -> str:
        """Generate Prometheus standard text exposition format (OpenMetrics compatible)."""
        lines = []

        # 1. Events Total Counter
        lines.append(f"# HELP {self.events_total.name} {self.events_total.help_text}")
        lines.append(f"# TYPE {self.events_total.name} counter")
        for key, val in self.events_total.collect():
            labels_str = ",".join(
                f'{k}="{v}"' for k, v in zip(self.events_total.label_names, key)
            )
            lines.append(f"{self.events_total.name}{{{labels_str}}} {val}")

        # 2. Tool Effects Total Counter
        lines.append(f"# HELP {self.tool_effects_total.name} {self.tool_effects_total.help_text}")
        lines.append(f"# TYPE {self.tool_effects_total.name} counter")
        for key, val in self.tool_effects_total.collect():
            labels_str = ",".join(
                f'{k}="{v}"' for k, v in zip(self.tool_effects_total.label_names, key)
            )
            lines.append(f"{self.tool_effects_total.name}{{{labels_str}}} {val}")

        # 3. Active Runs Gauge
        lines.append(f"# HELP {self.active_runs.name} {self.active_runs.help_text}")
        lines.append(f"# TYPE {self.active_runs.name} gauge")
        for key, val in self.active_runs.collect():
            labels_str = ",".join(
                f'{k}="{v}"' for k, v in zip(self.active_runs.label_names, key)
            )
            lines.append(f"{self.active_runs.name}{{{labels_str}}} {val}")

        # 4. Retention Cleanups Total Counter
        lines.append(
            f"# HELP {self.retention_cleanups_total.name} {self.retention_cleanups_total.help_text}"
        )
        lines.append(f"# TYPE {self.retention_cleanups_total.name} counter")
        for key, val in self.retention_cleanups_total.collect():
            labels_str = ",".join(
                f'{k}="{v}"'
                for k, v in zip(self.retention_cleanups_total.label_names, key)
            )
            lines.append(f"{self.retention_cleanups_total.name}{{{labels_str}}} {val}")

        # 5. Spans Dropped Total Counter
        lines.append(
            f"# HELP {self.spans_dropped_total.name} {self.spans_dropped_total.help_text}"
        )
        lines.append(f"# TYPE {self.spans_dropped_total.name} counter")
        for key, val in self.spans_dropped_total.collect():
            labels_str = ",".join(
                f'{k}="{v}"'
                for k, v in zip(self.spans_dropped_total.label_names, key)
            )
            lines.append(f"{self.spans_dropped_total.name}{{{labels_str}}} {val}")

        # 6. Event Duration Seconds Histogram
        lines.append(
            f"# HELP {self.event_duration_seconds.name} {self.event_duration_seconds.help_text}"
        )
        lines.append(f"# TYPE {self.event_duration_seconds.name} histogram")
        for key, data in self.event_duration_seconds.collect():
            base_labels = ",".join(
                f'{k}="{v}"'
                for k, v in zip(self.event_duration_seconds.label_names, key)
            )
            for idx, le in enumerate(self.event_duration_seconds.buckets):
                bucket_val = data["buckets"][idx]
                lines.append(
                    f'{self.event_duration_seconds.name}_bucket{{{base_labels},le="{le}"}} {bucket_val}'
                )
            lines.append(
                f'{self.event_duration_seconds.name}_bucket{{{base_labels},le="+Inf"}} {data["count"]}'
            )
            lines.append(
                f'{self.event_duration_seconds.name}_sum{{{base_labels}}} {data["sum"]}'
            )
            lines.append(
                f'{self.event_duration_seconds.name}_count{{{base_labels}}} {data["count"]}'
            )

        return "\n".join(lines) + "\n"


_global_prometheus_collector: LowCardinalityMetricsCollector | None = None


def get_prometheus_metrics() -> LowCardinalityMetricsCollector:
    global _global_prometheus_collector
    if _global_prometheus_collector is None:
        _global_prometheus_collector = LowCardinalityMetricsCollector()
    return _global_prometheus_collector
