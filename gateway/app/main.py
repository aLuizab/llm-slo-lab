"""llm-gateway: OpenAI-compatible proxy that measures LLM SLIs.

Routes
  POST /v1/chat/completions (also /openai/v1/chat/completions)  streaming and non-streaming
  GET  /v1/models                                                proxied
  GET  /healthz, /readyz
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace import SpanKind, Status, StatusCode

from app import semconv
from app.config import Settings
from app.measure import Measurement, RequestResult, finalize
from app.telemetry import Telemetry, set_global
from app.tokens import TokenCounter
from app.upstream import Upstream, UpstreamError, UpstreamTimeout

log = logging.getLogger("llm-gateway")


def create_app(settings: Settings | None = None, telemetry: Telemetry | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    owns_telemetry = telemetry is None
    telemetry = telemetry or Telemetry(settings)
    if owns_telemetry:
        set_global(telemetry)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50)
        )
        app.state.upstream = Upstream(settings, app.state.client)
        yield
        await app.state.client.aclose()
        if owns_telemetry:
            telemetry.shutdown()

    app = FastAPI(title="llm-gateway", lifespan=lifespan)
    app.state.settings = settings
    app.state.telemetry = telemetry
    app.state.tokens = TokenCounter(settings.tokenizer_path)
    app.state.in_flight = 0
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=telemetry.tracer_provider, excluded_urls="healthz,readyz"
    )

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/readyz")
    async def readyz():
        return {"ok": True, "upstream": settings.upstream_url}

    @app.get("/v1/models")
    @app.get("/openai/v1/models")
    async def models(request: Request):
        r = await request.app.state.upstream.models()
        return JSONResponse(status_code=r.status_code, content=r.json())

    @app.post("/v1/chat/completions")
    @app.post("/openai/v1/chat/completions")
    async def chat_completions(request: Request):
        return await _handle_chat(request)

    return app


def _base_attributes(settings: Settings, body: dict) -> dict:
    host, port = settings.upstream_host
    return {
        semconv.ATTR_OPERATION_NAME: semconv.OPERATION_CHAT,
        semconv.ATTR_PROVIDER_NAME: settings.provider_name,
        semconv.ATTR_REQUEST_MODEL: body.get("model") or settings.model_name,
        semconv.ATTR_SERVER_ADDRESS: host,
        semconv.ATTR_SERVER_PORT: port,
    }


def _record(
    telemetry: Telemetry, settings: Settings, attrs: dict, res: RequestResult, stream: bool
):
    inst = telemetry.instruments
    common = dict(attrs)
    if res.response_model:
        common[semconv.ATTR_RESPONSE_MODEL] = res.response_model
    if res.error_type:
        common[semconv.ATTR_ERROR_TYPE] = res.error_type
    outcome_attrs = {**common, semconv.ATTR_OUTCOME: res.outcome, semconv.ATTR_STREAM: stream}

    inst.requests.add(1, outcome_attrs)
    inst.operation_duration.record(res.duration_s, outcome_attrs)
    inst.server_request_duration.record(res.duration_s, outcome_attrs)
    if res.ttft_s is not None:
        inst.time_to_first_token.record(res.ttft_s, common)
    if res.tpot_s is not None:
        inst.time_per_output_token.record(res.tpot_s, common)
    if res.output_tokens_per_s is not None:
        inst.output_token_rate.record(res.output_tokens_per_s, common)
    token_attrs = {**common, semconv.ATTR_TOKEN_SOURCE: res.token_source}
    inst.token_usage.record(
        res.input_tokens, {**token_attrs, semconv.ATTR_TOKEN_TYPE: semconv.TOKEN_TYPE_INPUT}
    )
    inst.token_usage.record(
        res.output_tokens, {**token_attrs, semconv.ATTR_TOKEN_TYPE: semconv.TOKEN_TYPE_OUTPUT}
    )
    cm = settings.cost_model
    inst.request_cost.record(
        cm.amortized(res.duration_s, res.concurrency),
        {**common, semconv.ATTR_COST_MODEL: "amortized"},
    )
    inst.request_cost.record(
        cm.per_token(res.input_tokens, res.output_tokens),
        {**common, semconv.ATTR_COST_MODEL: "per_token"},
    )


def _span_response_attributes(
    span: trace.Span, res: RequestResult, settings: Settings, m: Measurement
):
    span.set_attribute(semconv.ATTR_USAGE_INPUT_TOKENS, res.input_tokens)
    span.set_attribute(semconv.ATTR_USAGE_OUTPUT_TOKENS, res.output_tokens)
    span.set_attribute(semconv.ATTR_OUTCOME, res.outcome)
    span.set_attribute(semconv.ATTR_TOKEN_SOURCE, res.token_source)
    if res.finish_reason:
        span.set_attribute(semconv.ATTR_RESPONSE_FINISH_REASONS, [res.finish_reason])
    if res.response_model:
        span.set_attribute(semconv.ATTR_RESPONSE_MODEL, res.response_model)
    if res.ttft_s is not None:
        span.set_attribute("llm_slo.ttft_s", res.ttft_s)
    if res.error_type:
        span.set_attribute(semconv.ATTR_ERROR_TYPE, res.error_type)
        span.set_status(Status(StatusCode.ERROR, res.error_type))
    if settings.capture_content and m.output_text:
        span.set_attribute(
            semconv.ATTR_OUTPUT_MESSAGES,
            json.dumps(
                [
                    {
                        "role": "assistant",
                        "parts": [{"type": "text", "content": m.output_text}],
                        "finish_reason": res.finish_reason or "",
                    }
                ]
            ),
        )


async def _handle_chat(request: Request):
    app = request.app
    settings: Settings = app.state.settings
    telemetry: Telemetry = app.state.telemetry
    inst = telemetry.instruments

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": {"message": "invalid JSON body"}})
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return JSONResponse(status_code=400, content={"error": {"message": "messages[] required"}})

    body.setdefault("model", settings.model_name)
    stream = bool(body.get("stream"))
    # x-mock-* headers are forwarded so tests and the offline demo can steer mock-llm per
    # request; the real model ignores them.
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower().startswith("x-mock-")}
    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens")
    if settings.system_prompt and not any(m.get("role") == "system" for m in body["messages"]):
        body["messages"] = [
            {"role": "system", "content": settings.system_prompt},
            *body["messages"],
        ]

    attrs = _base_attributes(settings, body)
    m = Measurement(concurrency_at_start=app.state.in_flight + 1)
    app.state.in_flight += 1
    inst.in_flight.add(1, {semconv.ATTR_PROVIDER_NAME: settings.provider_name})

    span = telemetry.tracer.start_span(
        f"{semconv.OPERATION_CHAT} {attrs[semconv.ATTR_REQUEST_MODEL]}",
        kind=SpanKind.CLIENT,
        attributes={
            **attrs,
            semconv.ATTR_STREAM: stream,
            **({semconv.ATTR_REQUEST_MAX_TOKENS: int(max_tokens)} if max_tokens else {}),
            **(
                {semconv.ATTR_REQUEST_TEMPERATURE: float(body["temperature"])}
                if body.get("temperature") is not None
                else {}
            ),
        },
    )
    if settings.capture_content:
        span.set_attribute(semconv.ATTR_INPUT_MESSAGES, json.dumps(body["messages"]))
        if settings.system_prompt:
            span.set_attribute(semconv.ATTR_SYSTEM_INSTRUCTIONS, settings.system_prompt)

    def finish() -> RequestResult:
        app.state.in_flight -= 1
        inst.in_flight.add(-1, {semconv.ATTR_PROVIDER_NAME: settings.provider_name})
        res = finalize(
            m, body["messages"], int(max_tokens) if max_tokens else None, app.state.tokens
        )
        _record(telemetry, settings, attrs, res, stream)
        _span_response_attributes(span, res, settings, m)
        span.end()
        return res

    # --- fault injection (chaos toggles) ---------------------------------------------
    if settings.fault_extra_latency_ms > 0:
        await asyncio.sleep(settings.fault_extra_latency_ms / 1000)
    if settings.fault_error_rate > 0 and random.random() < settings.fault_error_rate:
        m.fail(semconv.OUTCOME_INJECTED_ERROR, "injected_error")
        finish()
        return JSONResponse(
            status_code=503, content=_err("injected failure (FAULT_ERROR_RATE)", "injected")
        )

    upstream: Upstream = app.state.upstream
    with trace.use_span(span, end_on_exit=False):
        if not stream:
            try:
                status, data = await upstream.chat(body, m, fwd_headers)
            except UpstreamTimeout:
                finish()
                return JSONResponse(status_code=504, content=_err("upstream timeout", "timeout"))
            except UpstreamError as e:
                finish()
                return JSONResponse(status_code=_status_for(e), content=_err_body(e))
            finish()
            return JSONResponse(status_code=status, content=data)

        try:
            r, lines = await upstream.open_stream(body, m, fwd_headers)
        except UpstreamTimeout:
            finish()
            return JSONResponse(status_code=504, content=_err("upstream timeout", "timeout"))
        except UpstreamError as e:
            finish()
            return JSONResponse(status_code=_status_for(e), content=_err_body(e))

    async def body_iter():
        try:
            async for chunk in upstream.forward(r, lines, m):
                yield chunk
        finally:
            finish()

    return StreamingResponse(
        body_iter(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


def _err(message: str, code: str) -> dict:
    return {"error": {"message": message, "code": code}}


def _status_for(e: UpstreamError) -> int:
    """5xx and transport errors become 502; 4xx from the upstream are passed through."""
    return 502 if e.status >= 500 else e.status


def _err_body(e: UpstreamError) -> dict:
    try:
        data = json.loads(e.body)
        if isinstance(data, dict) and "error" in data:
            return data
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return _err(f"upstream returned {e.status}", "upstream_error")


app = create_app()
