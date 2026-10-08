#!/usr/bin/env bash
# Records the on-stage fallback casts with asciinema into talk/recordings/.
#   smoke.cast          a streamed completion through the gateway
#   break-errors.cast   break-errors -> alert firing -> heal -> resolved (about 6 minutes)
# Needs: cluster up, demo rules, evaluator and loadgen running (scripts/chaos-verify.sh style).
# Usage: scripts/record-demo.sh [smoke|break-errors|all]
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p talk/recordings
what="${1:-all}"
command -v asciinema >/dev/null || { echo "asciinema not found: uv tool install asciinema" >&2; exit 1; }

export REPO="$PWD"
rec() { # name, command
  echo "== recording talk/recordings/$1.cast"
  asciinema rec --overwrite --idle-time-limit 3 --title "llm-slo-lab: $1" \
    --command "bash -c 'cd \"$REPO\" && $2'" "talk/recordings/$1.cast"
}

# Do not record a broken lab: wait until the model answers through the gateway.
echo "== waiting for a healthy gateway + model"
for _ in $(seq 1 60); do
  if curl -fs -m 30 http://127.0.0.1:30080/v1/chat/completions -H 'content-type: application/json' \
       -d '{"messages":[{"role":"user","content":"Say OK."}],"max_tokens":4}' >/dev/null 2>&1; then break; fi
  sleep 10
done

if [ "$what" = smoke ] || [ "$what" = all ]; then
  # The model serves one request at a time: pause the evaluator so the smoke run shows the
  # uncontended TTFT, then resume it.
  kubectl -n llm patch cronjob evaluator -p '{"spec":{"suspend":true}}' >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do
    [ "$(kubectl -n llm get jobs -o json 2>/dev/null | jq '[.items[] | select(.status.active)] | length')" = "0" ] && break
    sleep 5
  done
  rec smoke 'echo "\$ make smoke"; make smoke'
  kubectl -n llm patch cronjob evaluator -p '{"spec":{"suspend":false}}' >/dev/null 2>&1 || true
fi

if [ "$what" = break-errors ] || [ "$what" = all ]; then
  cat > /tmp/break-errors-demo.sh <<'EOF'
set -u
cd "$REPO"
say() { printf '\n\033[1;36m$ %s\033[0m\n' "$*"; }
alerts() { scripts/slo-status.sh 2>/dev/null | sed -n '/== alerts/,$p'; }
say "make chaos-status   # healthy baseline"; make chaos-status 2>/dev/null | tail -8
say "make break-errors   # half of the requests now get a 503"; make break-errors
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  sleep 15
  say "make slo-status     # t+$((i*15))s"; alerts
  if scripts/slo-status.sh 2>/dev/null | grep -q 'firing  LLMAvailabilityBurnRatePageDemo'; then break; fi
done
say "make heal"; make heal
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do
  sleep 15
  say "make slo-status     # t+$((i*15))s after heal"; alerts
  if ! scripts/slo-status.sh 2>/dev/null | grep -q 'LLMAvailabilityBurnRatePageDemo'; then echo "resolved."; break; fi
done
EOF
  rec break-errors "bash /tmp/break-errors-demo.sh"
fi
ls -la talk/recordings/
