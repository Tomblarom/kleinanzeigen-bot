#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
cd /mnt/data
# Manual commands and scheduled runs must not share a browser session concurrently.
exec flock --nonblock /mnt/data/.bot.lock python -m kleinanzeigen_bot \
  --workspace-mode=portable --config=/mnt/data/config.yaml "$@"
