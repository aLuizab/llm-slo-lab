#!/usr/bin/env bash
# Installs one platform component with Helm. Usage: scripts/platform.sh <component>
# Components: cert-manager kserve
# Idempotent: uses `helm upgrade --install`. Versions come from versions.env.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source versions.env

component="${1:?component}"
wait_rollout() { kubectl -n "$1" rollout status deploy -l "$2" --timeout=300s; }

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
  *) echo "unknown component: $component" >&2; exit 2 ;;
esac
