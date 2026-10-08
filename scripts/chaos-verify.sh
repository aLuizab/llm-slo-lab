#!/usr/bin/env bash
# Phase 7 acceptance: every toggle fires its demo alert, heal resolves it.
# Runs loadgen in the background, then for each toggle: break -> wait for the alert to fire
# -> heal -> wait for it to resolve. Writes docs/evidence/chaos.log. Takes ~30-45 minutes.
# Usage: scripts/chaos-verify.sh [toggle ...]   default: errors latency throughput quality
set -uo pipefail
cd "$(dirname "$0")/.."
LOG=docs/evidence/chaos.log; mkdir -p docs/evidence; : > "$LOG"
toggles=("${@:-errors latency throughput quality}")
[ $# -eq 0 ] && toggles=(errors latency throughput quality)
declare -A ALERT=([errors]=LLMAvailabilityBurnRatePageDemo [latency]=LLMResponsivenessBurnRatePageDemo
                  [throughput]=LLMThroughputBurnRatePageDemo [quality]=LLMQualityBurnRatePageDemo)
log() { echo "$(date +%H:%M:%S)  $*" | tee -a "$LOG"; }

kubectl -n observability port-forward svc/kube-prometheus-stack-prometheus 19090:9090 >/dev/null 2>&1 & pf=$!
(cd loadgen && uv run --frozen python loadgen.py --url http://127.0.0.1:30080 --concurrency 1 \
   --duration 3600 --report-every 60 > ../docs/evidence/chaos-loadgen.log 2>&1) & lg=$!
trap 'kill $pf $lg 2>/dev/null' EXIT
sleep 3

alert_state() { # alertname -> firing|pending|none
  curl -fs http://127.0.0.1:19090/api/v1/alerts | jq -r --arg a "$1" '[.data.alerts[] | select(.labels.alertname == $a) | .state] | if index("firing") then "firing" elif index("pending") then "pending" else "none" end'
}
wait_for() { # alertname, wanted(firing|none), max seconds -> prints elapsed or TIMEOUT
  local t0=$(date +%s) s
  while :; do
    s=$(alert_state "$1")
    if [ "$s" = "$2" ]; then echo $(( $(date +%s) - t0 )); return 0; fi
    if [ $(( $(date +%s) - t0 )) -ge "$3" ]; then echo "TIMEOUT(state=$s)"; return 1; fi
    sleep 15
  done
}

log "chaos verification start; demo rules; loadgen concurrency 1 in background"
log "baseline alerts: $(curl -fs http://127.0.0.1:19090/api/v1/alerts | jq -r '[.data.alerts[] | select(.labels.slo_mode=="demo") | .labels.alertname + "=" + .state] | join(" ") // "none"')"
for t in "${toggles[@]}"; do
  a=${ALERT[$t]}
  log "--- break-$t (expect $a)"
  scripts/chaos.sh "break-$t" 2>&1 | sed 's/^/    /' | tee -a "$LOG"
  r=$(wait_for "$a" firing 420); log "break-$t: $a firing after ${r}s"
  log "    all demo alerts now: $(curl -fs http://127.0.0.1:19090/api/v1/alerts | jq -r '[.data.alerts[] | select(.labels.slo_mode=="demo" and .state=="firing") | .labels.alertname] | join(" ") // "none"')"
  log "--- heal"
  scripts/chaos.sh heal 2>&1 | sed 's/^/    /' | tee -a "$LOG"
  r=$(wait_for "$a" none 600); log "heal: $a resolved after ${r}s"
  sleep 30
done
log "chaos verification done"
