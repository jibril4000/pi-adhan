"""Tests for scheduler module."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.config import (
    AppConfig,
    AudioConfig,
    BackgroundConfig,
    CalculationConfig,
    LocationConfig,
    LoggingConfig,
    PrayersConfig,
    SchedulerConfig,
    PRAYER_NAMES,
)
from src.player import AdhanPlayer
from src.scheduler import AdhanScheduler


@pytest.fixture
def config(tmp_path):
    return AppConfig(
        location=LocationConfig(34.3078, -118.4467, "America/Los_Angeles"),
        calculation=CalculationConfig(method="NORTH_AMERICA"),
        audio=AudioConfig(default_file="audio/adhan_default.mp3"),
        prayers=PrayersConfig(),
        background=BackgroundConfig(),
        scheduler=SchedulerConfig(daily_recalc_time="00:05", misfire_grace_seconds=300),
        logging=LoggingConfig(),
        base_dir=str(tmp_path),
    )


@pytest.fixture
def player(config):
    return MagicMock(spec=AdhanPlayer)


@pytest.fixture
def scheduler(config, player):
    return AdhanScheduler(config, player)


class TestAdhanScheduler:
    @patch("src.scheduler.get_prayer_times")
    def test_schedules_future_prayers(self, mock_get_times, scheduler):
        tz = ZoneInfo("America/Los_Angeles")
        now = datetime.now(tz)

        # Create times: all in the future
        future_times = {}
        for i, prayer in enumerate(PRAYER_NAMES):
            future_times[prayer] = now + timedelta(hours=i + 1)

        mock_get_times.return_value = future_times

        scheduler._schedule_prayers_for_today()

        # All 5 should be scheduled
        jobs = [j for j in scheduler.scheduler.get_jobs() if j.id.startswith("prayer_")]
        assert len(jobs) == 5

    @patch("src.scheduler.get_prayer_times")
    def test_skips_past_prayers(self, mock_get_times, scheduler):
        tz = ZoneInfo("America/Los_Angeles")
        now = datetime.now(tz)

        times = {}
        # First 3 in the past, last 2 in the future
        for i, prayer in enumerate(PRAYER_NAMES):
            if i < 3:
                times[prayer] = now - timedelta(hours=3 - i)
            else:
                times[prayer] = now + timedelta(hours=i)

        mock_get_times.return_value = times

        scheduler._schedule_prayers_for_today()

        jobs = [j for j in scheduler.scheduler.get_jobs() if j.id.startswith("prayer_")]
        assert len(jobs) == 2

    @patch("src.scheduler.get_prayer_times")
    def test_start_and_shutdown(self, mock_get_times, scheduler):
        tz = ZoneInfo("America/Los_Angeles")
        now = datetime.now(tz)
        mock_get_times.return_value = {
            p: now + timedelta(hours=i + 1) for i, p in enumerate(PRAYER_NAMES)
        }

        scheduler.start()
        assert scheduler.scheduler.running

        # Should have daily recalc job + 5 prayer jobs
        job_ids = [j.id for j in scheduler.scheduler.get_jobs()]
        assert "daily_recalc" in job_ids

        scheduler.shutdown()
        assert not scheduler.scheduler.running

    @patch("src.scheduler.get_prayer_times")
    def test_reschedule_clears_old_jobs(self, mock_get_times, scheduler):
        tz = ZoneInfo("America/Los_Angeles")
        now = datetime.now(tz)

        # First schedule: all future
        mock_get_times.return_value = {
            p: now + timedelta(hours=i + 1) for i, p in enumerate(PRAYER_NAMES)
        }
        scheduler._schedule_prayers_for_today()
        jobs1 = [j for j in scheduler.scheduler.get_jobs() if j.id.startswith("prayer_")]
        assert len(jobs1) == 5

        # Reschedule: only 2 future
        times = {}
        for i, prayer in enumerate(PRAYER_NAMES):
            if i < 3:
                times[prayer] = now - timedelta(hours=1)
            else:
                times[prayer] = now + timedelta(hours=i + 1)
        mock_get_times.return_value = times

        scheduler._schedule_prayers_for_today()
        jobs2 = [j for j in scheduler.scheduler.get_jobs() if j.id.startswith("prayer_")]
        assert len(jobs2) == 2
