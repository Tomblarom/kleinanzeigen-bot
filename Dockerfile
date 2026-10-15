# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
FROM python:3.13-slim-bookworm AS build
WORKDIR /opt/app
COPY pyproject.toml pdm.lock version.py README.md LICENSE.txt ./
COPY src ./src
COPY scripts/fix_nodriver.py ./scripts/fix_nodriver.py
# The build context deliberately excludes .git and private runtime data.
ENV GIT_COMMIT_HASH=container PDM_CHECK_UPDATE=false
RUN pip install --no-cache-dir pdm==2.26.1 \
    && pdm install --prod --no-editable --frozen-lockfile \
    && .venv/bin/python scripts/fix_nodriver.py

FROM python:3.13-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates util-linux nano \
       xvfb x11-utils x11vnc fluxbox novnc websockify \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/app/.venv /opt/app/.venv
ENV PATH="/opt/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY docker/portainer/bot.sh /usr/local/bin/bot
COPY docker/portainer/browser-login.sh /usr/local/bin/browser-login
COPY docker/portainer/scheduler.sh /opt/scheduler.sh
COPY docker/portainer/config.example.yaml /opt/config.example.yaml
RUN chmod +x /usr/local/bin/bot /usr/local/bin/browser-login /opt/scheduler.sh
WORKDIR /mnt/data
VOLUME /mnt/data
ENTRYPOINT ["/opt/scheduler.sh"]
