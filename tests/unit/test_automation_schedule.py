# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Offline daily scheduler coverage with a controlled clock and runner."""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

from kleinanzeigen_bot import automation_schedule as scheduling
from kleinanzeigen_bot.utils import misc


@pytest.fixture
def afternoon() -> datetime:
    return datetime(2026, 10, 15, 18, tzinfo = scheduling.BERLIN)


def save_due_plan(scheduler:scheduling.Scheduler, afternoon:datetime) -> None:
    start = afternoon.replace(hour = 15, minute = 0, second = 0).isoformat()
    scheduling.save_schedule(scheduler.state_path, scheduling.Schedule(afternoon.date().isoformat(), start, start))


def test_draw_and_restart(tmp_path:Path, afternoon:datetime) -> None:
    state = tmp_path / "state.json"
    plan = scheduling.load_schedule(state, afternoon.replace(hour = 17))
    chosen = datetime.fromisoformat(plan.scheduled_at)
    assert afternoon.replace(hour = 15, minute = 0, second = 0) <= chosen <= afternoon.replace(hour = 18, minute = 0, second = 0)
    assert scheduling.load_schedule(state, afternoon) == plan
    plan.status = "running"
    scheduling.save_schedule(state, plan)
    assert scheduling.load_schedule(state, afternoon).due(afternoon)


def test_next_day_draws_an_afternoon(tmp_path:Path, afternoon:datetime) -> None:
    state = tmp_path / "state.json"
    scheduling.save_schedule(state, scheduling.new_schedule(afternoon))
    morning = (afternoon + timedelta(days = 1)).replace(hour = 9)
    plan = scheduling.load_schedule(state, morning)
    assert plan.day == morning.date().isoformat()
    assert not plan.due(morning)


@pytest.mark.parametrize("success", [True, False])
def test_daily_completion_or_hourly_retry(tmp_path:Path, afternoon:datetime, success:bool) -> None:
    scheduler = scheduling.Scheduler(tmp_path)
    (tmp_path / "automation.enabled").touch()
    save_due_plan(scheduler, afternoon)
    with patch.object(scheduler, "run_batch", return_value = success) as run, patch.object(misc, "now", return_value = afternoon):
        plan = scheduler.tick(afternoon)
        assert plan.status == ("complete" if success else "retry")
        scheduler.tick(afternoon + timedelta(minutes = 59))
        assert run.call_count == 1
        scheduler.tick(afternoon + timedelta(hours = 1))
        assert run.call_count == (1 if success else 2)


def test_paused_and_before_start_do_not_run(tmp_path:Path, afternoon:datetime) -> None:
    scheduler = scheduling.Scheduler(tmp_path)
    save_due_plan(scheduler, afternoon)
    with patch.object(scheduler, "run_batch") as run:
        scheduler.tick(afternoon)
        (tmp_path / "automation.enabled").touch()
        scheduler.tick(afternoon.replace(hour = 14))
        run.assert_not_called()


def test_retry_does_not_carry_into_morning(tmp_path:Path, afternoon:datetime) -> None:
    scheduler = scheduling.Scheduler(tmp_path)
    (tmp_path / "automation.enabled").touch()
    save_due_plan(scheduler, afternoon)
    late = afternoon.replace(hour = 23, minute = 45)
    with patch.object(scheduler, "run_batch", return_value = False) as run, patch.object(misc, "now", return_value = late):
        scheduler.tick(late)
        next_day = late + timedelta(hours = 1)
        assert not scheduler.tick(next_day).due(next_day)
        assert run.call_count == 1


def test_corrupt_state_redraws(tmp_path:Path, afternoon:datetime) -> None:
    state = tmp_path / "state.json"
    state.write_text("broken", encoding = "utf-8")
    assert scheduling.load_schedule(state, afternoon.replace(hour = 17)).day == afternoon.date().isoformat()


def test_interrupted_shutdown_keeps_running_state(tmp_path:Path, afternoon:datetime) -> None:
    scheduler = scheduling.Scheduler(tmp_path)
    (tmp_path / "automation.enabled").touch()
    save_due_plan(scheduler, afternoon)

    def stopped_run() -> bool:
        scheduler.stopping.set()
        return False

    with patch.object(scheduler, "run_batch", side_effect = stopped_run):
        assert scheduler.tick(afternoon).status == "running"
    assert scheduling.load_schedule(scheduler.state_path, afternoon).due(afternoon)


def test_lock_failure_retries(tmp_path:Path, afternoon:datetime) -> None:
    scheduler = scheduling.Scheduler(tmp_path)
    (tmp_path / "automation.enabled").touch()
    save_due_plan(scheduler, afternoon)
    with patch.object(scheduler, "run_batch", side_effect = OSError("busy")), patch.object(misc, "now", return_value = afternoon):
        assert scheduler.tick(afternoon).status == "retry"


@pytest.mark.skipif(os.name != "posix", reason = "The container scheduler uses POSIX process groups")
def test_timeout_stops_descendants(tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
    command = tmp_path / "bot"
    command.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, time\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(tmp_path / 'child.pid')!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n",
        encoding = "utf-8",
    )
    command.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(scheduling, "RUN_TIMEOUT", 1)
    scheduler = scheduling.Scheduler(tmp_path)
    assert not scheduler.run_batch()
    assert scheduler.child is None
    child_pid = int((tmp_path / "child.pid").read_text())
    # A killed grandchild may briefly remain as a zombie awaiting its reaper.
    if psutil.pid_exists(child_pid):
        assert psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE


def test_late_first_activation_schedules_tomorrow(tmp_path:Path, afternoon:datetime) -> None:
    scheduler = scheduling.Scheduler(tmp_path)
    (tmp_path / "automation.enabled").touch()
    late = afternoon.replace(hour = 20)
    with patch.object(scheduler, "run_batch") as run:
        plan = scheduler.tick(late)
        assert plan.day == (late + timedelta(days = 1)).date().isoformat()
        assert scheduler.tick(late) == plan
        assert scheduler.tick(late + timedelta(hours = 10)) == plan
        run.assert_not_called()
