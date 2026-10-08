#!/usr/bin/env python3
"""Async streaming load generator for the llm-gateway.

Keeps N concurrent streaming chat completions in flight and reports TTFT, tokens/s and
outcomes every few seconds. A ramp expresses concurrency over time, e.g. "1:60,4:120,1:60"
means 1 in flight for 60 s, then 4 for 120 s, then 1 for 60 s (used for the KEDA demo).

  uv run loadgen.py --url http://127.0.0.1:30080 --concurrency 2 --duration 60
  uv run loadgen.py --url http://127.0.0.1:30080 --ramp 1:60,6:180,1:60
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from dataclasses import dataclass, field

import httpx

PROMPTS = [
    "Explain in two sentences what an SLO is.",
    "What is an error budget? Answer briefly.",
    "Give three reasons to measure time to first token for a chat model.",
    "Summarize the difference between latency and throughput in one paragraph.",
    "Write one sentence about why alerts should be based on burn rate.",
    "List two risks of autoscaling on CPU for LLM inference.",
    "Reply only with a JSON object with keys city and country for Barcelona.",
    "What does KEDA do? One sentence.",
    "Name two things OpenTelemetry can export.",
    "In one sentence, what is a histogram bucket?",
]


@dataclass
class Sample:
    ttft: float | None
    total: float
    tokens: int
    outcome: str


@dataclass
class Stats:
    samples: list[Sample] = field(default_factory=list)
    in_flight: int = 0

    def window(self, since: float) -> list[Sample]:
        return [s for s in self.samples if s.total >= 0 and s._ts >= since]  # type: ignore[attr-defined]


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    k = max(0, min(len(values) - 1, round(p / 100 * (len(values) - 1))))
    return values[k]


async def one_request(client: httpx.AsyncClient, url: str, model: str, max_tokens: int) -> Sample:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": random.choice(PROMPTS)}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": True,
    }
    t0 = time.monotonic()
    ttft = None
    tokens = 0
    outcome = "success"
    try:
        async with client.stream("POST", f"{url}/v1/chat/completions", json=body) as r:
            if r.status_code >= 400:
                outcome = f"http_{r.status_code}"
            else:
                async for line in r.aiter_lines():
                    if line.startswith("event: error"):
                        outcome = "stream_error"
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for ch in chunk.get("choices") or []:
                        if (ch.get("delta") or {}).get("content"):
                            if ttft is None:
                                ttft = time.monotonic() - t0
                            tokens += 1
                if outcome == "success" and tokens == 0:
                    outcome = "empty"
    except httpx.TimeoutException:
        outcome = "timeout"
    except httpx.HTTPError as e:
        outcome = type(e).__name__
    return Sample(ttft=ttft, total=time.monotonic() - t0, tokens=tokens, outcome=outcome)


async def worker(
    stop: asyncio.Event, gate: asyncio.Semaphore, client, url, model, max_tokens, stats: Stats
):
    while not stop.is_set():
        async with gate:
            if stop.is_set():
                return
            stats.in_flight += 1
            try:
                s = await one_request(client, url, model, max_tokens)
            finally:
                stats.in_flight -= 1
            s._ts = time.monotonic()  # type: ignore[attr-defined]
            stats.samples.append(s)
            if s.outcome != "success" and s.total < 1.0:
                # a fast failure (connection refused/reset) must not turn into a tight loop
                await asyncio.sleep(1.0)


def report(stats: Stats, since: float, label: str) -> None:
    w = stats.window(since)
    if not w:
        print(f"{label}: no completed requests yet (in flight {stats.in_flight})", flush=True)
        return
    ttfts = [s.ttft for s in w if s.ttft is not None]
    rates = [
        (s.tokens - 1) / (s.total - s.ttft)
        for s in w
        if s.ttft and s.tokens > 1 and s.total > s.ttft
    ]
    bad = sum(1 for s in w if s.outcome != "success")
    print(
        f"{label}: n={len(w)} in_flight={stats.in_flight} "
        f"ttft p50={pct(ttfts, 50):.2f}s p95={pct(ttfts, 95):.2f}s "
        f"tok/s mean={statistics.fmean(rates) if rates else float('nan'):.1f} "
        f"total p50={pct([s.total for s in w], 50):.1f}s bad={bad} ({100 * bad / len(w):.0f}%)",
        flush=True,
    )


async def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", default="http://127.0.0.1:30080")
    ap.add_argument("--model", default=None, help="default: first model from /v1/models")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--duration", type=int, default=60, help="seconds (ignored with --ramp)")
    ap.add_argument("--ramp", default=None, help="phases 'concurrency:seconds,...'")
    # Answers cut at max_tokens count as *truncated* (a bad event), so leave room: the 0.5B
    # model rarely stops early.
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--report-every", type=int, default=10)
    args = ap.parse_args()

    phases = (
        [(int(c), int(s)) for c, s in (p.split(":") for p in args.ramp.split(","))]
        if args.ramp
        else [(args.concurrency, args.duration)]
    )
    max_conc = max(c for c, _ in phases)
    timeout = httpx.Timeout(connect=5, read=180, write=10, pool=10)
    limits = httpx.Limits(max_connections=max_conc + 2)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        model = args.model or (await client.get(f"{args.url}/v1/models")).json()["data"][0]["id"]
        print(f"loadgen -> {args.url} model={model} phases={phases} max_tokens={args.max_tokens}")
        stats = Stats()
        stop = asyncio.Event()
        gate = asyncio.Semaphore(0)
        workers = [
            asyncio.create_task(worker(stop, gate, client, args.url, model, args.max_tokens, stats))
            for _ in range(max_conc)
        ]
        current = 0
        t_start = time.monotonic()
        for conc, seconds in phases:
            # resize the semaphore: release to grow, acquire to shrink
            while current < conc:
                gate.release()
                current += 1
            while current > conc:
                await gate.acquire()
                current -= 1
            print(f"-- phase: concurrency={conc} for {seconds}s", flush=True)
            phase_end = time.monotonic() + seconds
            while time.monotonic() < phase_end:
                since = time.monotonic()
                await asyncio.sleep(min(args.report_every, max(0.1, phase_end - time.monotonic())))
                report(stats, since, f"t+{time.monotonic() - t_start:4.0f}s")
        stop.set()
        for _ in range(max_conc):
            gate.release()
        await asyncio.gather(*workers, return_exceptions=True)
        print("== summary")
        report(stats, 0, "all")
        outcomes: dict[str, int] = {}
        for s in stats.samples:
            outcomes[s.outcome] = outcomes.get(s.outcome, 0) + 1
        print(f"outcomes: {outcomes}")


if __name__ == "__main__":
    asyncio.run(main())
