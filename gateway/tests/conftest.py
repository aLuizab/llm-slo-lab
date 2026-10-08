"""Test fixtures: a real mock-llm server (uvicorn, background thread) and an in-process gateway."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mock-llm"))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.telemetry import Telemetry  # noqa: E402
from mock_llm.main import MockOptions  # noqa: E402
from mock_llm.main import create_app as create_mock  # noqa: E402

TOKENIZER = os.environ.get(
    "TOKENIZER_PATH",
    str(Path.home() / ".cache/llm-slo-lab/models/qwen2.5-0.5b-instruct/tokenizer.json"),
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def run_server(app) -> Iterator[str]:
    """Serve an ASGI app with uvicorn in a background thread; yields its base URL.

    httpx's ASGITransport buffers whole responses, so anything that must observe the gateway
    *during* a streamed response (in-flight gauge) needs a real server.
    """
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def mock_llm():
    """mock-llm on a real port, fast timings; per-test knobs go through x-mock-* headers."""
    app = create_mock(MockOptions(ttft_ms=30, inter_token_ms=2, output_tokens=12))
    with run_server(app) as url:
        yield url


@pytest.fixture
def make_gateway(mock_llm):
    """Factory: build a gateway app + in-memory telemetry with custom settings."""
    created = []

    def _make(**overrides):
        settings = Settings(
            upstream_url=mock_llm,
            model_name="mock-llm",
            provider_name="mock",
            tokenizer_path=overrides.pop("tokenizer_path", TOKENIZER),
            **overrides,
        )
        reader = InMemoryMetricReader()
        spans = InMemorySpanExporter()
        telemetry = Telemetry(
            settings, metric_readers=[reader], span_processors=[SimpleSpanProcessor(spans)]
        )
        app = create_app(settings, telemetry)
        created.append(telemetry)
        return app, reader, spans

    yield _make
    for t in created:
        t.shutdown()


@pytest.fixture
async def gateway(make_gateway):
    app, reader, spans = make_gateway()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gw"
        ) as client:
            yield client, reader, spans


def metrics_by_name(reader: InMemoryMetricReader) -> dict[str, list]:
    """{metric name: [data points]} from an InMemoryMetricReader."""
    out: dict[str, list] = {}
    data = reader.get_metrics_data()
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out.setdefault(metric.name, []).extend(metric.data.data_points)
    return out
