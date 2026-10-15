#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
image=${1:?Pass the image to test}
# No test may contact kleinanzeigen.de.
docker run --rm --network=none "$image" --help >/dev/null
docker run --rm --network=none --entrypoint bash "$image" -euc '
  cp /opt/config.example.yaml /mnt/data/config.yaml
  export KLEINANZEIGEN_BOT_USERNAME=offline@example.invalid
  export KLEINANZEIGEN_BOT_PASSWORD=offline-test
  bot verify
  timeout 30 chromium --headless --no-sandbox --disable-dev-shm-usage --dump-dom about:blank 2>/dev/null | grep -q "<html>"
'
container=$(docker run -d --network=none "$image")
trap 'docker rm -fv "$container" >/dev/null' EXIT
for attempt in {1..30}; do
  if docker logs "$container" 2>&1 | grep -q 'Scheduler ready'; then
    break
  fi
  sleep 1
done
docker logs "$container" 2>&1 | grep -q 'Scheduler ready'
docker exec "$container" test -s /mnt/data/config.yaml
docker exec "$container" test ! -e /mnt/data/automation.enabled
test "$(docker inspect --format '{{.State.Running}}' "$container")" = true
# The visible browser starts on demand and never navigates to Kleinanzeigen in tests.
docker exec "$container" bash -euc '
  touch /mnt/data/automation.enabled
  if browser-login; then
    echo "Manual login started while automation was enabled." >&2
    exit 1
  fi
  rm /mnt/data/automation.enabled
  mkdir -p /mnt/data/.vnc
  x11vnc -storepasswd offline1 /mnt/data/.vnc/passwd >/dev/null
'
docker exec -d "$container" bash -c 'browser-login > /tmp/browser-login-check.log 2>&1'
desktop_ready=false
for attempt in {1..30}; do
  if docker exec "$container" python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:6080/vnc.html", timeout=1)' 2>/dev/null \
    && docker exec "$container" bash -c 'DISPLAY=:99 xwininfo -root -tree | grep -q Chromium'; then
    desktop_ready=true
    break
  fi
  sleep 1
done
if [[ $desktop_ready != true ]]; then
  docker exec "$container" cat /tmp/browser-login-check.log
  echo 'The visible browser did not become ready.' >&2
  exit 1
fi
docker exec "$container" python -c '
import socket
with socket.create_connection(("127.0.0.1", 5900), timeout=5) as connection:
    assert connection.recv(12).startswith(b"RFB ")
    connection.sendall(b"RFB 003.008\n")
    count = connection.recv(1)[0]
    methods = connection.recv(count)
    assert 2 in methods and 1 not in methods, methods  # VNC password required
'
if docker exec "$container" bot --help >/dev/null; then
  echo 'A bot command bypassed the manual browser lock.' >&2
  exit 1
fi
if docker exec "$container" browser-login; then
  echo 'A second manual browser bypassed the session lock.' >&2
  exit 1
fi
docker exec "$container" python -c '
import os
import signal
from pathlib import Path
for path in Path("/proc").glob("[0-9]*/cmdline"):
    try:
        arguments = path.read_bytes().split(b"\0")
        if arguments[0] == b"/usr/lib/chromium/chromium" and not any(a.startswith(b"--type=") for a in arguments):
            os.kill(int(path.parent.name), signal.SIGTERM)
    except (FileNotFoundError, ProcessLookupError):
        pass
'
desktop_stopped=false
for attempt in {1..30}; do
  if docker exec "$container" bot --help >/dev/null 2>&1; then
    desktop_stopped=true
    break
  fi
  sleep 1
done
test "$desktop_stopped" = true
docker exec "$container" python -c '
import socket
with socket.socket() as connection:
    connection.settimeout(2)
    assert connection.connect_ex(("127.0.0.1", 6080)) != 0, "Desktop port still open"
'
docker exec "$container" bash -euc '
  exec 9>/mnt/data/.bot.lock
  flock --exclusive 9
  if bot --help >/dev/null; then
    echo "A bot command bypassed the session lock." >&2
    exit 1
  fi
'
# Force a due persisted plan and run the real scheduler with an offline fake bot.
docker run --rm --network=none --entrypoint bash "$image" -euc '
  mkdir -p /tmp/offline-bin
  cat > /tmp/offline-bin/bot <<"BOT"
#!/usr/bin/env bash
echo "$*" >> /mnt/data/commands
exit 0
BOT
  chmod +x /tmp/offline-bin/bot
  export PATH="/tmp/offline-bin:$PATH"
  touch /mnt/data/automation.enabled
  python - <<"PYTHON"
from datetime import timedelta
from pathlib import Path
from kleinanzeigen_bot.automation_schedule import BERLIN, Scheduler, new_schedule, save_schedule
from kleinanzeigen_bot.utils.misc import now
root = Path("/mnt/data")
state = root / ".automation-schedule.json"
test_now = now().astimezone(BERLIN).replace(hour=18, minute=0, second=0, microsecond=0)
plan = new_schedule(test_now)
plan.next_attempt = (test_now - timedelta(seconds=1)).isoformat()
save_schedule(state, plan)
scheduler = Scheduler(root)
assert scheduler.tick(test_now).status == "complete"
assert scheduler.tick(test_now).status == "complete"
assert (root / "commands").read_text().splitlines() == ["extend --online --ads=all"]
PYTHON
'
