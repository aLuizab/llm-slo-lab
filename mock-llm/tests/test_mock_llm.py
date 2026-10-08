import json

import httpx
import pytest

from mock_llm.main import MockOptions, create_app, generate_words

FAST = MockOptions(ttft_ms=5, inter_token_ms=1, output_tokens=8)


@pytest.fixture
async def client():
    app = create_app(FAST)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://mock"
    ) as c:
        yield c


def _text(events, drop_tail: int) -> str:
    return "".join(
        e["choices"][0]["delta"].get("content", "")
        for e in events[:-drop_tail]
        if isinstance(e, dict) and e["choices"]
    )


async def _collect(resp: httpx.Response) -> list[dict | str]:
    events = []
    async for line in resp.aiter_lines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            events.append("[DONE]" if payload == "[DONE]" else json.loads(payload))
    return events


async def test_stream_shape_and_usage(client):
    body = {"model": "m", "messages": [{"role": "user", "content": "hi there"}], "stream": True}
    async with client.stream("POST", "/v1/chat/completions", json=body) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = await _collect(resp)
    assert events[-1] == "[DONE]"
    assert len(_text(events, 2).split()) == 8
    assert events[-3]["choices"][0]["finish_reason"] == "stop"
    usage = events[-2]["usage"]
    assert usage == {"prompt_tokens": 2, "completion_tokens": 8, "total_tokens": 10}


async def test_no_usage_header_mimics_hf_backend(client):
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    async with client.stream(
        "POST", "/v1/chat/completions", json=body, headers={"x-mock-include-usage": "false"}
    ) as resp:
        events = await _collect(resp)
    assert all(not (isinstance(e, dict) and e.get("usage")) for e in events)


async def test_max_tokens_caps_and_sets_length(client):
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 3}
    resp = await client.post("/v1/chat/completions", json=body)
    data = resp.json()
    assert len(data["choices"][0]["message"]["content"].split()) == 3
    assert data["choices"][0]["finish_reason"] == "length"
    assert data["usage"]["completion_tokens"] == 3


async def test_error_rate(client):
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    resp = await client.post("/v1/chat/completions", json=body, headers={"x-mock-error-rate": "1"})
    assert resp.status_code == 500
    assert resp.json()["error"]["type"] == "server_error"


async def test_empty_output(client):
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    async with client.stream(
        "POST", "/v1/chat/completions", json=body, headers={"x-mock-output-tokens": "0"}
    ) as resp:
        events = await _collect(resp)
    assert _text(events, 1) == ""


def test_echo_mode():
    body = {"messages": [{"role": "user", "content": "alpha beta gamma"}]}
    assert generate_words(body, 3, "echo") == ["alpha", "beta", "gamma"]
    assert len(generate_words(body, 10, "echo")) == 10
    assert generate_words(body, 0, "lorem") == []
