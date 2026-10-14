#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
if [[ $# -gt 0 ]]; then
  exec bot "$@"
fi
umask 077
interval=${CHECK_INTERVAL_SECONDS:-86400}
if [[ ! $interval =~ ^[0-9]+$ ]] || (( interval < 60 )); then
  echo 'CHECK_INTERVAL_SECONDS must be an integer of at least 60.' >&2
  exit 1
fi
mkdir -p /mnt/data/downloaded-ads
if [[ ! -e /mnt/data/config.yaml ]]; then
  cp /opt/config.example.yaml /mnt/data/config.yaml
fi
echo 'Scheduler ready. Configure the account, download and verify ads, then create /mnt/data/automation.enabled.'
next_run=0
while true; do
  if [[ ! -f /mnt/data/automation.enabled ]]; then
    next_run=0
  elif (( SECONDS >= next_run )); then
    # Validation does not contact Kleinanzeigen. Publishing only starts after it passes.
    if bot verify; then
      if ! bot publish --ads=due; then
        echo 'Publishing failed; inspect the bot logs. Retrying at the next scheduled check.' >&2
      fi
    else
      echo 'Configuration validation failed; no ads were published.' >&2
    fi
    next_run=$((SECONDS + interval))
  fi
  sleep 60 &
  wait $!
done
