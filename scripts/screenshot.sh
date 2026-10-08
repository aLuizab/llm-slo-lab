#!/usr/bin/env bash
# Screenshot a Grafana dashboard (or one panel) with a headless browser.
# Usage: scripts/screenshot.sh OUT.png [panelId] [from] [to]
# Uses Edge/Chrome from Windows when running under WSL (localhost port-forwards are shared),
# else chromium/google-chrome on PATH. Needs `make ui` (or any port-forward to :3000) running.
set -uo pipefail
out="${1:?output png}"; panel="${2:-}"; from="${3:-now-30m}"; to="${4:-now}"
size="${SIZE:-1600,900}"
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
    host=$(hostname -I | awk '{print $1}')
    target=$(wslpath -w "$(realpath -m "$out")") ;;
  *) target="$out" ;;
esac
url="http://$host:3000/d/llm-slos/llm-slos?orgId=1&kiosk&from=$from&to=$to"
[ -n "$panel" ] && url="http://$host:3000/d-solo/llm-slos/llm-slos?orgId=1&panelId=$panel&from=$from&to=$to"
# A throwaway profile: without it a browser already running on the desktop takes over the
# request and the headless run never returns.
profile=$(mktemp -d)
case "$browser" in /mnt/c/*) profile_arg=$(wslpath -w "$profile") ;; *) profile_arg="$profile" ;; esac
timeout 90 "$browser" --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check \
  --user-data-dir="$profile_arg" --virtual-time-budget=15000 --window-size="$size" \
  --screenshot="$target" "$url" >/dev/null 2>&1
rm -rf "$profile"
[ -s "$out" ] && echo "wrote $out ($(stat -c %s "$out") bytes) from $url" || { echo "screenshot failed" >&2; exit 1; }
