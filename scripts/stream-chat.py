#!/usr/bin/env python3
"""Stream one chat completion from an OpenAI-compatible endpoint and print tokens as they arrive.

Usage: stream-chat.py BASE_URL MODEL [PROMPT]   (BASE_URL is the prefix before /v1)
Only the standard library, so it runs with any python3.
"""

import json
import sys
import time
import urllib.request

base, model = sys.argv[1].rstrip("/"), sys.argv[2]
prompt = sys.argv[3] if len(sys.argv) > 3 else "Explain in two sentences what an SLO is."
body = {
    "model": model,
    "messages": [{"role": "user", "content": prompt}],
    "max_tokens": 80,
    "temperature": 0,
    "stream": True,
    "stream_options": {"include_usage": True},
}
req = urllib.request.Request(
    f"{base}/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={"content-type": "application/json"},
)
print(f"POST {base}/v1/chat/completions  model={model}\nprompt: {prompt}\n---")
t0 = time.monotonic()
ttft = None
n = 0
usage = None
finish = None
try:
    resp = urllib.request.urlopen(req, timeout=180)
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    sys.exit(1)
for raw in resp:
    line = raw.decode("utf-8", errors="replace").strip()
    if line.startswith("event: error"):
        print("\n[stream error event]")
    if not line.startswith("data:"):
        continue
    data = line[5:].strip()
    if data == "[DONE]":
        break
    chunk = json.loads(data)
    if chunk.get("usage"):
        usage = chunk["usage"]
    for ch in chunk.get("choices", []):
        if ch.get("finish_reason"):
            finish = ch["finish_reason"]
        delta = (ch.get("delta") or {}).get("content")
        if delta:
            if ttft is None:
                ttft = time.monotonic() - t0
            n += 1
            sys.stdout.write(delta)
            sys.stdout.flush()
total = time.monotonic() - t0
tps = (n - 1) / (total - ttft) if ttft is not None and n > 1 and total > ttft else 0.0
ttft_txt = f"{ttft:.2f}s" if ttft is not None else "n/a"
print(f"\n---\nchunks={n} ttft={ttft_txt} total={total:.2f}s output_tok/s={tps:.1f} finish={finish}")
print("usage in stream:", json.dumps(usage) if usage else "none (gateway counts with the tokenizer, ADR-006)")
