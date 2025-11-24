"""Distributed tracing with OpenTelemetry for Contex"""

import os
from typing import Optional
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

from .logging import get_logger

logger = get_logger(__name__)


class TracingManager:
    """
    Manages distributed tracing with OpenTelemetry.

    Features:
    - Automatic instrumentation of FastAPI and Redis
    - Trace ID injection into logs
    - OTLP export to Jaeger/Tempo
    - Console export for development
    - Custom span utilities
    """

    def __init__(
        self,
        service_name: str = "contex",
        service_version: str = "0.3.0",
        enable_console_export: bool = False,
        enable_otlp_export: bool = True,
        otlp_endpoint: Optional[str] = None,
    ):
        """
        Initialize tracing manager.

        Args:
            service_name: Service name for traces
            service_version: Service version
            enable_console_export: Export traces to console (development)
            enable_otlp_export: Export traces to OTLP endpoint (Jaeger/Tempo)
            otlp_endpoint: OTLP endpoint URL (default: http://localhost:4317)
        """
        self.service_name = service_name
        self.service_version = service_version
        self.enable_console_export = enable_console_export
        self.enable_otlp_export = enable_otlp_export
        self.otlp_endpoint = otlp_endpoint or "http://localhost:4317"
        self.tracer_provider: Optional[TracerProvider] = None
        self.tracer: Optional[trace.Tracer] = None

    def initialize(self):
        """Initialize OpenTelemetry tracing"""
        # Create resource with service info
        resource = Resource.create({
            SERVICE_NAME: self.service_name,
            SERVICE_VERSION: self.service_version,
        })

        # Create tracer provider
        self.tracer_provider = TracerProvider(resource=resource)

        # Add console exporter for development
        if self.enable_console_export:
            console_exporter = ConsoleSpanExporter()
            console_processor = BatchSpanProcessor(console_exporter)
            self.tracer_provider.add_span_processor(console_processor)
            logger.info("Tracing: Console exporter enabled")

        # Add OTLP exporter for production
        if self.enable_otlp_export:
            try:
                otlp_exporter = OTLPSpanExporter(
                    endpoint=self.otlp_endpoint,
                    insecure=True  # Use insecure for local development
                )
                otlp_processor = BatchSpanProcessor(otlp_exporter)
                self.tracer_provider.add_span_processor(otlp_processor)
                logger.info("Tracing: OTLP exporter enabled", endpoint=self.otlp_endpoint)
            except Exception as e:
                logger.warning("Tracing: Failed to initialize OTLP exporter", error=str(e))

        # Set as global tracer provider
        trace.set_tracer_provider(self.tracer_provider)

        # Get tracer
        self.tracer = trace.get_tracer(__name__)

        logger.info("Tracing initialized",
                   service_name=self.service_name,
                   version=self.service_version)

    def instrument_fastapi(self, app):
        """
        Instrument FastAPI application.

        Args:
            app: FastAPI application instance
        """
        try:
            FastAPIInstrumentor.instrument_app(
                app,
                tracer_provider=self.tracer_provider,
                excluded_urls="health,ready,metrics"  # Don't trace health checks
            )
            logger.info("Tracing: FastAPI instrumented")
        except Exception as e:
            logger.error("Tracing: Failed to instrument FastAPI", error=str(e))

    def instrument_redis(self):
        """Instrument Redis operations"""
        try:
            RedisInstrumentor().instrument(tracer_provider=self.tracer_provider)
            logger.info("Tracing: Redis instrumented")
        except Exception as e:
            logger.error("Tracing: Failed to instrument Redis", error=str(e))

    def get_current_trace_id(self) -> Optional[str]:
        """
        Get current trace ID from context.

        Returns:
            Trace ID as hex string or None if no active span
        """
        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            return format(span.get_span_context().trace_id, '032x')
        return None

    def get_current_span_id(self) -> Optional[str]:
        """
        Get current span ID from context.

        Returns:
            Span ID as hex string or None if no active span
        """
        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            return format(span.get_span_context().span_id, '016x')
        return None

    @contextmanager
    def start_span(self, name: str, **attributes):
        """
        Context manager for creating custom spans.

        Args:
            name: Span name
            **attributes: Span attributes

        Example:
            >>> with tracing_manager.start_span("process_data", data_key="config"):
            ...     # Process data
            ...     pass
        """
        if not self.tracer:
            # Tracing not initialized, just yield
            yield None
            return

        with self.tracer.start_as_current_span(name) as span:
            # Add attributes
            for key, value in attributes.items():
                span.set_attribute(key, str(value))

            try:
                yield span
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                raise

    def add_span_attributes(self, **attributes):
        """
        Add attributes to current span.

        Args:
            **attributes: Attributes to add
        """
        span = trace.get_current_span()
        if span and span.is_recording():
            for key, value in attributes.items():
                span.set_attribute(key, str(value))

    def add_span_event(self, name: str, **attributes):
        """
        Add an event to current span.

        Args:
            name: Event name
            **attributes: Event attributes
        """
        span = trace.get_current_span()
        if span and span.is_recording():
            span.add_event(name, attributes)

    def shutdown(self):
        """Shutdown tracing and flush remaining spans"""
        if self.tracer_provider:
            self.tracer_provider.shutdown()
            logger.info("Tracing shutdown complete")


# Global tracing manager instance
_tracing_manager: Optional[TracingManager] = None


def get_tracing_manager() -> Optional[TracingManager]:
    """Get global tracing manager instance"""
    return _tracing_manager


def initialize_tracing(
    service_name: str = "contex",
    service_version: str = "0.3.0",
    enable_console_export: bool = None,
    enable_otlp_export: bool = None,
    otlp_endpoint: str = None,
) -> TracingManager:
    """
    Initialize global tracing manager.

    Args:
        service_name: Service name
        service_version: Service version
        enable_console_export: Enable console export (defaults to TRACING_CONSOLE_EXPORT env var)
        enable_otlp_export: Enable OTLP export (defaults to TRACING_OTLP_EXPORT env var)
        otlp_endpoint: OTLP endpoint (defaults to TRACING_OTLP_ENDPOINT env var)

    Returns:
        TracingManager instance
    """
    global _tracing_manager

    # Get configuration from environment if not provided
    if enable_console_export is None:
        enable_console_export = os.getenv("TRACING_CONSOLE_EXPORT", "false").lower() == "true"

    if enable_otlp_export is None:
        enable_otlp_export = os.getenv("TRACING_OTLP_EXPORT", "true").lower() == "true"

    if otlp_endpoint is None:
        otlp_endpoint = os.getenv("TRACING_OTLP_ENDPOINT", "http://localhost:4317")

    # Create and initialize manager
    _tracing_manager = TracingManager(
        service_name=service_name,
        service_version=service_version,
        enable_console_export=enable_console_export,
        enable_otlp_export=enable_otlp_export,
        otlp_endpoint=otlp_endpoint,
    )

    _tracing_manager.initialize()

    return _tracing_manager


def get_current_trace_id() -> Optional[str]:
    """
    Get current trace ID.

    Returns:
        Trace ID as hex string or None
    """
    manager = get_tracing_manager()
    if manager:
        return manager.get_current_trace_id()
    return None


def get_current_span_id() -> Optional[str]:
    """
    Get current span ID.

    Returns:
        Span ID as hex string or None
    """
    manager = get_tracing_manager()
    if manager:
        return manager.get_current_span_id()
    return None
