#!/usr/bin/env bash
# Screenshot a Grafana dashboard (or one panel) with a headless browser.
# Usage: scripts/screenshot.sh OUT.png [panelId] [from] [to]
# Opens its own port-forward to Grafana on SHOT_PORT (default 33001; port 3000 can be blocked
# for Windows by Hyper-V's reserved port ranges), uses Edge/Chrome from Windows when running
# under WSL, else chromium/google-chrome on PATH.
set -uo pipefail
out="${1:?output png}"; panel="${2:-}"; from="${3:-now-30m}"; to="${4:-now}"
size="${SIZE:-1600,900}"
port="${SHOT_PORT:-33001}"
kubectl -n observability port-forward --address 0.0.0.0 svc/kube-prometheus-stack-grafana "$port:80" >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
for _ in $(seq 1 30); do curl -fs "http://localhost:$port/api/health" >/dev/null 2>&1 && break; sleep 1; done
browser=""
for b in "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe" \
         "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
         "$(command -v chromium 2>/dev/null)" "$(command -v google-chrome 2>/dev/null)"; do
  [ -n "$b" ] && [ -x "$b" ] && browser="$b" && break
done
[ -z "$browser" ] && { echo "no headless browser found" >&2; exit 1; }
mkdir -p "$(dirname "$out")"
host=localhost
case "$browser" in
  /mnt/c/*)
    # A Windows browser: its localhost is not WSL's. Reach the port-forward (bound to 0.0.0.0
    # by scripts/ui.sh) through the WSL IP, and hand Chrome a Windows path.
    # eth0 is the WSL NIC; `hostname -I` may list a Docker/kind bridge first.
    host=$(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
    [ -z "$host" ] && host=$(hostname -I | awk '{print $1}')
    target=$(wslpath -w "$(realpath -m "$out")") ;;
  *) target="$out" ;;
esac
# Absolute times and no auto-refresh: with a relative range the dashboard keeps re-querying
# every 15 s and the headless browser's virtual-time budget never settles.
abs() { # now | now-30m | now-2h | epoch ms
  local n
  case "$1" in
    now) echo $(( $(date +%s) * 1000 )) ;;
    now-*m) n=${1#now-}; n=${n%m}; echo $(( $(date +%s) * 1000 - n * 60 * 1000 )) ;;
    now-*h) n=${1#now-}; n=${n%h}; echo $(( $(date +%s) * 1000 - n * 3600 * 1000 )) ;;
    *) echo "$1" ;;
  esac
}
from=$(abs "$from"); to=$(abs "$to")
url="http://$host:$port/d/llm-slos/llm-slos?orgId=1&kiosk&refresh=&from=$from&to=$to"
[ -n "$panel" ] && url="http://$host:$port/d-solo/llm-slos/llm-slos?orgId=1&refresh=&panelId=$panel&from=$from&to=$to"
# A throwaway profile: without it a browser already running on the desktop takes over the
# request and the headless run never returns. For a Windows browser the profile must live on
# the Windows side: on a WSL path Chrome's lock files fail ("LockFileEx: incorrect function")
# and the run hangs. Crashpad is disabled for the same reason (and it is faster).
case "$browser" in
  /mnt/c/*)
    wintmp=$(wslpath -u "$(cmd.exe /c 'echo %TEMP%' 2>/dev/null | tr -d '\r')")
    profile="$wintmp/llm-slo-lab-shot-$$"; mkdir -p "$profile"
    profile_arg=$(wslpath -w "$profile")
    # Chrome also writes the PNG to the Windows side; it is copied into place afterwards.
    wintarget="$wintmp/llm-slo-lab-shot-$$.png"; target=$(wslpath -w "$wintarget") ;;
  *) profile=$(mktemp -d); profile_arg="$profile"; wintarget="" ;;
esac
# --virtual-time-budget lets Grafana's async queries and renders finish (about 60 s wall time
# for the full dashboard); a plain --timeout captures a blank page. Works only with the
# absolute range + refresh off above, otherwise the budget never settles.
timeout 150 "$browser" --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check \
  --disable-crashpad --disable-breakpad --user-data-dir="$profile_arg" \
  --virtual-time-budget="${SHOT_VTB_MS:-45000}" \
  --window-size="$size" --screenshot="$target" "$url" >/dev/null 2>&1
if [ -n "$wintarget" ]; then
  [ -s "$wintarget" ] && cp "$wintarget" "$out"; rm -f "$wintarget"
  (sleep 2; cmd.exe /c "rmdir /s /q $(wslpath -w "$profile")" >/dev/null 2>&1) &
else
  rm -rf "$profile"
fi
[ -s "$out" ] && echo "wrote $out ($(stat -c %s "$out") bytes) from $url" || { echo "screenshot failed" >&2; exit 1; }
