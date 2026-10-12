"""OpenTelemetry tracer setup and current-span helpers."""

import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

from .version import VERSION


def initialize_tracing() -> TracerProvider:
    """Install the global tracer provider with the exporters the TRACING_* env vars enable."""
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "contex", SERVICE_VERSION: VERSION}))
    if os.getenv("TRACING_CONSOLE_EXPORT", "false").lower() == "true":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    if os.getenv("TRACING_OTLP_EXPORT", "false").lower() == "true":
        endpoint = os.getenv("TRACING_OTLP_ENDPOINT", "http://localhost:4317")
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
    return provider


def get_current_trace_id() -> str | None:
    """The active span's trace ID as hex, or None outside a span."""
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def get_current_span_id() -> str | None:
    """The active span's span ID as hex, or None outside a span."""
    context = trace.get_current_span().get_span_context()
    return format(context.span_id, "016x") if context.is_valid else None
