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
docker exec "$container" bash -euc '
  exec 9>/mnt/data/.bot.lock
  flock --exclusive 9
  if bot --help >/dev/null; then
    echo "A bot command bypassed the session lock." >&2
    exit 1
  fi
'
if docker run --rm --network=none -e CHECK_INTERVAL_SECONDS=0 "$image"; then
  echo 'The scheduler accepted an invalid interval.' >&2
  exit 1
fi
docker run --rm --network=none --entrypoint bash "$image" -euc '
  mkdir -p /tmp/offline-bin
  cat > /tmp/offline-bin/bot <<"BOT"
#!/usr/bin/env bash
echo "$*" >> /mnt/data/commands
if [[ $1 == verify ]]; then exit "$BOT_VERIFY_EXIT"; fi
BOT
  chmod +x /tmp/offline-bin/bot
  export PATH="/tmp/offline-bin:$PATH"
  touch /mnt/data/automation.enabled
  export BOT_VERIFY_EXIT=1
  timeout 3 /opt/scheduler.sh || test "$?" = 124
  test "$(cat /mnt/data/commands)" = verify
  : > /mnt/data/commands
  export BOT_VERIFY_EXIT=0
  timeout 3 /opt/scheduler.sh || test "$?" = 124
  grep -qx "verify" /mnt/data/commands
  grep -qx "publish --ads=due" /mnt/data/commands
'
