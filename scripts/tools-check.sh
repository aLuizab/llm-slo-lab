#!/usr/bin/env bash
# Verifies the local toolchain. Exits non-zero if a tool is missing.
set -uo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source versions.env

fail=0
check() { # name, command, expected-substring
  local out
  if ! out=$($2 2>&1 | head -1); then out="(not found)"; fail=1; fi
  if [[ -n "${3:-}" && "$out" != *"$3"* ]]; then
    printf '  %-12s %s   (expected %s)\n' "$1" "$out" "$3"; fail=1
  else
    printf '  %-12s %s\n' "$1" "$out"
  fi
}
echo "toolchain:"
check docker  "docker version --format {{.Server.Version}}"
check kind    "kind version" "$KIND_VERSION"
check kubectl "kubectl version --client" "$KUBECTL_VERSION"
check helm    "helm version --short" "$HELM_VERSION"
check promtool "promtool --version" "$PROMETHEUS_VERSION"
check kubeconform "kubeconform -v" "$KUBECONFORM_VERSION"
check yamllint "yamllint --version" "$YAMLLINT_VERSION"
check uv      "uv --version" "$UV_VERSION"
check jq      "jq --version"
check make    "make --version"
check git     "git --version"
check gh      "gh --version"
check python  "uv python find $PYTHON_VERSION"
echo "gh auth: $(gh auth status 2>&1 | grep -m1 'Logged in' | sed 's/^ *//')"
echo "host: $(nproc) cpus, $(free -g | awk '/Mem:/ {print $2}') GB RAM"
exit $fail
