"""
telemetry.py — OpenTelemetry tracing setup.

Spans follow the OpenTelemetry GenAI semantic conventions (gen_ai.*), so the
traces are readable by any backend that understands them. Incoming
`traceparent` headers are honoured (FastAPI's built-in instrumentation
continues them), so an agent's run span becomes the ancestor of the
gateway's LLM span.
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(service_name: str, version: str, endpoint: str) -> trace.Tracer:
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "service.version": version})
    )
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


def trace_id_hex(span: trace.Span) -> str | None:
    ctx = span.get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
