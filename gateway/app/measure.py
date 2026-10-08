"""Per-request measurement: what happened, when, and how it is classified."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app import semconv
from app.tokens import SOURCE_UPSTREAM, TokenCounter


@dataclass
class Measurement:
    started: float = field(default_factory=time.monotonic)
    first_token_at: float | None = None
    ended: float | None = None
    content_chunks: int = 0
    output_parts: list[str] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict | None = None
    response_model: str | None = None
    outcome: str = semconv.OUTCOME_SUCCESS
    error_type: str | None = None
    upstream_status: int | None = None
    concurrency_at_start: int = 1

    def mark_content(self, text: str) -> None:
        now = time.monotonic()
        if self.first_token_at is None:
            self.first_token_at = now
        self.content_chunks += 1
        self.output_parts.append(text)

    def fail(self, outcome: str, error_type: str) -> None:
        self.outcome = outcome
        self.error_type = error_type

    @property
    def output_text(self) -> str:
        return "".join(self.output_parts)

    @property
    def duration(self) -> float:
        return (self.ended or time.monotonic()) - self.started

    @property
    def ttft(self) -> float | None:
        return None if self.first_token_at is None else self.first_token_at - self.started


@dataclass(frozen=True)
class RequestResult:
    outcome: str
    error_type: str | None
    duration_s: float
    ttft_s: float | None
    tpot_s: float | None
    output_tokens_per_s: float | None
    input_tokens: int
    output_tokens: int
    token_source: str
    finish_reason: str | None
    response_model: str | None
    concurrency: int

    @property
    def is_bad(self) -> bool:
        return self.outcome in semconv.BAD_OUTCOMES


def finalize(
    m: Measurement, messages: list[dict], max_tokens: int | None, counter: TokenCounter
) -> RequestResult:
    """Freeze the measurement into a RequestResult, classifying empty/truncated responses."""
    m.ended = m.ended or time.monotonic()

    if m.usage and "completion_tokens" in m.usage:
        input_tokens = int(m.usage.get("prompt_tokens", 0))
        output_tokens = int(m.usage.get("completion_tokens", 0))
        source = SOURCE_UPSTREAM
    else:
        input_tokens = counter.count_messages(messages)
        output_tokens = counter.count(m.output_text)
        source = counter.source

    if m.outcome == semconv.OUTCOME_SUCCESS:
        if output_tokens == 0 or not m.output_text.strip():
            m.fail(semconv.OUTCOME_EMPTY, "empty_response")
        elif m.finish_reason == "length" and max_tokens is not None and output_tokens >= max_tokens:
            # The HF backend reports `length` even when it stopped on its own (ADR-013), so
            # truncation also requires hitting the requested cap.
            m.fail(semconv.OUTCOME_TRUNCATED, "truncated_response")

    tpot = rate = None
    if m.first_token_at is not None and output_tokens > 1:
        gen_time = m.ended - m.first_token_at
        if gen_time > 0:
            tpot = gen_time / (output_tokens - 1)
            rate = (output_tokens - 1) / gen_time

    return RequestResult(
        outcome=m.outcome,
        error_type=m.error_type,
        duration_s=m.ended - m.started,
        ttft_s=m.ttft,
        tpot_s=tpot,
        output_tokens_per_s=rate,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        token_source=source,
        finish_reason=m.finish_reason,
        response_model=m.response_model,
        concurrency=m.concurrency_at_start,
    )
