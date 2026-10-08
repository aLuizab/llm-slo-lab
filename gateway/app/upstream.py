"""Streaming proxy to the OpenAI-compatible upstream, feeding a Measurement as bytes flow."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

import httpx

from app import semconv
from app.config import Settings
from app.measure import Measurement


class UpstreamError(Exception):
    """Upstream answered with an error status before any content was produced."""

    def __init__(self, status: int, body: bytes):
        super().__init__(f"upstream status {status}")
        self.status = status
        self.body = body


class UpstreamTimeout(Exception):
    pass


def _parse_chunk(payload: str, m: Measurement) -> None:
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return
    if not isinstance(chunk, dict):
        return
    if chunk.get("usage"):
        m.usage = chunk["usage"]
    if chunk.get("model"):
        m.response_model = chunk["model"]
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if content:
            m.mark_content(content)
        if choice.get("finish_reason"):
            m.finish_reason = choice["finish_reason"]


class Upstream:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
        self.base = settings.upstream_url

    def _timeout(self) -> httpx.Timeout:
        s = self.settings
        return httpx.Timeout(connect=s.connect_timeout_s, read=s.ttft_timeout_s, write=10, pool=5)

    async def models(self) -> httpx.Response:
        return await self.client.get(f"{self.base}/v1/models", timeout=self._timeout())

    async def chat(
        self, body: dict, m: Measurement, headers: dict[str, str] | None = None
    ) -> tuple[int, dict]:
        """Non-streaming completion. Returns (status, json)."""
        try:
            r = await self.client.post(
                f"{self.base}/v1/chat/completions",
                json=body,
                headers=headers,
                timeout=self._timeout(),
            )
        except httpx.TimeoutException as e:
            m.fail(semconv.OUTCOME_TIMEOUT, type(e).__name__)
            raise UpstreamTimeout from e
        except httpx.HTTPError as e:
            m.fail(semconv.OUTCOME_UPSTREAM_ERROR, type(e).__name__)
            raise UpstreamError(502, str(e).encode()) from e
        m.upstream_status = r.status_code
        if r.status_code >= 400:
            m.fail(semconv.OUTCOME_UPSTREAM_ERROR, f"http_{r.status_code}")
            raise UpstreamError(r.status_code, r.content)
        data = r.json()
        if data.get("usage"):
            m.usage = data["usage"]
        m.response_model = data.get("model")
        for choice in data.get("choices") or []:
            content = (choice.get("message") or {}).get("content")
            if content:
                m.mark_content(content)
            if choice.get("finish_reason"):
                m.finish_reason = choice["finish_reason"]
        return r.status_code, data

    async def open_stream(
        self, body: dict, m: Measurement, headers: dict[str, str] | None = None
    ) -> tuple[httpx.Response, AsyncIterator[str]]:
        """Open the upstream stream and wait for its first SSE line.

        Waiting for the first line before returning lets the gateway answer with a real
        status code (502/504) when the upstream fails or times out before producing anything.
        The first line is forwarded immediately by `forward`, so client-observed TTFT is not
        inflated.
        """
        req = self.client.build_request(
            "POST",
            f"{self.base}/v1/chat/completions",
            json=body,
            headers=headers,
            timeout=self._timeout(),
        )
        try:
            r = await self.client.send(req, stream=True)
        except httpx.TimeoutException as e:
            m.fail(semconv.OUTCOME_TIMEOUT, type(e).__name__)
            raise UpstreamTimeout from e
        except httpx.HTTPError as e:
            m.fail(semconv.OUTCOME_UPSTREAM_ERROR, type(e).__name__)
            raise UpstreamError(502, str(e).encode()) from e
        m.upstream_status = r.status_code
        if r.status_code >= 400:
            content = await r.aread()
            await r.aclose()
            m.fail(semconv.OUTCOME_UPSTREAM_ERROR, f"http_{r.status_code}")
            raise UpstreamError(r.status_code, content)

        lines = r.aiter_lines()
        try:
            first = await anext(lines)
        except StopAsyncIteration:
            first = ""
        except httpx.TimeoutException as e:
            await r.aclose()
            m.fail(semconv.OUTCOME_TIMEOUT, type(e).__name__)
            raise UpstreamTimeout from e
        except httpx.HTTPError as e:
            await r.aclose()
            m.fail(semconv.OUTCOME_UPSTREAM_ERROR, type(e).__name__)
            raise UpstreamError(502, str(e).encode()) from e

        async def chained() -> AsyncIterator[str]:
            yield first
            async for line in lines:
                yield line

        return r, chained()

    async def forward(
        self, r: httpx.Response, lines: AsyncIterator[str], m: Measurement
    ) -> AsyncIterator[bytes]:
        """Re-emit upstream SSE lines to the client while parsing them into the Measurement.

        Errors after the first byte cannot change the HTTP status any more; they are sent as
        a final SSE `error` event and recorded in the Measurement.
        """
        deadline = m.started + self.settings.total_timeout_s
        throttle = self.settings.fault_inter_token_delay_ms / 1000.0
        try:
            async for line in lines:
                if not line:
                    continue
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        yield b"data: [DONE]\n\n"
                        break
                    if throttle > 0:  # chaos: slow the stream before the chunk is counted
                        await asyncio.sleep(throttle)
                    _parse_chunk(payload, m)
                    yield f"data: {payload}\n\n".encode()
                else:
                    yield f"{line}\n\n".encode()
                if time.monotonic() > deadline:
                    m.fail(semconv.OUTCOME_TIMEOUT, "total_timeout")
                    yield _sse_error("gateway total timeout exceeded", "timeout")
                    break
        except httpx.TimeoutException as e:
            m.fail(semconv.OUTCOME_TIMEOUT, type(e).__name__)
            yield _sse_error("upstream timed out mid-stream", "timeout")
        except httpx.HTTPError as e:
            m.fail(semconv.OUTCOME_UPSTREAM_ERROR, type(e).__name__)
            yield _sse_error(f"upstream error: {type(e).__name__}", "upstream_error")
        finally:
            await r.aclose()


def _sse_error(message: str, code: str) -> bytes:
    payload = json.dumps({"error": {"message": message, "code": code}})
    return f"event: error\ndata: {payload}\n\n".encode()
