#!/usr/bin/env bash
# Streams a chat completion straight from the KServe predictor (bypassing the gateway).
# Usage: scripts/smoke-model.sh [prompt]
set -euo pipefail
cd "$(dirname "$0")/.."
NAMESPACE="${NAMESPACE:-llm}"
SVC="${SVC:-qwen-predictor}"
MODEL="${MODEL:-qwen2.5-0.5b-instruct}"
PORT="${PORT:-18080}"

kubectl -n "$NAMESPACE" port-forward "svc/$SVC" "$PORT:80" >/dev/null 2>&1 &
pf=$!; trap 'kill $pf 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do curl -fs "http://127.0.0.1:$PORT/openai/v1/models" >/dev/null 2>&1 && break; sleep 0.5; done
echo "models: $(curl -fs "http://127.0.0.1:$PORT/openai/v1/models" | jq -c '[.data[].id]')"
python3 scripts/stream-chat.py "http://127.0.0.1:$PORT/openai" "$MODEL" "$@"
