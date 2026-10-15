#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
if [[ $# -gt 0 ]]; then
  exec bot "$@"
fi
umask 077
mkdir -p /mnt/data/downloaded-ads
if [[ ! -e /mnt/data/config.yaml ]]; then
  cp /opt/config.example.yaml /mnt/data/config.yaml
fi
exec python -m kleinanzeigen_bot.automation_schedule
