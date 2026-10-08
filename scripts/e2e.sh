#!/usr/bin/env bash
# End-to-end check on a kind cluster with mock-llm instead of the real model (CI).
# Assumes: cluster up, kube-prometheus-stack + otel-collector + KEDA installed, images loaded.
# Steps: deploy mock-llm + gateway (pointed at mock), demo rules, ScaledObject; run load;
# assert the SLI metrics exist in Prometheus; break-errors; assert the demo alert fires; heal.
set -euo pipefail
cd "$(dirname "$0")/.."
NS=llm
say() { echo "== $(date +%H:%M:%S) $*"; }

say "deploy mock-llm, gateway (upstream = mock), demo rules, ScaledObject"
kubectl apply -f model/namespace.yaml -f mock-llm/k8s/ -f gateway/k8s/ -f slo/rules-demo.yaml -f autoscaling/scaledobject-mock-llm.yaml
kubectl -n $NS patch cm llm-gateway-env --type merge \
  -p '{"data":{"UPSTREAM_URL":"http://mock-llm.llm.svc","MODEL_NAME":"mock-llm","PROVIDER_NAME":"mock"}}'
kubectl -n $NS rollout restart deploy/llm-gateway
kubectl -n $NS rollout status deploy/mock-llm --timeout=180s
kubectl -n $NS rollout status deploy/llm-gateway --timeout=180s
for _ in $(seq 1 30); do curl -fs http://127.0.0.1:30080/v1/models >/dev/null && break; sleep 2; done
curl -fs http://127.0.0.1:30080/v1/models | jq -c .

say "load: 60 s at concurrency 2"
(cd loadgen && uv run --frozen python loadgen.py --url http://127.0.0.1:30080 --concurrency 2 --duration 60 --max-tokens 32 --report-every 30)

kubectl -n observability port-forward svc/kube-prometheus-stack-prometheus 19090:9090 >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
sleep 3
q() { curl -fs http://127.0.0.1:19090/api/v1/query --data-urlencode "query=$1" | jq -r '.data.result[0].value[1] // "none"'; }

say "assert: SLI metrics present in Prometheus"
fail=0
for m in 'sum(llm_slo_requests_total)' 'sum(gen_ai_server_time_to_first_token_seconds_count)' \
         'sum(gen_ai_client_token_usage_count)' 'sum(llm_slo_request_cost_count)' 'sum(llm_slo_output_tokens_per_second_count)' \
         'llm_slo_requests_in_flight'; do
  v=$(q "$m"); printf '  %-55s %s\n' "$m" "$v"
  [ "$v" = "none" ] && fail=1
done
[ $fail -eq 1 ] && { echo "missing metrics" >&2; exit 1; }
say "assert: recording rules evaluate"
v=$(q 'slodemo:sli_error:ratio_rate1m{sli="availability"}'); echo "  availability error ratio (1m): $v"; [ "$v" != "none" ]

say "break-errors, keep load flowing, wait for LLMAvailabilityBurnRatePageDemo"
scripts/chaos.sh break-errors
(cd loadgen && uv run --frozen python loadgen.py --url http://127.0.0.1:30080 --concurrency 2 --duration 300 --max-tokens 32 --report-every 60 > /tmp/e2e-loadgen.log 2>&1) & lg=$!
fired=""
for i in $(seq 1 24); do
  state=$(curl -fs http://127.0.0.1:19090/api/v1/alerts | jq -r '[.data.alerts[] | select(.labels.alertname=="LLMAvailabilityBurnRatePageDemo") | .state] | index("firing") // "no"')
  echo "  t+$((i*15))s alert firing: $state  burn3m=$(q 'slodemo:burn_rate:rate3m{sli="availability"}')"
  [ "$state" != "no" ] && { fired=yes; break; }
  sleep 15
done
kill $lg 2>/dev/null || true
[ -n "$fired" ] || { echo "demo alert did not fire within 6 minutes" >&2; exit 1; }

say "heal"
scripts/chaos.sh heal 2>&1 | grep -v evaluator || true
say "e2e OK"
