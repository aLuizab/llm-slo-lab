"""mock-llm: an OpenAI-compatible chat completions server that streams fake tokens.

Used in CI and as the offline demo fallback. Every timing knob has an env default and a
per-request header override, so tests can exercise slow TTFT, errors, empty output and
missing `usage` without restarting the server.

Env (defaults in parentheses):
  MOCK_MODEL (mock-llm)            served model id
  MOCK_TTFT_MS (200)               delay before the first content chunk
  MOCK_INTER_TOKEN_MS (20)         delay between content chunks
  MOCK_ERROR_RATE (0.0)            probability of a 500 before any output
  MOCK_OUTPUT_TOKENS (40)          output length, capped by the request's max_tokens
  MOCK_INCLUDE_USAGE (true)        send a final chunk with `usage` (like vLLM); false mimics the
                                   KServe HF backend, which sends none
  MOCK_RESPONSE_MODE (lorem)       lorem | echo (echo repeats the last user message)
  MOCK_SEED                        seed for the error-rate RNG (deterministic tests)
Headers: x-mock-ttft-ms, x-mock-inter-token-ms, x-mock-error-rate, x-mock-output-tokens,
         x-mock-include-usage, x-mock-response-mode
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

LOREM = (
    "An SLO is a target for a service level indicator measured over a window. "
    "Error budgets turn reliability into a currency teams can spend. "
    "Burn rate alerts page on the speed at which the budget is consumed. "
    "Time to first token is what a user feels when a model starts answering. "
    "Tokens per second is what a user feels while the answer is streaming. "
).split()


@dataclass(frozen=True)
class MockOptions:
    model: str = "mock-llm"
    ttft_ms: float = 200.0
    inter_token_ms: float = 20.0
    error_rate: float = 0.0
    output_tokens: int = 40
    include_usage: bool = True
    response_mode: str = "lorem"

    @classmethod
    def from_env(cls) -> MockOptions:
        env = os.environ.get
        return cls(
            model=env("MOCK_MODEL", "mock-llm"),
            ttft_ms=float(env("MOCK_TTFT_MS", "200")),
            inter_token_ms=float(env("MOCK_INTER_TOKEN_MS", "20")),
            error_rate=float(env("MOCK_ERROR_RATE", "0")),
            output_tokens=int(env("MOCK_OUTPUT_TOKENS", "40")),
            include_usage=env("MOCK_INCLUDE_USAGE", "true").lower() in ("1", "true", "yes"),
            response_mode=env("MOCK_RESPONSE_MODE", "lorem"),
        )

    def with_headers(self, headers) -> MockOptions:
        h = {k.lower(): v for k, v in headers.items()}
        updates = {}
        if "x-mock-ttft-ms" in h:
            updates["ttft_ms"] = float(h["x-mock-ttft-ms"])
        if "x-mock-inter-token-ms" in h:
            updates["inter_token_ms"] = float(h["x-mock-inter-token-ms"])
        if "x-mock-error-rate" in h:
            updates["error_rate"] = float(h["x-mock-error-rate"])
        if "x-mock-output-tokens" in h:
            updates["output_tokens"] = int(h["x-mock-output-tokens"])
        if "x-mock-include-usage" in h:
            updates["include_usage"] = h["x-mock-include-usage"].lower() in ("1", "true", "yes")
        if "x-mock-response-mode" in h:
            updates["response_mode"] = h["x-mock-response-mode"]
        return replace(self, **updates)


def _last_user_message(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):  # OpenAI content parts
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            return str(content)
    return ""


def generate_words(body: dict, n: int, mode: str) -> list[str]:
    """Deterministic fake output of exactly n words (one word per chunk)."""
    if n <= 0:
        return []
    if mode == "echo":
        words = _last_user_message(body.get("messages", [])).split()
        words = (words + LOREM)[:n] if words else LOREM[:n]
        while len(words) < n:
            words += LOREM[: n - len(words)]
        return words
    out = []
    while len(out) < n:
        out += LOREM[: n - len(out)]
    return out


def _count_prompt_words(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        total += len(str(content).split())
    return total


def create_app(defaults: MockOptions | None = None) -> FastAPI:
    defaults = defaults or MockOptions.from_env()
    app = FastAPI(title="mock-llm")
    app.state.defaults = defaults
    app.state.rng = random.Random(os.environ.get("MOCK_SEED") or None)
    app.state.last_request = None

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/_debug/last_request")
    async def last_request():
        return app.state.last_request

    @app.get("/v1/models")
    @app.get("/openai/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": defaults.model, "object": "model"}]}

    @app.post("/v1/chat/completions")
    @app.post("/openai/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        app.state.last_request = body
        opts = defaults.with_headers(request.headers)
        if opts.error_rate > 0 and app.state.rng.random() < opts.error_rate:
            return JSONResponse(
                status_code=500,
                content={"error": {"message": "mock upstream failure", "type": "server_error"}},
            )
        max_tokens = body.get("max_tokens") or body.get("max_completion_tokens")
        n = opts.output_tokens if max_tokens is None else min(opts.output_tokens, int(max_tokens))
        words = generate_words(body, n, opts.response_mode)
        finish = "length" if max_tokens is not None and n >= int(max_tokens) else "stop"
        prompt_tokens = _count_prompt_words(body.get("messages", []))
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": len(words),
            "total_tokens": prompt_tokens + len(words),
        }
        rid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())
        model = body.get("model") or opts.model

        if body.get("stream"):
            include_usage = opts.include_usage and bool(
                (body.get("stream_options") or {}).get("include_usage", True)
            )
            return StreamingResponse(
                _stream(rid, created, model, words, finish, usage, include_usage, opts),
                media_type="text/event-stream",
                headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
            )

        await asyncio.sleep((opts.ttft_ms + opts.inter_token_ms * max(len(words) - 1, 0)) / 1000)
        text = " ".join(words)
        return {
            "id": rid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish,
                }
            ],
            "usage": usage,
        }

    return app


async def _stream(
    rid: str,
    created: int,
    model: str,
    words: list[str],
    finish: str,
    usage: dict,
    include_usage: bool,
    opts: MockOptions,
) -> AsyncIterator[bytes]:
    def chunk(delta: dict, finish_reason: str | None = None, **extra) -> bytes:
        payload = {
            "id": rid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            **extra,
        }
        return f"data: {json.dumps(payload)}\n\n".encode()

    await asyncio.sleep(opts.ttft_ms / 1000)
    yield chunk({"role": "assistant", "content": ""})
    for i, word in enumerate(words):
        if i:
            await asyncio.sleep(opts.inter_token_ms / 1000)
        text = word if i == len(words) - 1 else word + " "
        yield chunk({"content": text})
    yield chunk({}, finish_reason=finish)
    if include_usage:
        payload = {
            "id": rid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [],
            "usage": usage,
        }
        yield f"data: {json.dumps(payload)}\n\n".encode()
    yield b"data: [DONE]\n\n"


app = create_app()
