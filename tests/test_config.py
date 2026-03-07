"""Tests for config module."""

import os
import tempfile

import pytest
import yaml

from src.config import (
    AppConfig,
    AudioConfig,
    CalculationConfig,
    LocationConfig,
    LoggingConfig,
    SchedulerConfig,
    load_config,
    validate_audio_files,
)


@pytest.fixture
def valid_config_data():
    return {
        "location": {
            "latitude": 34.3078,
            "longitude": -118.4467,
            "timezone": "America/Los_Angeles",
        },
        "calculation": {"method": "NORTH_AMERICA"},
        "audio": {
            "default_file": "audio/adhan_default.mp3",
            "per_prayer": {"fajr": "audio/adhan_fajr.mp3"},
            "volume": 100,
        },
        "scheduler": {
            "daily_recalc_time": "00:05",
            "misfire_grace_seconds": 300,
        },
        "logging": {
            "file": "logs/adhan.log",
            "max_bytes": 5242880,
            "backup_count": 3,
            "level": "INFO",
        },
    }


@pytest.fixture
def config_file(valid_config_data, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(valid_config_data))
    return str(path)


class TestLocationConfig:
    def test_valid_location(self):
        loc = LocationConfig(34.3078, -118.4467, "America/Los_Angeles")
        assert loc.latitude == 34.3078
        assert loc.longitude == -118.4467

    def test_invalid_latitude(self):
        with pytest.raises(ValueError, match="Latitude"):
            LocationConfig(91.0, 0.0, "UTC")

    def test_invalid_longitude(self):
        with pytest.raises(ValueError, match="Longitude"):
            LocationConfig(0.0, 181.0, "UTC")

    def test_invalid_timezone(self):
        with pytest.raises(Exception):
            LocationConfig(0.0, 0.0, "Not/A/Timezone")


class TestCalculationConfig:
    def test_valid_method(self):
        calc = CalculationConfig(method="NORTH_AMERICA")
        assert calc.method == "NORTH_AMERICA"

    def test_case_insensitive(self):
        calc = CalculationConfig(method="north_america")
        assert calc.method == "NORTH_AMERICA"

    def test_invalid_method(self):
        with pytest.raises(ValueError, match="Unknown calculation method"):
            CalculationConfig(method="INVALID")


class TestAudioConfig:
    def test_valid_audio(self):
        audio = AudioConfig(default_file="test.mp3", per_prayer={"fajr": "fajr.mp3"}, volume=80)
        assert audio.volume == 80

    def test_invalid_volume(self):
        with pytest.raises(ValueError, match="Volume"):
            AudioConfig(volume=101)

    def test_invalid_prayer_name(self):
        with pytest.raises(ValueError, match="Unknown prayer"):
            AudioConfig(per_prayer={"zuhr": "test.mp3"})


class TestSchedulerConfig:
    def test_valid_time(self):
        sched = SchedulerConfig(daily_recalc_time="00:05")
        assert sched.daily_recalc_time == "00:05"

    def test_invalid_time_format(self):
        with pytest.raises(ValueError):
            SchedulerConfig(daily_recalc_time="25:00")


class TestLoadConfig:
    def test_load_valid_config(self, config_file):
        config = load_config(config_file)
        assert isinstance(config, AppConfig)
        assert config.location.latitude == 34.3078
        assert config.calculation.method == "NORTH_AMERICA"
        assert config.audio.volume == 100

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path.yaml")

    def test_defaults_applied(self, tmp_path):
        minimal = {
            "location": {
                "latitude": 34.3078,
                "longitude": -118.4467,
                "timezone": "America/Los_Angeles",
            }
        }
        path = tmp_path / "minimal.yaml"
        path.write_text(yaml.dump(minimal))
        config = load_config(str(path))
        assert config.calculation.method == "NORTH_AMERICA"
        assert config.audio.volume == 100
        assert config.scheduler.misfire_grace_seconds == 300


class TestValidateAudioFiles:
    def test_missing_default_file(self, config_file):
        config = load_config(config_file)
        missing = validate_audio_files(config)
        assert len(missing) >= 1

    def test_files_present(self, tmp_path):
        # Create audio files
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        (audio_dir / "adhan.mp3").write_text("fake")

        data = {
            "location": {
                "latitude": 0.0,
                "longitude": 0.0,
                "timezone": "UTC",
            },
            "audio": {
                "default_file": "audio/adhan.mp3",
                "per_prayer": {},
                "volume": 100,
            },
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data))

        config = load_config(str(path))
        missing = validate_audio_files(config)
        assert missing == []
