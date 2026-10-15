# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Persistent daily account-extension scheduler for the Portainer image."""

from __future__ import annotations

import json
import os
import random
import signal
import subprocess  # noqa: S404 — trusted local bot command, never a shell
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .utils import loggers, misc

if TYPE_CHECKING:
    from types import FrameType

LOG = loggers.get_logger(__name__)
BERLIN = ZoneInfo("Europe/Berlin")
RUN_TIMEOUT = 30 * 60
STOP_TIMEOUT = 20
RETRY_DELAY = timedelta(hours = 1)


@dataclass
class Schedule:
    """One persisted Berlin calendar day's plan."""

    day:str
    scheduled_at:str
    next_attempt:str
    status:str = "pending"

    def due(self, now:datetime) -> bool:
        return (self.day == now.astimezone(BERLIN).date().isoformat()
                and self.status != "complete" and datetime.fromisoformat(self.next_attempt) <= now)

    def finish(self, now:datetime, *, success:bool) -> None:
        self.status = "complete" if success else "retry"
        self.next_attempt = (now + RETRY_DELAY).isoformat()


def new_schedule(now:datetime) -> Schedule:
    """Pick a fresh daily start between 15:00 and 18:00, inclusive."""
    now = now.astimezone(BERLIN)
    start = now.replace(hour = 15, minute = 0, second = 0, microsecond = 0)
    chosen = start + timedelta(seconds = random.SystemRandom().randint(0, 3 * 60 * 60))
    return Schedule(now.date().isoformat(), chosen.isoformat(), chosen.isoformat())


def save_schedule(path:Path, schedule:Schedule) -> None:
    """Persist before executing so a restart can recover an interrupted run."""
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding = "utf-8") as stream:
        json.dump(asdict(schedule), stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_schedule(path:Path, now:datetime) -> Schedule:
    """Keep today's draw and retry time across restarts; discard older plans."""
    now = now.astimezone(BERLIN)
    try:
        schedule = Schedule(**json.loads(path.read_text(encoding = "utf-8")))
        scheduled = datetime.fromisoformat(schedule.scheduled_at)
        next_attempt = datetime.fromisoformat(schedule.next_attempt)
        if scheduled.tzinfo is not None and next_attempt.tzinfo is not None:
            local_start = scheduled.astimezone(BERLIN)
            start = local_start.replace(hour = 15, minute = 0, second = 0, microsecond = 0)
            valid_day = now.date() <= local_start.date() <= (now + timedelta(days = 1)).date()
            if (valid_day and schedule.day == local_start.date().isoformat() and start <= scheduled <= start.replace(hour = 18)
                    and schedule.status in {"pending", "running", "retry", "complete"}):
                return schedule
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        LOG.warning("Invalid scheduler state; drawing a new afternoon start.")
    # A first activation after the afternoon window starts on the following day.
    draw_day = now + timedelta(days = 1) if now.hour >= 18 else now  # noqa: PLR2004
    schedule = new_schedule(draw_day)
    save_schedule(path, schedule)
    LOG.info("Next account-extension run: %s", schedule.scheduled_at)
    return schedule


class Scheduler:
    """Run one sequential batch, with bounded execution and durable retries."""

    def __init__(self, root:Path) -> None:
        self.root = root
        self.state_path = root / ".automation-schedule.json"
        self.stopping = threading.Event()
        self.child:subprocess.Popen[bytes] | None = None

    def stop(self, signum:int, frame:FrameType | None) -> None:  # noqa: ARG002
        """Wake the idle loop or terminate the active process group."""
        self.stopping.set()
        self._signal_child(signal.SIGTERM)

    def _signal_child(self, signum:int) -> None:
        if self.child is not None:
            try:
                os.killpg(self.child.pid, signum)
            except ProcessLookupError:
                pass

    def run_batch(self) -> bool:
        """Inherit container logs; terminate descendants on timeout or shutdown."""
        self.child = subprocess.Popen(["bot", "extend", "--online", "--ads=all"], start_new_session = True)  # noqa: S603,S607
        deadline = time.monotonic() + RUN_TIMEOUT
        try:
            while not self.stopping.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    LOG.error("Account-extension run exceeded 30 minutes; stopping its process group.")
                    return False
                try:
                    self.child.wait(timeout = min(1, remaining))
                    return self.child.returncode == 0 and not self.stopping.is_set()
                except subprocess.TimeoutExpired:
                    continue
            return False
        finally:
            # Always clean up descendants, even when the parent exited first.
            self._signal_child(signal.SIGTERM)
            try:
                self.child.wait(timeout = STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass
            self._signal_child(signal.SIGKILL)
            self.child.wait()
            self.child = None

    def tick(self, now:datetime) -> Schedule:
        """Advance a single tick; time and execution are replaceable in offline tests."""
        now = now.astimezone(BERLIN)
        schedule = load_schedule(self.state_path, now)
        if self.stopping.is_set() or not (self.root / "automation.enabled").is_file() or not schedule.due(now):
            return schedule
        schedule.status = "running"
        save_schedule(self.state_path, schedule)
        LOG.info("Starting scheduled account extensions.")
        try:
            success = self.run_batch()
        except OSError as ex:
            LOG.error("Could not start account extensions: %s", ex)
            success = False
        if self.stopping.is_set():
            return schedule
        schedule.finish(misc.now().astimezone(BERLIN), success = success)
        save_schedule(self.state_path, schedule)
        if success:
            LOG.info("Daily account-extension run completed.")
        else:
            LOG.warning("Account extensions incomplete; next retry: %s", schedule.next_attempt)
        return schedule

    def run(self) -> None:
        LOG.info("Scheduler ready. Enable account extensions with /mnt/data/automation.enabled.")
        while not self.stopping.is_set():
            self.tick(misc.now())
            self.stopping.wait(60)


def main() -> None:
    """Start the container's supervised scheduler."""
    os.umask(0o077)
    loggers.configure_console_logging()
    LOG.setLevel(loggers.INFO)
    scheduler = Scheduler(Path("/mnt/data"))
    signal.signal(signal.SIGTERM, scheduler.stop)
    signal.signal(signal.SIGINT, scheduler.stop)
    scheduler.run()


if __name__ == "__main__":
    main()
