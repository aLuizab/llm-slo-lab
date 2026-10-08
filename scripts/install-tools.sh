#!/usr/bin/env bash
# Installs the lab toolchain into ~/.local/bin without sudo (Linux amd64).
# Versions come from versions.env. Re-run to upgrade.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source versions.env

BIN="$HOME/.local/bin"; mkdir -p "$BIN"; export PATH="$BIN:$PATH"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT; pushd "$TMP" >/dev/null

echo "== kind $KIND_VERSION"
curl -fsSLo "$BIN/kind" "https://kind.sigs.k8s.io/dl/$KIND_VERSION/kind-linux-amd64"; chmod +x "$BIN/kind"
echo "== kubectl $KUBECTL_VERSION"
curl -fsSLo "$BIN/kubectl" "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64/kubectl"; chmod +x "$BIN/kubectl"
echo "== helm $HELM_VERSION"
curl -fsSL "https://get.helm.sh/helm-$HELM_VERSION-linux-amd64.tar.gz" | tar xz; mv linux-amd64/helm "$BIN/helm"
echo "== promtool $PROMETHEUS_VERSION"
curl -fsSL "https://github.com/prometheus/prometheus/releases/download/v$PROMETHEUS_VERSION/prometheus-$PROMETHEUS_VERSION.linux-amd64.tar.gz" | tar xz
mv "prometheus-$PROMETHEUS_VERSION.linux-amd64/promtool" "$BIN/promtool"
echo "== kubeconform $KUBECONFORM_VERSION"
curl -fsSL "https://github.com/yannh/kubeconform/releases/download/$KUBECONFORM_VERSION/kubeconform-linux-amd64.tar.gz" | tar xz; mv kubeconform "$BIN/kubeconform"
echo "== jq"
curl -fsSLo "$BIN/jq" "https://github.com/jqlang/jq/releases/latest/download/jq-linux-amd64"; chmod +x "$BIN/jq"
if ! command -v make >/dev/null; then
  echo "== make (apt .deb extracted locally, no sudo)"
  apt-get download make >/dev/null; dpkg -x make_*.deb "$TMP/make"; cp "$TMP/make/usr/bin/make" "$BIN/make"
fi
echo "== uv $UV_VERSION"
curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_NO_MODIFY_PATH=1 sh >/dev/null
echo "== yamllint $YAMLLINT_VERSION"
uv tool install "yamllint==$YAMLLINT_VERSION" >/dev/null 2>&1
echo "== python $PYTHON_VERSION"
uv python install "$PYTHON_VERSION" >/dev/null 2>&1
popd >/dev/null

echo; scripts/tools-check.sh
echo; echo "Make sure ~/.local/bin is on your PATH (Ubuntu's ~/.profile adds it on next login)."
