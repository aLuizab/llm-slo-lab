#!/usr/bin/env bash
# Streams a chat completion straight from the KServe predictor and prints tokens as they
# arrive, plus TTFT and the final `usage` block if the backend sends one.
# Usage: scripts/smoke-model.sh [prompt]
set -euo pipefail
cd "$(dirname "$0")/.."
NAMESPACE="${NAMESPACE:-llm}"
SVC="${SVC:-qwen-predictor}"
MODEL="${MODEL:-qwen2.5-0.5b-instruct}"
PROMPT="${1:-Explain in two sentences what an SLO is.}"
PORT="${PORT:-18080}"

kubectl -n "$NAMESPACE" port-forward "svc/$SVC" "$PORT:80" >/dev/null 2>&1 &
pf=$!; trap 'kill $pf 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do curl -fs "http://127.0.0.1:$PORT/openai/v1/models" >/dev/null 2>&1 && break; sleep 0.5; done

echo "model list: $(curl -fs "http://127.0.0.1:$PORT/openai/v1/models" | jq -c '[.data[].id]')"
echo "prompt: $PROMPT"; echo "---"
body=$(jq -nc --arg m "$MODEL" --arg p "$PROMPT" \
  '{model:$m, messages:[{role:"user",content:$p}], max_tokens:80, temperature:0, stream:true, stream_options:{include_usage:true}}')
curl -sN "http://127.0.0.1:$PORT/openai/v1/chat/completions" -H 'content-type: application/json' -d "$body" \
  | python3 -u -c '
import json, sys, time
t0 = time.monotonic(); ttft = None; n = 0; usage = None; finish = None
for line in sys.stdin:
    line = line.strip()
    if not line.startswith("data:"): continue
    data = line[5:].strip()
    if data == "[DONE]": break
    chunk = json.loads(data)
    if chunk.get("usage"): usage = chunk["usage"]
    for ch in chunk.get("choices", []):
        delta = ch.get("delta", {}).get("content")
        if ch.get("finish_reason"): finish = ch["finish_reason"]
        if delta:
            if ttft is None: ttft = time.monotonic() - t0
            n += 1; sys.stdout.write(delta); sys.stdout.flush()
total = time.monotonic() - t0
tps = (n - 1) / (total - ttft) if ttft is not None and n > 1 and total > ttft else 0.0
print(f"\n---\nchunks={n} ttft={ttft:.2f}s total={total:.2f}s output_tok/s={tps:.1f} finish={finish}")
usage_txt = json.dumps(usage) if usage else "none (the gateway counts with the tokenizer, ADR-006)"
print("usage in stream:", usage_txt)
'
