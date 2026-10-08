#!/usr/bin/env bash
# Phase 6 acceptance: ramp load, watch replicas follow in-flight requests, then scale back.
# Writes docs/evidence/keda-ramp.log with one line every 15 s: time, in-flight, replicas.
# Usage: scripts/keda-ramp.sh [RAMP]   default 1:60,4:180,1:240
set -uo pipefail
cd "$(dirname "$0")/.."
RAMP="${1:-1:60,4:180,1:240}"
DEPLOY="${DEPLOY:-qwen-predictor}"
LOG=docs/evidence/keda-ramp.log; mkdir -p docs/evidence
kubectl -n observability port-forward svc/kube-prometheus-stack-prometheus 19090:9090 >/dev/null 2>&1 & pf=$!
trap 'kill $pf $lg 2>/dev/null' EXIT
sleep 2
(cd loadgen && uv run --frozen python loadgen.py --url http://127.0.0.1:30080 --ramp "$RAMP" --report-every 30 > ../docs/evidence/keda-loadgen.log 2>&1) & lg=$!
total=0; for p in ${RAMP//,/ }; do total=$((total + ${p#*:})); done
echo "ramp=$RAMP total=${total}s deploy=$DEPLOY" | tee "$LOG"
printf '%-9s %-10s %-9s %-9s %-9s %s\n' time inflight ready desired mem_mb phase | tee -a "$LOG"
start=$(date +%s)
while :; do
  now=$(( $(date +%s) - start ))
  inflight=$(curl -fs http://127.0.0.1:19090/api/v1/query --data-urlencode 'query=sum(llm_slo_requests_in_flight)' | jq -r '.data.result[0].value[1] // "0"')
  ready=$(kubectl -n llm get deploy "$DEPLOY" -o jsonpath='{.status.readyReplicas}' 2>/dev/null); ready=${ready:-0}
  desired=$(kubectl -n llm get deploy "$DEPLOY" -o jsonpath='{.spec.replicas}' 2>/dev/null)
  phase=$(grep -oE 'concurrency=[0-9]+' docs/evidence/keda-loadgen.log 2>/dev/null | tail -1)
  mem=$(free -m | awk '/Mem:/ {print $3}')
  printf '%-9s %-10s %-9s %-9s %-9s %s\n' "t+${now}s" "$inflight" "$ready" "$desired" "$mem" "$phase" | tee -a "$LOG"
  if ! kill -0 $lg 2>/dev/null && [ "$now" -gt "$total" ]; then break; fi
  [ "$now" -gt $((total + 240)) ] && break
  sleep 15
done
echo "--- loadgen summary" | tee -a "$LOG"; tail -3 docs/evidence/keda-loadgen.log | tee -a "$LOG"
