"""Tests for prayer_times module."""

from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import requests as requests_lib

from src.config import PRAYER_NAMES
from src.prayer_times import calculate_offline, fetch_from_api, get_prayer_times


SYLMAR_LAT = 34.3078
SYLMAR_LON = -118.4467
TZ = "America/Los_Angeles"
METHOD = "NORTH_AMERICA"


class TestCalculateOffline:
    def test_returns_all_prayers(self):
        times = calculate_offline(date.today(), SYLMAR_LAT, SYLMAR_LON, METHOD, TZ)
        assert set(times.keys()) == set(PRAYER_NAMES)

    def test_returns_timezone_aware_datetimes(self):
        times = calculate_offline(date.today(), SYLMAR_LAT, SYLMAR_LON, METHOD, TZ)
        for prayer, dt in times.items():
            assert isinstance(dt, datetime), f"{prayer} is not a datetime"
            assert dt.tzinfo is not None, f"{prayer} is not timezone-aware"

    def test_prayer_order(self):
        """Fajr < Dhuhr < Asr < Maghrib < Isha (usually)."""
        times = calculate_offline(date(2025, 3, 15), SYLMAR_LAT, SYLMAR_LON, METHOD, TZ)
        assert times["fajr"] < times["dhuhr"]
        assert times["dhuhr"] < times["asr"]
        assert times["asr"] < times["maghrib"]
        assert times["maghrib"] < times["isha"]

    def test_different_methods(self):
        t1 = calculate_offline(date.today(), SYLMAR_LAT, SYLMAR_LON, "NORTH_AMERICA", TZ)
        t2 = calculate_offline(date.today(), SYLMAR_LAT, SYLMAR_LON, "MUSLIM_WORLD_LEAGUE", TZ)
        # Different methods should produce different Fajr/Isha times (different angles)
        assert t1["fajr"] != t2["fajr"] or t1["isha"] != t2["isha"]


class TestFetchFromApi:
    @patch("src.prayer_times.requests.get")
    def test_successful_fetch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "timings": {
                    "Fajr": "05:30",
                    "Dhuhr": "12:15",
                    "Asr": "15:45",
                    "Maghrib": "18:20",
                    "Isha": "19:50",
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = fetch_from_api(date(2025, 3, 15), SYLMAR_LAT, SYLMAR_LON, METHOD, TZ)
        assert result is not None
        assert set(result.keys()) == set(PRAYER_NAMES)
        assert result["fajr"].hour == 5
        assert result["fajr"].minute == 30

    @patch("src.prayer_times.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = requests_lib.ConnectionError("No network")
        result = fetch_from_api(date.today(), SYLMAR_LAT, SYLMAR_LON, METHOD, TZ)
        assert result is None

    @patch("src.prayer_times.requests.get")
    def test_bad_response_format(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"unexpected": "format"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = fetch_from_api(date.today(), SYLMAR_LAT, SYLMAR_LON, METHOD, TZ)
        assert result is None

    @patch("src.prayer_times.requests.get")
    def test_handles_timezone_in_time_string(self, mock_get):
        """Aladhan sometimes returns 'HH:MM (TZA)' format."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "timings": {
                    "Fajr": "05:30 (PST)",
                    "Dhuhr": "12:15 (PST)",
                    "Asr": "15:45 (PST)",
                    "Maghrib": "18:20 (PST)",
                    "Isha": "19:50 (PST)",
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = fetch_from_api(date(2025, 3, 15), SYLMAR_LAT, SYLMAR_LON, METHOD, TZ)
        assert result is not None
        assert result["fajr"].hour == 5


class TestGetPrayerTimes:
    @patch("src.prayer_times.fetch_from_api")
    def test_uses_api_when_available(self, mock_api):
        tz = ZoneInfo(TZ)
        mock_times = {
            p: datetime(2025, 3, 15, 5 + i * 3, 0, tzinfo=tz)
            for i, p in enumerate(PRAYER_NAMES)
        }
        mock_api.return_value = mock_times

        result = get_prayer_times(date(2025, 3, 15), SYLMAR_LAT, SYLMAR_LON, METHOD, TZ)
        assert result == mock_times
        mock_api.assert_called_once()

    @patch("src.prayer_times.fetch_from_api")
    def test_falls_back_to_offline(self, mock_api):
        mock_api.return_value = None

        result = get_prayer_times(date(2025, 3, 15), SYLMAR_LAT, SYLMAR_LON, METHOD, TZ)
        assert set(result.keys()) == set(PRAYER_NAMES)
        # Should still get valid times from offline calculation
        for dt in result.values():
            assert isinstance(dt, datetime)
            assert dt.tzinfo is not None
