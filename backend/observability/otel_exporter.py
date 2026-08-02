"""OpenTelemetry OTLP Tracing Exporter for business-significant events.

Converts EventPolicy-classified Agent execution events and tool traces into standard
OpenTelemetry (OTLP/HTTP) Spans and Traces suitable for export to Jaeger, Grafana Tempo,
Datadog, or any standard OpenTelemetry Collector.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from .event_policy import EventClass, EventPolicy, PayloadPolicy

try:
    from executors.redaction import redact_secrets
except ImportError:
    try:
        from redaction import redact_secrets
    except ImportError:
        def redact_secrets(val: Any) -> Any:
            return val

log = logging.getLogger(__name__)

# Standard OTLP Span Kinds
SPAN_KIND_INTERNAL = 1
SPAN_KIND_SERVER = 2
SPAN_KIND_CLIENT = 3
SPAN_KIND_PRODUCER = 4
SPAN_KIND_CONSUMER = 5

MAX_ATTR_STR_LEN = 512


def generate_trace_id() -> str:
    """Generate a 32-character hex string representing a 128-bit W3C trace ID."""
    return f"{uuid.uuid4().hex}{uuid.uuid4().hex[:32]}"[:32]


def generate_span_id() -> str:
    """Generate a 16-character hex string representing a 64-bit W3C span ID."""
    return uuid.uuid4().hex[:16]


def normalize_trace_id(raw_trace_id: str | None) -> str:
    """Normalize raw trace IDs (e.g. hyphenated UUIDs) to valid 32-character W3C hex strings."""
    if not raw_trace_id or not isinstance(raw_trace_id, str):
        return generate_trace_id()
    cleaned = raw_trace_id.replace("-", "").strip().lower()
    if len(cleaned) == 32 and all(c in "0123456789abcdef" for c in cleaned):
        return cleaned
    if len(cleaned) < 32 and all(c in "0123456789abcdef" for c in cleaned):
        return cleaned.zfill(32)
    return generate_trace_id()


def sanitize_attribute_value(val: Any, max_len: int = MAX_ATTR_STR_LEN) -> Any:
    """Redact secrets and bound string length to prevent sensitive data leakage."""
    if isinstance(val, str):
        res = redact_secrets(val)
        if isinstance(res, tuple):
            res = res[0]
        redacted = str(res)
        if len(redacted) > max_len:
            return redacted[:max_len] + "...[truncated]"
        return redacted
    elif isinstance(val, (list, tuple)):
        return [sanitize_attribute_value(item, max_len) for item in val]
    elif isinstance(val, dict):
        return {str(k): sanitize_attribute_value(v, max_len) for k, v in val.items()}
    return val


@dataclass
class OtelSpan:
    name: str
    trace_id: str = field(default_factory=generate_trace_id)
    span_id: str = field(default_factory=generate_span_id)
    parent_span_id: str | None = None
    kind: int = SPAN_KIND_INTERNAL
    start_time_ns: int = field(default_factory=lambda: int(time.time() * 1e9))
    end_time_ns: int | None = None
    status_code: str = "STATUS_CODE_UNSET"  # STATUS_CODE_UNSET, STATUS_CODE_OK, STATUS_CODE_ERROR
    status_message: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def finish(self, status_code: str = "STATUS_CODE_OK", status_message: str = "") -> OtelSpan:
        if self.end_time_ns is None:
            self.end_time_ns = int(time.time() * 1e9)
        self.status_code = status_code
        self.status_message = sanitize_attribute_value(status_message)
        return self

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        sanitized_attrs = {
            k: sanitize_attribute_value(v) for k, v in (attributes or {}).items()
        }
        self.events.append({
            "time_unix_nano": int(time.time() * 1e9),
            "name": name,
            "attributes": sanitized_attrs,
        })

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = sanitize_attribute_value(value)


def _format_otlp_attribute_value(val: Any) -> dict[str, Any]:
    if isinstance(val, bool):
        return {"boolValue": val}
    elif isinstance(val, int):
        return {"intValue": str(val)}
    elif isinstance(val, float):
        return {"doubleValue": val}
    elif isinstance(val, (list, tuple)):
        return {"arrayValue": {"values": [_format_otlp_attribute_value(v) for v in val]}}
    elif isinstance(val, dict):
        return {"stringValue": json.dumps(val, ensure_ascii=False)}
    else:
        return {"stringValue": str(val)}


def _format_otlp_attributes(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    formatted = []
    for k, v in attrs.items():
        if v is not None:
            formatted.append({
                "key": str(k),
                "value": _format_otlp_attribute_value(v),
            })
    return formatted


class OtelTraceExporter:
    """Buffer and export OpenTelemetry traces over OTLP/HTTP."""

    def __init__(
        self,
        service_name: str = "nuke-ai-collaborator",
        service_version: str = "1.0.0",
        endpoint: str | None = None,
        enabled: bool = True,
        max_buffer_size: int = 1000,
    ) -> None:
        self.service_name = service_name
        self.service_version = service_version
        self.endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
        self.enabled = enabled
        self.max_buffer_size = max_buffer_size
        self._spans_buffer: list[OtelSpan] = []
        self._active_spans: dict[str, OtelSpan] = {}
        self._lock = threading.Lock()

    def start_span(
        self,
        name: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        kind: int = SPAN_KIND_INTERNAL,
        attributes: dict[str, Any] | None = None,
    ) -> OtelSpan:
        sanitized_attrs = {
            k: sanitize_attribute_value(v) for k, v in (attributes or {}).items()
        }
        valid_trace_id = normalize_trace_id(trace_id)
        if trace_id and trace_id != valid_trace_id:
            sanitized_attrs["event.raw_trace_id"] = str(trace_id)

        span = OtelSpan(
            name=name,
            trace_id=valid_trace_id,
            span_id=generate_span_id(),
            parent_span_id=parent_span_id,
            kind=kind,
            attributes=sanitized_attrs,
        )
        with self._lock:
            self._active_spans[span.span_id] = span
        return span

    def end_span(
        self,
        span: OtelSpan,
        status_code: str = "STATUS_CODE_OK",
        status_message: str = "",
    ) -> None:
        span.finish(status_code=status_code, status_message=status_message)
        with self._lock:
            self._active_spans.pop(span.span_id, None)

            if not self.enabled:
                return

            self._spans_buffer.append(span)
            should_flush = len(self._spans_buffer) >= self.max_buffer_size

        if should_flush:
            self.flush()

    def record_event_policy(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        policy: EventPolicy,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> OtelSpan:
        """Create a completed Span for an EventPolicy-classified execution event."""
        while isinstance(policy, (tuple, list)):
            policy = policy[0]
        while isinstance(event_type, (tuple, list)):
            event_type = event_type[0]
        event_type_str = str(event_type)

        span_name = f"event.{event_type_str}"
        span = self.start_span(
            name=span_name,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            kind=SPAN_KIND_INTERNAL,
        )

        # Populate low-cardinality and security-audited attributes
        span.set_attribute("event.type", event_type_str)
        span.set_attribute("event.business_significant", policy.business_significant)
        span.set_attribute("event.retention", policy.retention.value)
        span.set_attribute("event.payload_policy", policy.payload_policy.value)
        span.set_attribute("event.classes", [c.value for c in policy.event_classes])
        span.set_attribute("event.effects", [e.value for e in policy.effect_classes])

        # Attach sanitized summary/error fields in compliance with PayloadPolicy
        if policy.payload_policy != PayloadPolicy.REDACTED:
            for key in ("tool_name", "effect_class", "reason", "error"):
                if key in payload:
                    val = payload[key]
                    if policy.payload_policy == PayloadPolicy.REFERENCE_ONLY and key in ("reason", "error"):
                        continue
                    span.set_attribute(f"event.{key}", val)

        status = "STATUS_CODE_ERROR" if EventClass.DIAGNOSTIC in policy.event_classes and "error" in payload else "STATUS_CODE_OK"
        self.end_span(span, status_code=status)
        return span

    def to_otlp_payload(self, spans: list[OtelSpan] | None = None) -> dict[str, Any]:
        """Convert a list of Spans to standard OTLP/HTTP JSON format."""
        with self._lock:
            target_spans = spans if spans is not None else list(self._spans_buffer)
        otlp_spans = []

        for span in target_spans:
            end_ns = span.end_time_ns or int(time.time() * 1e9)
            span_dict = {
                "traceId": span.trace_id,
                "spanId": span.span_id,
                "name": span.name,
                "kind": span.kind,
                "startTimeUnixNano": str(span.start_time_ns),
                "endTimeUnixNano": str(end_ns),
                "attributes": _format_otlp_attributes(span.attributes),
                "status": {
                    "code": span.status_code,
                    "message": span.status_message,
                },
            }
            if span.parent_span_id:
                span_dict["parentSpanId"] = span.parent_span_id
            if span.events:
                span_dict["events"] = [
                    {
                        "timeUnixNano": str(e["time_unix_nano"]),
                        "name": e["name"],
                        "attributes": _format_otlp_attributes(e.get("attributes", {})),
                    }
                    for e in span.events
                ]
            otlp_spans.append(span_dict)

        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": self.service_name}},
                            {"key": "service.version", "value": {"stringValue": self.service_version}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "nuke.observability", "version": "1.0.0"},
                            "spans": otlp_spans,
                        }
                    ],
                }
            ]
        }

    def flush_spans(self) -> list[OtelSpan]:
        with self._lock:
            flushed = list(self._spans_buffer)
            self._spans_buffer.clear()

        if flushed and self.endpoint and self.enabled:
            # Non-blocking HTTP export so Worker event loop is never stalled
            self._dispatch_http_export(flushed)

        return flushed

    def flush(self) -> list[OtelSpan]:
        """Alias for flush_spans to fix AttributeError when buffer max size is reached."""
        return self.flush_spans()

    def _dispatch_http_export(self, spans: list[OtelSpan]) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self._send_otlp_http, spans)
        except RuntimeError:
            threading.Thread(target=self._send_otlp_http, args=(spans,), daemon=True).start()

    def _send_otlp_http(self, spans: list[OtelSpan]) -> bool:
        if not self.endpoint:
            return False
        payload = self.to_otlp_payload(spans)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status in (200, 202):
                    return True
                self._record_dropped(len(spans), f"http_status_{resp.status}")
                return False
        except Exception as exc:
            log.warning("OpenTelemetry HTTP export failed: %s", exc)
            self._record_dropped(len(spans), "export_error")
            return False

    def _record_dropped(self, count: int, reason: str) -> None:
        try:
            from .prometheus_exporter import get_prometheus_metrics
            get_prometheus_metrics().record_spans_dropped(count, reason=reason)
        except Exception:
            pass


_global_otel_exporter: OtelTraceExporter | None = None


def get_otel_exporter() -> OtelTraceExporter:
    global _global_otel_exporter
    if _global_otel_exporter is None:
        _global_otel_exporter = OtelTraceExporter()
    return _global_otel_exporter
