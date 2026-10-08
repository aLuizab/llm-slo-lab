#!/usr/bin/env bash
# Phase 3 acceptance: PromQL results for every SLI signal plus one trace from Jaeger.
# Assumes some load went through the gateway recently (make loadgen).
set -uo pipefail
NS=observability
kubectl -n $NS port-forward svc/kube-prometheus-stack-prometheus 19090:9090 >/dev/null 2>&1 & p1=$!
kubectl -n $NS port-forward svc/jaeger 16686:16686 >/dev/null 2>&1 & p2=$!
trap 'kill $p1 $p2 2>/dev/null' EXIT
sleep 2

q() { # label, promql
  local res
  res=$(curl -fs "http://127.0.0.1:19090/api/v1/query" --data-urlencode "query=$2" \
    | jq -r '.data.result | if length == 0 then "  (no data)" else .[] | "  " + (.metric | del(.__name__, .job, .instance, .service_name, .service_version, .k8s_pod_name, .k8s_namespace_name, .k8s_deployment_name, .k8s_container_name, .service_instance_id, .server_address, .server_port, .gen_ai_operation_name) | tostring) + " => " + (.value[1] | tonumber | . * 1000000 | round / 1000000 | tostring) end')
  printf '%s\n%s\n' "$1" "$res"
}
echo "== Prometheus (via native OTLP receiver)"
q "TTFT p95 (5m)"      'histogram_quantile(0.95, sum by (le) (rate(gen_ai_server_time_to_first_token_seconds_bucket[5m])))'
q "TTFT p50 (5m)"      'histogram_quantile(0.50, sum by (le) (rate(gen_ai_server_time_to_first_token_seconds_bucket[5m])))'
q "time/output token p50" 'histogram_quantile(0.50, sum by (le) (rate(gen_ai_server_time_per_output_token_seconds_bucket[5m])))'
q "output tokens/s p50 (per request)" 'histogram_quantile(0.50, sum by (le) (rate(llm_slo_output_tokens_per_second_bucket[5m])))'
q "token usage (5m, by type)" 'sum by (gen_ai_token_type, llm_slo_token_source) (increase(gen_ai_client_token_usage_sum[5m]))'
q "requests (5m, by outcome)" 'sum by (llm_slo_outcome) (increase(llm_slo_requests_total[5m]))'
q "cost per request, mean (5m, by model)" 'sum by (llm_slo_cost_model) (rate(llm_slo_request_cost_sum[5m])) / sum by (llm_slo_cost_model) (rate(llm_slo_request_cost_count[5m]))'
q "in-flight requests" 'sum(llm_slo_requests_in_flight)'
q "operation duration p95" 'histogram_quantile(0.95, sum by (le) (rate(gen_ai_client_operation_duration_seconds_bucket[5m])))'
echo
echo "== metric names present"
curl -fs 'http://127.0.0.1:19090/api/v1/label/__name__/values' | jq -r '.data[] | select(startswith("gen_ai_") or startswith("llm_slo_"))' | sed 's/^/  /'
echo
echo "== Jaeger (v2 exposes the v3 query API; /api/services from v1 is gone)"
echo "  services: $(curl -fs http://127.0.0.1:16686/api/v3/services | jq -c '.services')"
start=$(date -u -d '-1 hour' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-1H +%Y-%m-%dT%H:%M:%SZ)
end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
curl -fs "http://127.0.0.1:16686/api/v3/traces?query.service_name=llm-gateway&query.num_traces=1&query.start_time_min=$start&query.start_time_max=$end" \
  | jq -r '
      [.result.resourceSpans[]?.scopeSpans[]?.spans[]?] as $spans
      | if ($spans | length) == 0 then "  (no trace found)" else
        "  trace " + $spans[0].traceId + ": " + ([$spans[] | .name] | join(" | "))
        + "\n  chat span attrs: "
        + ([$spans[] | select(.name | startswith("chat")) | .attributes[]
            | select(.key | startswith("gen_ai") or startswith("llm_slo"))
            | .key + "=" + ((.value.stringValue // .value.intValue // .value.doubleValue // .value.boolValue // (.value.arrayValue.values | map(.stringValue) | join(","))) | tostring)]
           | join(", "))
        end'
