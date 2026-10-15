#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
umask 077
cd /mnt/data
if [[ -f automation.enabled ]]; then
  echo 'Pause automation first: rm -f /mnt/data/automation.enabled' >&2
  exit 1
fi
# Keep the browser profile exclusive until Chromium and the desktop have stopped.
exec 9>/mnt/data/.bot.lock
if ! flock --nonblock 9; then
  echo 'Another bot or browser-login session is running.' >&2
  exit 1
fi
mkdir -p /mnt/data/.vnc
password_file=/mnt/data/.vnc/passwd
if [[ ! -s $password_file ]]; then
  if [[ ! -t 0 ]]; then
    echo 'Run browser-login in an interactive container console to set the VNC password.' >&2
    exit 1
  fi
  echo 'Choose a separate VNC password (exactly eight characters; do not use your account password).'
  x11vnc -storepasswd "$password_file"
fi
chmod 600 "$password_file"
profile=$(python - <<'PY'
import os
from pathlib import Path
from ruamel.yaml import YAML

config = YAML(typ="safe").load(Path("/mnt/data/config.yaml")) or {}
browser = config.get("browser", {})
profile = browser.get("user_data_dir") or "/mnt/data/.temp/browser-profile"
for argument in browser.get("arguments", []) or []:
    if argument.startswith("--user-data-dir="):
        profile = argument.split("=", 1)[1]
print(Path(os.path.expandvars(profile)).expanduser().resolve())
PY
)
profile_name=$(python - <<'PY'
from pathlib import Path
from ruamel.yaml import YAML

config = YAML(typ="safe").load(Path("/mnt/data/config.yaml")) or {}
print(config.get("browser", {}).get("profile_name") or "Default")
PY
)
session_dir=$(mktemp -d /tmp/browser-login.XXXXXX)
children=()
cleanup() {
  trap - EXIT INT TERM
  if (( ${#children[@]} )); then
    kill "${children[@]}" 2>/dev/null || true
    # Some window managers keep waiting after the X server disappears.
    for attempt in {1..50}; do
      mapfile -t remaining < <(jobs -pr)
      (( ${#remaining[@]} )) || break
      sleep 0.1
    done
    if (( ${#remaining[@]} )); then
      kill -KILL "${remaining[@]}" 2>/dev/null || true
    fi
    wait "${children[@]}" 2>/dev/null || true
  fi
  rm -rf "$session_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export DISPLAY=:99
# Only this supervisor owns the lock; detached browser helpers must not retain it.
Xvfb "$DISPLAY" -screen 0 1440x1080x24 -nolisten tcp >"$session_dir/xvfb.log" 2>&1 9>&- &
children+=("$!")
ready=false
for attempt in {1..50}; do
  if xdpyinfo >/dev/null 2>&1; then
    ready=true
    break
  fi
  kill -0 "${children[0]}" 2>/dev/null || break
  sleep 0.1
done
if [[ $ready != true ]]; then
  echo 'The virtual display could not start.' >&2
  exit 1
fi
echo 'session.screen0.rootCommand: /bin/true' >"$session_dir/fluxbox.init"
fluxbox -rc "$session_dir/fluxbox.init" >"$session_dir/fluxbox.log" 2>&1 9>&- &
children+=("$!")
# Handle VNC connections independently of X11 event polling.
x11vnc -display "$DISPLAY" -localhost -nolookup -threads -rfbport 5900 -rfbauth "$password_file" \
  -forever -shared >"$session_dir/vnc.log" 2>&1 9>&- &
children+=("$!")
/usr/bin/websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900 \
  >"$session_dir/websockify.log" 2>&1 9>&- &
children+=("$!")
chromium --no-sandbox --disable-dev-shm-usage --no-first-run --no-default-browser-check \
  --window-size=1440,1080 --user-data-dir="$profile" --profile-directory="$profile_name" \
  about:blank >"$session_dir/chromium.log" 2>&1 9>&- &
children+=("$!")
echo 'Open http://<docker-host>:6080/vnc.html and connect using your VNC password.'
echo 'Log in manually in Chromium. Close Chromium when finished; keep this console open until then.'
# A failure in any desktop component also ends the session and releases the lock.
wait -n "${children[@]}"
