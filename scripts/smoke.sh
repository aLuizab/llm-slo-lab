#!/usr/bin/env bash
# Streams a chat completion through the llm-gateway (NodePort 30080 mapped by kind).
# Usage: scripts/smoke.sh [prompt]      GATEWAY_URL overrides the base URL.
set -euo pipefail
cd "$(dirname "$0")/.."
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:30080}"
MODEL="${MODEL:-$(curl -fs "$GATEWAY_URL/v1/models" | jq -r '.data[0].id')}"
python3 scripts/stream-chat.py "$GATEWAY_URL" "$MODEL" "$@"
