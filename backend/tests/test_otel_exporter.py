"""Unit tests for OpenTelemetry Exporter."""

import json
import unittest

from observability.event_policy import classify_event
from observability.otel_exporter import (
    SPAN_KIND_INTERNAL,
    OtelSpan,
    OtelTraceExporter,
    generate_span_id,
    generate_trace_id,
    get_otel_exporter,
    normalize_trace_id,
    sanitize_attribute_value,
)


class TestOtelExporter(unittest.TestCase):
    def test_trace_id_span_id_format(self):
        trace_id = generate_trace_id()
        span_id = generate_span_id()
        self.assertEqual(len(trace_id), 32)
        self.assertEqual(len(span_id), 16)

    def test_normalize_hyphenated_uuid_trace_id(self):
        raw_uuid = "12345678-1234-1234-1234-123456789abc"
        cleaned = normalize_trace_id(raw_uuid)
        self.assertEqual(len(cleaned), 32)
        self.assertEqual(cleaned, "12345678123412341234123456789abc")

        exporter = OtelTraceExporter(enabled=True)
        span = exporter.start_span("test", trace_id=raw_uuid)
        self.assertEqual(span.trace_id, "12345678123412341234123456789abc")
        self.assertEqual(span.attributes.get("event.raw_trace_id"), raw_uuid)

    def test_redaction_in_attribute_values(self):
        raw_token = "github_pat_11AAAAAAA0000000000000_1234567890abcdef1234567890abcdef1234567890"
        redacted = sanitize_attribute_value(f"Authorization: Bearer {raw_token}")
        self.assertNotIn(raw_token, redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_span_lifecycle(self):
        span = OtelSpan(name="test_span", kind=SPAN_KIND_INTERNAL)
        self.assertIsNone(span.end_time_ns)
        self.assertEqual(span.status_code, "STATUS_CODE_UNSET")

        span.add_event("start_step", {"step": 1})
        span.set_attribute("http.status_code", 200)

        span.finish(status_code="STATUS_CODE_OK")
        self.assertIsNotNone(span.end_time_ns)
        self.assertEqual(span.status_code, "STATUS_CODE_OK")
        self.assertEqual(len(span.events), 1)

    def test_record_event_policy_to_otlp(self):
        exporter = OtelTraceExporter(service_name="nuke-test", enabled=True)
        policy = classify_event("session_start", {})

        span = exporter.record_event_policy(
            event_type="session_start",
            payload={"bot_id": 1},
            policy=policy,
        )

        self.assertIn("event.type", span.attributes)
        self.assertEqual(span.attributes["event.type"], "session_start")

        otlp = exporter.to_otlp_payload()
        self.assertIn("resourceSpans", otlp)
        resource_spans = otlp["resourceSpans"]
        self.assertEqual(len(resource_spans), 1)
        self.assertEqual(
            resource_spans[0]["resource"]["attributes"][0]["value"]["stringValue"],
            "nuke-test",
        )

        spans = resource_spans[0]["scopeSpans"][0]["spans"]
        self.assertEqual(len(spans), 1)

    def test_global_singleton(self):
        e1 = get_otel_exporter()
        e2 = get_otel_exporter()
        self.assertIs(e1, e2)


if __name__ == "__main__":
    unittest.main()
