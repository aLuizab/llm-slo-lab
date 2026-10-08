import json
from pathlib import Path

import httpx
import pytest

from app import semconv
from app.cost import CostModel
from app.tokens import SOURCE_ESTIMATE, SOURCE_TOKENIZER, SOURCE_UPSTREAM, TokenCounter
from tests.conftest import TOKENIZER, metrics_by_name, run_server

BODY = {
    "model": "mock-llm",
    "messages": [{"role": "user", "content": "Explain SLOs in one sentence."}],
    "stream": True,
    "max_tokens": 64,
}


async def _stream(client: httpx.AsyncClient, body=BODY, headers=None):
    events, status = [], None
    async with client.stream("POST", "/v1/chat/completions", json=body, headers=headers or {}) as r:
        status = r.status_code
        async for line in r.aiter_lines():
            if line.startswith("data:"):
                p = line[5:].strip()
                events.append("[DONE]" if p == "[DONE]" else json.loads(p))
            elif line.startswith("event:"):
                events.append(line)
    return status, events


def _text(events) -> str:
    return "".join(
        e["choices"][0]["delta"].get("content", "")
        for e in events[:-1]
        if isinstance(e, dict) and e["choices"]
    )


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


def _point(points, **attrs):
    for p in points:
        if all(p.attributes.get(k) == v for k, v in attrs.items()):
            return p
    raise AssertionError(f"no data point with {attrs}; have {[dict(p.attributes) for p in points]}")


async def test_streaming_passthrough_and_metrics(gateway):
    client, reader, spans = gateway
    status, events = await _stream(client)
    assert status == 200
    assert events[-1] == "[DONE]"
    assert len(_text(events).split()) == 12

    m = metrics_by_name(reader)
    for name in (
        semconv.METRIC_CLIENT_OPERATION_DURATION,
        semconv.METRIC_CLIENT_TOKEN_USAGE,
        semconv.METRIC_SERVER_REQUEST_DURATION,
        semconv.METRIC_SERVER_TIME_TO_FIRST_TOKEN,
        semconv.METRIC_SERVER_TIME_PER_OUTPUT_TOKEN,
        semconv.METRIC_REQUEST_COST,
        semconv.METRIC_OUTPUT_TOKEN_RATE,
        semconv.METRIC_REQUESTS,
        semconv.METRIC_REQUESTS_IN_FLIGHT,
    ):
        assert name in m, f"missing metric {name}"

    ttft = m[semconv.METRIC_SERVER_TIME_TO_FIRST_TOKEN][0]
    assert ttft.count == 1 and 0.03 <= ttft.sum < 1.0  # mock TTFT is 30 ms
    assert ttft.attributes[semconv.ATTR_OPERATION_NAME] == "chat"
    assert ttft.attributes[semconv.ATTR_PROVIDER_NAME] == "mock"
    assert ttft.attributes[semconv.ATTR_REQUEST_MODEL] == "mock-llm"
    assert ttft.attributes[semconv.ATTR_SERVER_ADDRESS] == "127.0.0.1"

    req = _point(m[semconv.METRIC_REQUESTS], **{semconv.ATTR_OUTCOME: "success"})
    assert req.value == 1 and req.attributes[semconv.ATTR_STREAM] is True
    assert m[semconv.METRIC_REQUESTS_IN_FLIGHT][0].value == 0  # back to zero after the request
    assert m[semconv.METRIC_SERVER_TIME_PER_OUTPUT_TOKEN][0].count == 1
    assert m[semconv.METRIC_OUTPUT_TOKEN_RATE][0].sum > 0

    # one CLIENT span named "chat <model>", content NOT captured by default
    chat_spans = [s for s in spans.get_finished_spans() if s.name == "chat mock-llm"]
    assert len(chat_spans) == 1
    s = chat_spans[0]
    assert s.attributes[semconv.ATTR_USAGE_OUTPUT_TOKENS] == 12
    assert s.attributes[semconv.ATTR_OUTCOME] == "success"
    assert semconv.ATTR_INPUT_MESSAGES not in s.attributes
    assert semconv.ATTR_OUTPUT_MESSAGES not in s.attributes


async def test_non_streaming(gateway):
    client, reader, _ = gateway
    r = await client.post("/v1/chat/completions", json={**BODY, "stream": False})
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"]
    m = metrics_by_name(reader)
    req = _point(m[semconv.METRIC_REQUESTS], **{semconv.ATTR_OUTCOME: "success"})
    assert req.attributes[semconv.ATTR_STREAM] is False
    # non-streaming: TTFT is the whole response, still recorded
    assert m[semconv.METRIC_SERVER_TIME_TO_FIRST_TOKEN][0].count == 1


async def test_upstream_error_is_bad_event(gateway):
    client, reader, spans = gateway
    status, _ = await _stream(client, headers={"x-mock-error-rate": "1"})
    assert status == 502
    m = metrics_by_name(reader)
    p = _point(m[semconv.METRIC_REQUESTS], **{semconv.ATTR_OUTCOME: semconv.OUTCOME_UPSTREAM_ERROR})
    assert p.attributes[semconv.ATTR_ERROR_TYPE] == "http_500"
    assert semconv.METRIC_SERVER_TIME_TO_FIRST_TOKEN not in m  # no first token, no TTFT sample
    span = next(s for s in spans.get_finished_spans() if s.name == "chat mock-llm")
    assert span.status.is_ok is False
    assert span.attributes[semconv.ATTR_ERROR_TYPE] == "http_500"


async def test_ttft_timeout(make_gateway):
    app, reader, _ = make_gateway(ttft_timeout_s=0.2)
    async with app.router.lifespan_context(app), _client(app) as c:
        status, _ = await _stream(c, headers={"x-mock-ttft-ms": "1500"})
    assert status == 504
    m = metrics_by_name(reader)
    p = _point(m[semconv.METRIC_REQUESTS], **{semconv.ATTR_OUTCOME: semconv.OUTCOME_TIMEOUT})
    assert p.attributes[semconv.ATTR_ERROR_TYPE] == "ReadTimeout"


async def test_total_timeout_mid_stream(make_gateway):
    app, reader, _ = make_gateway(total_timeout_s=0.3)
    async with app.router.lifespan_context(app), _client(app) as c:
        status, events = await _stream(
            c, headers={"x-mock-inter-token-ms": "100", "x-mock-output-tokens": "50"}
        )
    assert status == 200  # headers were already sent
    assert any(e == "event: error" for e in events)
    m = metrics_by_name(reader)
    _point(m[semconv.METRIC_REQUESTS], **{semconv.ATTR_OUTCOME: semconv.OUTCOME_TIMEOUT})


async def test_empty_response_is_bad_event(gateway):
    client, reader, _ = gateway
    status, _ = await _stream(client, headers={"x-mock-output-tokens": "0"})
    assert status == 200
    m = metrics_by_name(reader)
    _point(m[semconv.METRIC_REQUESTS], **{semconv.ATTR_OUTCOME: semconv.OUTCOME_EMPTY})


async def test_truncated_at_max_tokens_is_bad_event(gateway):
    client, reader, _ = gateway
    status, _ = await _stream(
        client, body={**BODY, "max_tokens": 5}, headers={"x-mock-output-tokens": "5"}
    )
    assert status == 200
    m = metrics_by_name(reader)
    _point(m[semconv.METRIC_REQUESTS], **{semconv.ATTR_OUTCOME: semconv.OUTCOME_TRUNCATED})


@pytest.mark.skipif(not Path(TOKENIZER).is_file(), reason="tokenizer.json not available")
async def test_token_counting_with_tokenizer_when_no_usage(gateway):
    client, reader, _ = gateway
    _, events = await _stream(client, headers={"x-mock-include-usage": "false"})
    text = _text(events)
    expected = TokenCounter(TOKENIZER).count(text)
    m = metrics_by_name(reader)
    out = _point(m[semconv.METRIC_CLIENT_TOKEN_USAGE], **{semconv.ATTR_TOKEN_TYPE: "output"})
    assert out.attributes[semconv.ATTR_TOKEN_SOURCE] == SOURCE_TOKENIZER
    assert out.sum == expected > 0
    inp = _point(m[semconv.METRIC_CLIENT_TOKEN_USAGE], **{semconv.ATTR_TOKEN_TYPE: "input"})
    assert inp.sum > 0


async def test_token_counting_uses_upstream_usage_when_present(gateway):
    client, reader, _ = gateway
    await _stream(client)  # mock sends usage: 12 completion tokens, 5 prompt words
    m = metrics_by_name(reader)
    out = _point(m[semconv.METRIC_CLIENT_TOKEN_USAGE], **{semconv.ATTR_TOKEN_TYPE: "output"})
    assert out.attributes[semconv.ATTR_TOKEN_SOURCE] == SOURCE_UPSTREAM
    assert out.sum == 12
    inp = _point(m[semconv.METRIC_CLIENT_TOKEN_USAGE], **{semconv.ATTR_TOKEN_TYPE: "input"})
    assert inp.sum == 5


async def test_estimate_fallback_without_tokenizer(make_gateway):
    app, reader, _ = make_gateway(tokenizer_path="/nonexistent/tokenizer.json")
    async with app.router.lifespan_context(app), _client(app) as c:
        await _stream(c, headers={"x-mock-include-usage": "false"})
    m = metrics_by_name(reader)
    out = _point(m[semconv.METRIC_CLIENT_TOKEN_USAGE], **{semconv.ATTR_TOKEN_TYPE: "output"})
    assert out.attributes[semconv.ATTR_TOKEN_SOURCE] == SOURCE_ESTIMATE


def test_cost_math():
    cm = CostModel(node_cost_per_hour=3.6, input_per_1k=0.1, output_per_1k=0.2)
    assert cm.amortized(duration_s=10, concurrency=1) == pytest.approx(0.01)
    assert cm.amortized(duration_s=10, concurrency=4) == pytest.approx(0.0025)
    assert cm.amortized(duration_s=10, concurrency=0) == pytest.approx(0.01)
    assert cm.per_token(input_tokens=500, output_tokens=1000) == pytest.approx(0.05 + 0.2)


def test_cost_model_from_file(tmp_path):
    f = tmp_path / "cost.yaml"
    f.write_text(
        "amortized: {node_cost_per_hour: 1.0}\nper_token: {input_per_1k: 0.5, output_per_1k: 0.7}\n"
    )
    cm = CostModel.from_file(f)
    assert (cm.node_cost_per_hour, cm.input_per_1k, cm.output_per_1k) == (1.0, 0.5, 0.7)


async def test_cost_recorded_for_both_models(gateway):
    client, reader, _ = gateway
    await _stream(client)
    m = metrics_by_name(reader)
    amortized = _point(m[semconv.METRIC_REQUEST_COST], **{semconv.ATTR_COST_MODEL: "amortized"})
    per_token = _point(m[semconv.METRIC_REQUEST_COST], **{semconv.ATTR_COST_MODEL: "per_token"})
    assert amortized.sum > 0 and per_token.sum > 0


async def test_fault_injection_errors(make_gateway):
    app, reader, _ = make_gateway(fault_error_rate=1.0)
    async with app.router.lifespan_context(app), _client(app) as c:
        status, _ = await _stream(c)
    assert status == 503
    m = metrics_by_name(reader)
    _point(m[semconv.METRIC_REQUESTS], **{semconv.ATTR_OUTCOME: semconv.OUTCOME_INJECTED_ERROR})


async def test_fault_injection_latency_inflates_ttft(make_gateway):
    app, reader, _ = make_gateway(fault_extra_latency_ms=250)
    async with app.router.lifespan_context(app), _client(app) as c:
        status, _ = await _stream(c)
    assert status == 200
    m = metrics_by_name(reader)
    assert m[semconv.METRIC_SERVER_TIME_TO_FIRST_TOKEN][0].sum >= 0.25


async def test_system_prompt_injected_once(make_gateway, mock_llm):
    app, _, _ = make_gateway(system_prompt="Be terse.")
    async with (
        app.router.lifespan_context(app),
        _client(app) as c,
        httpx.AsyncClient(base_url=mock_llm) as dbg,
    ):
        await _stream(c)
        seen = (await dbg.get("/_debug/last_request")).json()
        assert seen["messages"][0] == {"role": "system", "content": "Be terse."}
        own = [{"role": "system", "content": "X"}, *BODY["messages"]]
        await _stream(c, body={**BODY, "messages": own})
        seen = (await dbg.get("/_debug/last_request")).json()
        assert [m["role"] for m in seen["messages"]] == ["system", "user"]
        assert seen["messages"][0]["content"] == "X"


async def test_broken_prompt_template_fault(make_gateway, mock_llm):
    app, reader, _ = make_gateway(fault_prompt_template="broken")
    async with (
        app.router.lifespan_context(app),
        _client(app) as c,
        httpx.AsyncClient(base_url=mock_llm) as dbg,
    ):
        status, _ = await _stream(c)
        seen = (await dbg.get("/_debug/last_request")).json()
    assert status == 200
    assert "Lisbon" in seen["messages"][-1]["content"]  # user question replaced
    m = metrics_by_name(reader)
    _point(m[semconv.METRIC_REQUESTS], **{semconv.ATTR_OUTCOME: "success"})  # still a good event


async def test_inter_token_delay_fault_lowers_measured_rate(make_gateway):
    app, reader, _ = make_gateway(fault_inter_token_delay_ms=100)
    async with app.router.lifespan_context(app), _client(app) as c:
        status, _ = await _stream(c)
    assert status == 200
    m = metrics_by_name(reader)
    rate = m[semconv.METRIC_OUTPUT_TOKEN_RATE][0]
    assert rate.count == 1 and rate.sum < 12  # 12 tokens at >=100 ms each: well under 12 tok/s


async def test_content_capture_opt_in(make_gateway):
    app, _, spans = make_gateway(capture_content=True, system_prompt="Be terse.")
    async with app.router.lifespan_context(app), _client(app) as c:
        await _stream(c)
    span = next(s for s in spans.get_finished_spans() if s.name == "chat mock-llm")
    assert "Explain SLOs" in span.attributes[semconv.ATTR_INPUT_MESSAGES]
    assert json.loads(span.attributes[semconv.ATTR_OUTPUT_MESSAGES])[0]["role"] == "assistant"
    assert span.attributes[semconv.ATTR_SYSTEM_INSTRUCTIONS] == "Be terse."


async def test_in_flight_gauge_during_request(make_gateway):
    app, reader, _ = make_gateway()
    with run_server(app) as url:  # real server: ASGITransport would buffer the whole stream
        async with httpx.AsyncClient(base_url=url) as c:
            async with c.stream(
                "POST", "/v1/chat/completions", json=BODY, headers={"x-mock-ttft-ms": "300"}
            ) as r:
                assert r.status_code == 200
                m = metrics_by_name(reader)
                assert m[semconv.METRIC_REQUESTS_IN_FLIGHT][0].value == 1
                async for _ in r.aiter_lines():
                    pass
    assert metrics_by_name(reader)[semconv.METRIC_REQUESTS_IN_FLIGHT][0].value == 0


async def test_client_attribute_from_header(gateway):
    client, reader, spans = gateway
    await _stream(client)
    await _stream(client, headers={"x-llm-slo-client": "evaluator"})
    await _stream(client, headers={"x-llm-slo-client": "Not Valid!"})
    m = metrics_by_name(reader)
    by_client = {p.attributes[semconv.ATTR_CLIENT]: p.value for p in m[semconv.METRIC_REQUESTS]}
    assert by_client == {"user": 2, "evaluator": 1}
    # only one server span + one chat span per request (no per-chunk send/receive spans)
    names = sorted(s.name for s in spans.get_finished_spans())
    assert names == sorted(["POST /v1/chat/completions", "chat mock-llm"] * 3)


async def test_bad_request(gateway):
    client, _, _ = gateway
    r = await client.post("/v1/chat/completions", json={"model": "x"})
    assert r.status_code == 400
