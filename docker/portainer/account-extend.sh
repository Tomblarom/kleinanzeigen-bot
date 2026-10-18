#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
cd /mnt/data
exec 9>/mnt/data/.bot.lock
flock --nonblock 9
export VNC_DISABLE=1
# All source files and current account states come from the official download CLI.
/opt/kleinanzeigen-bot download --ads=all --lang=en --workspace-mode=portable --config=/mnt/data/config.yaml
/opt/kleinanzeigen-bot extend --ads=all --lang=en --workspace-mode=portable --config=/mnt/data/config.yaml
