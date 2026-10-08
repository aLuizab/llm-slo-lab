#!/usr/bin/env bash
# Shows the lab's rule groups (health, rule counts) and firing/pending alerts from Prometheus.
set -uo pipefail
kubectl -n observability port-forward svc/kube-prometheus-stack-prometheus 19090:9090 >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
sleep 2
echo "== rule groups"
curl -fs http://127.0.0.1:19090/api/v1/rules | jq -r '
  .data.groups[] | select(.name | startswith("llm-slo"))
  | "  " + .name + "  rules=" + (.rules | length | tostring)
    + "  unhealthy=" + ([.rules[] | select(.health != "ok")] | length | tostring)
    + (([.rules[] | select(.lastError != "" and .lastError != null) | .name + ": " + .lastError] | join("; ")) as $e
       | if $e == "" then "" else "  errors=" + $e end)'
echo "== burn rates / budget (prod)"
curl -fs 'http://127.0.0.1:19090/api/v1/query' --data-urlencode 'query=slo:error_budget_remaining:ratio' \
  | jq -r '.data.result[] | "  budget remaining " + .metric.sli + " = " + (.value[1] | tonumber * 100 | round / 100 | tostring)'
curl -fs 'http://127.0.0.1:19090/api/v1/query' --data-urlencode 'query=slodemo:burn_rate:rate3m' \
  | jq -r '.data.result[] | "  demo burn 3m  " + .metric.sli + " = " + (.value[1] | tonumber * 100 | round / 100 | tostring)'
echo "== alerts (pending/firing)"
curl -fs http://127.0.0.1:19090/api/v1/alerts | jq -r '
  if (.data.alerts | length) == 0 then "  none" else
  .data.alerts[] | "  " + .state + "  " + .labels.alertname + "  value=" + (.value | tonumber * 10 | round / 10 | tostring) end'
