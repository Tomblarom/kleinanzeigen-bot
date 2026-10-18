#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
image=${1:?Pass the image to test}
# These checks cannot reach either marketplace.
docker run --rm --network=none "$image" --help >/dev/null
docker run --rm --network=none --entrypoint bash "$image" -euc '
  cp /opt/config.example.yaml /mnt/data/config.yaml
  export KLEINANZEIGEN_BOT_USERNAME=offline@example.invalid
  export KLEINANZEIGEN_BOT_PASSWORD=offline-test
  bot verify
  bot extend --help >/dev/null
  bot login --help >/dev/null
  timeout 30 chromium --headless --no-sandbox --disable-dev-shm-usage --dump-dom about:blank 2>/dev/null | grep -q "<html>"
'
container=$(docker run -d --network=none "$image")
trap 'docker rm -fv "$container" >/dev/null' EXIT
for attempt in {1..30}; do
  if docker logs "$container" 2>&1 | grep -q 'Scheduler ready'; then break; fi
  sleep 1
done
docker logs "$container" 2>&1 | grep -q 'Scheduler ready'
docker exec "$container" test -s /mnt/data/config.yaml
docker exec "$container" test ! -e /mnt/data/automation.enabled
docker exec "$container" python3 -c '''
import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, "/opt")
from automation_schedule import BERLIN, Scheduler, new_schedule, save_schedule
root = Path("/mnt/data")
now = datetime.now(BERLIN).replace(hour=18, minute=0, second=0, microsecond=0)
plan = new_schedule(now)
plan.next_attempt = (now-timedelta(seconds=1)).isoformat()
save_schedule(root / ".automation-schedule.json", plan)
# Replace only the external command with an offline runner.
s = Scheduler(root)
s.run_command = lambda command: command == ["account-extend"]
(root / "automation.enabled").touch()
assert s.tick(now).status == "complete"
assert s.tick(now).status == "complete"
'''
docker exec "$container" bash -euc '''
  exec 9>/mnt/data/.bot.lock
  flock --exclusive 9
  if bot --help >/dev/null; then echo "Browser lock bypassed" >&2; exit 1; fi
'''
