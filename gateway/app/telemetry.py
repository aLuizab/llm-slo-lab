"""OpenTelemetry setup: one MeterProvider and one TracerProvider, OTLP/HTTP exporters.

Providers are instances (not globals) so tests can inject in-memory readers/exporters.
Histogram buckets are set with `explicit_bucket_boundaries_advisory`, which the SDK honours
unless a View overrides it.
"""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app import __version__, semconv
from app.config import Settings


@dataclass
class Instruments:
    operation_duration: otel_metrics.Histogram
    token_usage: otel_metrics.Histogram
    server_request_duration: otel_metrics.Histogram
    time_to_first_token: otel_metrics.Histogram
    time_per_output_token: otel_metrics.Histogram
    request_cost: otel_metrics.Histogram
    output_token_rate: otel_metrics.Histogram
    requests: otel_metrics.Counter
    in_flight: otel_metrics.UpDownCounter


class Telemetry:
    def __init__(
        self,
        settings: Settings,
        metric_readers: list[MetricReader] | None = None,
        span_processors: list[SpanProcessor] | None = None,
    ):
        resource = Resource.create(
            {SERVICE_NAME: settings.service_name, SERVICE_VERSION: __version__}
        )
        readers = list(metric_readers or [])
        processors = list(span_processors or [])
        if settings.otlp_enabled and not metric_readers:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(), export_interval_millis=settings.metric_export_interval_ms
                )
            )
        if settings.otlp_enabled and not span_processors:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            processors.append(BatchSpanProcessor(OTLPSpanExporter()))

        self.meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        self.tracer_provider = TracerProvider(resource=resource)
        for p in processors:
            self.tracer_provider.add_span_processor(p)
        self.tracer = self.tracer_provider.get_tracer("llm-gateway", __version__)
        meter = self.meter_provider.get_meter("llm-gateway", __version__)
        b = settings.buckets
        self.instruments = Instruments(
            operation_duration=meter.create_histogram(
                semconv.METRIC_CLIENT_OPERATION_DURATION,
                unit="s",
                description="GenAI operation duration",
                explicit_bucket_boundaries_advisory=b.duration,
            ),
            token_usage=meter.create_histogram(
                semconv.METRIC_CLIENT_TOKEN_USAGE,
                unit="{token}",
                description="Number of input and output tokens used",
                explicit_bucket_boundaries_advisory=b.tokens,
            ),
            server_request_duration=meter.create_histogram(
                semconv.METRIC_SERVER_REQUEST_DURATION,
                unit="s",
                description="Generative AI server request duration",
                explicit_bucket_boundaries_advisory=b.duration,
            ),
            time_to_first_token=meter.create_histogram(
                semconv.METRIC_SERVER_TIME_TO_FIRST_TOKEN,
                unit="s",
                description="Time to generate the first token for successful responses",
                explicit_bucket_boundaries_advisory=b.ttft,
            ),
            time_per_output_token=meter.create_histogram(
                semconv.METRIC_SERVER_TIME_PER_OUTPUT_TOKEN,
                unit="s",
                description="Time per output token generated after the first token",
                explicit_bucket_boundaries_advisory=b.tpot,
            ),
            request_cost=meter.create_histogram(
                semconv.METRIC_REQUEST_COST,
                unit="{USD}",
                description="Notional cost per request (assumptions, not real prices)",
                explicit_bucket_boundaries_advisory=b.cost,
            ),
            output_token_rate=meter.create_histogram(
                semconv.METRIC_OUTPUT_TOKEN_RATE,
                unit="{token}/s",
                description="Output tokens per second after the first token",
                explicit_bucket_boundaries_advisory=b.token_rate,
            ),
            requests=meter.create_counter(
                semconv.METRIC_REQUESTS, unit="{request}", description="Requests by outcome"
            ),
            in_flight=meter.create_up_down_counter(
                semconv.METRIC_REQUESTS_IN_FLIGHT,
                unit="{request}",
                description="Requests currently being served (KEDA signal)",
            ),
        )

    def shutdown(self) -> None:
        self.meter_provider.shutdown()
        self.tracer_provider.shutdown()


def set_global(telemetry: Telemetry) -> None:
    """Register providers globally (only once per process; tests skip this)."""
    otel_metrics.set_meter_provider(telemetry.meter_provider)
    otel_trace.set_tracer_provider(telemetry.tracer_provider)
