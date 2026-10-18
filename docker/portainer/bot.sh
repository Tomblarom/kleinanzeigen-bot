#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
cd /mnt/data
export VNC_DISABLE=1
if [[ ${1:-} == login ]]; then export VNC_DISABLE=0; fi
exec flock --nonblock /mnt/data/.bot.lock /bin/bash /opt/run.sh \
  --workspace-mode=portable --config=/mnt/data/config.yaml "$@"
