#!/usr/bin/env bash
# Port-forwards Grafana, Prometheus, Alertmanager and Jaeger and prints the URLs.
# Runs until Ctrl-C.
set -uo pipefail
NS=observability
pids=()
# bound to 0.0.0.0 so a Windows browser can reach them through the WSL IP (scripts/screenshot.sh)
fwd() { kubectl -n "$NS" port-forward --address 0.0.0.0 "svc/$1" "$2:$3" >/dev/null 2>&1 & pids+=($!); }
fwd kube-prometheus-stack-grafana 3000 80
fwd kube-prometheus-stack-prometheus 9090 9090
fwd kube-prometheus-stack-alertmanager 9093 9093
fwd jaeger 16686 16686
trap 'kill "${pids[@]}" 2>/dev/null' EXIT
sleep 1
cat <<EOF
  Grafana       http://localhost:3000   (admin / llm-slo-lab; anonymous viewer enabled)
  Prometheus    http://localhost:9090
  Alertmanager  http://localhost:9093
  Jaeger        http://localhost:16686
  Gateway       http://localhost:30080  (NodePort, no port-forward needed)
Press Ctrl-C to stop.
EOF
wait
