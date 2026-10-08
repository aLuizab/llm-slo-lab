"""Evaluator: runs golden.jsonl through the gateway at temperature 0 and emits the quality
signal over OTLP.

Metrics (service.name = llm-evaluator):
  llm_slo.eval.checks      counter, attrs llm_slo.eval.check (type), llm_slo.eval.result (pass|fail)
  llm_slo.eval.items       counter, attrs llm_slo.eval.result
  llm_slo.eval.pass_ratio  gauge, checks passed / checks run in this invocation
  llm_slo.eval.duration    histogram (s), per item
Spans: one `eval run` span with a child `eval <item id>` per item. Prompts and answers are
never recorded unless CAPTURE_CONTENT=true (ADR-009).

Env: GATEWAY_URL (http://llm-gateway.llm.svc), GOLDEN_PATH (golden.jsonl), EVAL_SAMPLE
(items per run, 0 = all; the slice rotates each run so every item is covered over time),
EVAL_MAX_TOKENS (default when an item has none), EVAL_TIMEOUT_S, OTEL_EXPORTER_OTLP_ENDPOINT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

from evaluator.checks import run_checks

METRIC_CHECKS = "llm_slo.eval.checks"
METRIC_ITEMS = "llm_slo.eval.items"
METRIC_PASS_RATIO = "llm_slo.eval.pass_ratio"
METRIC_DURATION = "llm_slo.eval.duration"
ATTR_CHECK = "llm_slo.eval.check"
ATTR_RESULT = "llm_slo.eval.result"
ATTR_ITEM = "llm_slo.eval.item"
CLIENT_HEADER = {"x-llm-slo-client": "evaluator"}


def load_golden(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(json.loads(line))
    return items


def select_slice(items: list[dict], sample: int, run_index: int) -> list[dict]:
    """A rotating window of `sample` items so consecutive runs cover the whole set."""
    if sample <= 0 or sample >= len(items):
        return items
    start = (run_index * sample) % len(items)
    doubled = items + items
    return doubled[start : start + sample]


class Telemetry:
    def __init__(
        self,
        metric_readers: list[MetricReader] | None = None,
        span_processors: list[SpanProcessor] | None = None,
    ):
        resource = Resource.create(
            {SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "llm-evaluator")}
        )
        readers = list(metric_readers or [])
        processors = list(span_processors or [])
        if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") and not metric_readers:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
            processors.append(BatchSpanProcessor(OTLPSpanExporter()))
        self.meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        self.tracer_provider = TracerProvider(resource=resource)
        for p in processors:
            self.tracer_provider.add_span_processor(p)
        self.tracer = self.tracer_provider.get_tracer("llm-evaluator")
        meter = self.meter_provider.get_meter("llm-evaluator")
        self.checks = meter.create_counter(METRIC_CHECKS, unit="{check}")
        self.items = meter.create_counter(METRIC_ITEMS, unit="{item}")
        self.pass_ratio = meter.create_gauge(METRIC_PASS_RATIO, unit="1")
        self.duration = meter.create_histogram(
            METRIC_DURATION,
            unit="s",
            explicit_bucket_boundaries_advisory=[0.5, 1, 2, 4, 8, 16, 32, 64],
        )

    def shutdown(self) -> None:
        self.meter_provider.force_flush()
        self.tracer_provider.force_flush()
        self.meter_provider.shutdown()
        self.tracer_provider.shutdown()


def ask(client: httpx.Client, url: str, item: dict, max_tokens: int, timeout: float) -> str:
    body = {
        "messages": [{"role": "user", "content": item["prompt"]}],
        "temperature": 0,
        "max_tokens": int(item.get("max_tokens", max_tokens)),
        "stream": False,
    }
    r = client.post(f"{url}/v1/chat/completions", json=body, headers=CLIENT_HEADER, timeout=timeout)
    r.raise_for_status()
    choices = r.json().get("choices") or []
    return (choices[0].get("message") or {}).get("content") or "" if choices else ""


def evaluate(
    items: list[dict],
    url: str,
    telemetry: Telemetry,
    max_tokens: int,
    timeout: float,
    capture_content: bool = False,
) -> dict:
    passed_checks = total_checks = passed_items = 0
    report = []
    with httpx.Client() as client, telemetry.tracer.start_as_current_span("eval run") as run_span:
        run_span.set_attribute("llm_slo.eval.items_planned", len(items))
        for item in items:
            with telemetry.tracer.start_as_current_span(f"eval {item['id']}") as span:
                span.set_attribute(ATTR_ITEM, item["id"])
                t0 = time.monotonic()
                try:
                    answer = ask(client, url, item, max_tokens, timeout)
                    error = None
                except httpx.HTTPError as e:
                    answer, error = "", type(e).__name__
                elapsed = time.monotonic() - t0
                telemetry.duration.record(elapsed, {ATTR_ITEM: item["id"]})
                results = run_checks(answer, item["checks"])
                if error:
                    results = [
                        {**r, "passed": False, "reason": f"request failed: {error}"}
                        for r in results
                    ]
                item_ok = all(r["passed"] for r in results)
                for r in results:
                    total_checks += 1
                    passed_checks += int(r["passed"])
                    telemetry.checks.add(
                        1, {ATTR_CHECK: r["type"], ATTR_RESULT: "pass" if r["passed"] else "fail"}
                    )
                telemetry.items.add(1, {ATTR_RESULT: "pass" if item_ok else "fail"})
                passed_items += int(item_ok)
                span.set_attribute(ATTR_RESULT, "pass" if item_ok else "fail")
                span.set_attribute("llm_slo.eval.reasons", [r["reason"] for r in results])
                if capture_content:
                    span.set_attribute("gen_ai.input.messages", json.dumps(item["prompt"]))
                    span.set_attribute("gen_ai.output.messages", json.dumps(answer))
                if not item_ok:
                    span.set_status(Status(StatusCode.ERROR, "check failed"))
                report.append(
                    {
                        "id": item["id"],
                        "pass": item_ok,
                        "seconds": round(elapsed, 2),
                        "checks": results,
                    }
                )
        ratio = passed_checks / total_checks if total_checks else 0.0
        telemetry.pass_ratio.set(ratio)
        run_span.set_attribute("llm_slo.eval.pass_ratio", ratio)
    return {
        "items": len(items),
        "items_passed": passed_items,
        "checks": total_checks,
        "checks_passed": passed_checks,
        "pass_ratio": round(ratio, 4),
        "results": report,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="golden-dataset evaluator")
    ap.add_argument("--url", default=os.environ.get("GATEWAY_URL", "http://127.0.0.1:30080"))
    ap.add_argument(
        "--golden",
        default=os.environ.get("GOLDEN_PATH", str(Path(__file__).parent.parent / "golden.jsonl")),
    )
    ap.add_argument("--sample", type=int, default=int(os.environ.get("EVAL_SAMPLE", "0")))
    ap.add_argument("--max-tokens", type=int, default=int(os.environ.get("EVAL_MAX_TOKENS", "64")))
    ap.add_argument("--timeout", type=float, default=float(os.environ.get("EVAL_TIMEOUT_S", "120")))
    ap.add_argument("--run-index", type=int, default=None, help="default: derived from the clock")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    items = load_golden(Path(args.golden))
    run_index = args.run_index if args.run_index is not None else int(time.time() // 120)
    chosen = select_slice(items, args.sample, run_index)
    telemetry = Telemetry()
    capture = os.environ.get("CAPTURE_CONTENT", "false").lower() in ("1", "true", "yes")
    t0 = time.monotonic()
    summary = evaluate(chosen, args.url, telemetry, args.max_tokens, args.timeout, capture)
    telemetry.shutdown()
    summary["seconds"] = round(time.monotonic() - t0, 1)
    results = summary.pop("results")
    for r in results:
        if args.verbose or not r["pass"]:
            reasons = "; ".join(f"{c['type']}: {c['reason']}" for c in r["checks"])
            verdict = "PASS" if r["pass"] else "FAIL"
            print(f"  {verdict} {r['id']:<26} {r['seconds']:>5.1f}s  {reasons}")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
