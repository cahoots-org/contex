"""Tests for the current-span helpers."""

from src.core.tracing import get_current_span_id, get_current_trace_id, initialize_tracing


def test_ids_only_inside_a_span():
    tracer = initialize_tracing().get_tracer(__name__)
    assert get_current_trace_id() is None and get_current_span_id() is None
    with tracer.start_as_current_span("op"):
        assert len(get_current_trace_id()) == 32
        assert len(get_current_span_id()) == 16
