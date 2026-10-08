#!/usr/bin/env bash
# Installs one platform component with Helm. Usage: scripts/platform.sh <component>
# Components: cert-manager kserve kube-prometheus-stack otel-collector jaeger
# Idempotent: uses `helm upgrade --install`. Versions come from versions.env.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source versions.env

component="${1:?component}"

case "$component" in
  cert-manager)
    helm repo add jetstack https://charts.jetstack.io --force-update >/dev/null
    helm upgrade --install cert-manager jetstack/cert-manager \
      --namespace cert-manager --create-namespace --version "$CERT_MANAGER_VERSION" \
      -f platform/cert-manager/values.yaml --wait --timeout 5m
    ;;
  kserve)
    helm upgrade --install kserve-crd oci://ghcr.io/kserve/charts/kserve-crd \
      --namespace kserve --create-namespace --version "$KSERVE_VERSION" --wait
    helm upgrade --install kserve oci://ghcr.io/kserve/charts/kserve-resources \
      --namespace kserve --version "$KSERVE_VERSION" \
      -f platform/kserve/values-resources.yaml --wait --timeout 5m
    helm upgrade --install kserve-runtimes oci://ghcr.io/kserve/charts/kserve-runtime-configs \
      --namespace kserve --version "$KSERVE_VERSION" \
      -f platform/kserve/values-runtime-configs.yaml --wait
    kubectl -n kserve get deploy
    kubectl get clusterservingruntime kserve-huggingfaceserver
    ;;
  kube-prometheus-stack)
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update >/dev/null
    helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
      --namespace observability --create-namespace --version "$KUBE_PROMETHEUS_STACK_VERSION" \
      -f platform/kube-prometheus-stack/values.yaml --wait --timeout 10m
    kubectl -n observability get pods
    ;;
  otel-collector)
    helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts --force-update >/dev/null
    helm upgrade --install otel-collector open-telemetry/opentelemetry-collector \
      --namespace observability --create-namespace --version "$OTEL_COLLECTOR_CHART_VERSION" \
      -f platform/otel-collector/values.yaml --wait --timeout 5m
    kubectl -n observability rollout status deploy/otel-collector --timeout=120s
    ;;
  jaeger)
    kubectl create namespace observability --dry-run=client -o yaml | kubectl apply -f -
    kubectl apply -f platform/jaeger/jaeger.yaml
    kubectl -n observability rollout status deploy/jaeger --timeout=300s
    ;;
  *) echo "unknown component: $component" >&2; exit 2 ;;
esac
