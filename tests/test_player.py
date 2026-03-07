"""Tests for player module."""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.config import AppConfig, AudioConfig, CalculationConfig, LocationConfig, LoggingConfig, PrayersConfig, SchedulerConfig
from src.player import AdhanPlayer


@pytest.fixture
def config(tmp_path):
    # Create audio files
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "adhan_default.mp3").write_text("fake mp3 data")
    (audio_dir / "adhan_fajr.mp3").write_text("fake fajr mp3 data")

    return AppConfig(
        location=LocationConfig(34.3078, -118.4467, "America/Los_Angeles"),
        calculation=CalculationConfig(method="NORTH_AMERICA"),
        audio=AudioConfig(
            default_file="audio/adhan_default.mp3",
            per_prayer={"fajr": "audio/adhan_fajr.mp3"},
            volume=100,
        ),
        prayers=PrayersConfig(),
        scheduler=SchedulerConfig(),
        logging=LoggingConfig(),
        base_dir=str(tmp_path),
    )


@pytest.fixture
def player(config):
    return AdhanPlayer(config)


class TestResolveAudioPath:
    def test_default_file(self, player, config):
        path = player._resolve_audio_path("dhuhr")
        expected = os.path.join(config.base_dir, "audio/adhan_default.mp3")
        assert path == expected

    def test_per_prayer_override(self, player, config):
        path = player._resolve_audio_path("fajr")
        expected = os.path.join(config.base_dir, "audio/adhan_fajr.mp3")
        assert path == expected

    def test_missing_per_prayer_falls_back(self, config):
        # Override with a non-existent file
        config.audio.per_prayer["asr"] = "audio/nonexistent.mp3"
        player = AdhanPlayer(config)
        path = player._resolve_audio_path("asr")
        expected = os.path.join(config.base_dir, "audio/adhan_default.mp3")
        assert path == expected


class TestPlayAdhan:
    @patch("src.player.subprocess.run")
    def test_successful_playback(self, mock_run, player):
        mock_run.return_value = MagicMock(returncode=0)
        assert player.play_adhan("fajr") is True
        mock_run.assert_called_once()

        # Verify mpv command
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "mpv"
        assert "--no-video" in cmd
        assert "--really-quiet" in cmd
        assert "--volume=100" in cmd

    @patch("src.player.subprocess.run")
    def test_failed_playback(self, mock_run, player):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        assert player.play_adhan("dhuhr") is False

    @patch("src.player.subprocess.run")
    def test_timeout(self, mock_run, player):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="mpv", timeout=600)
        assert player.play_adhan("asr") is False

    @patch("src.player.subprocess.run")
    def test_mpv_not_found(self, mock_run, player):
        mock_run.side_effect = FileNotFoundError()
        assert player.play_adhan("maghrib") is False

    def test_missing_audio_file(self, config):
        config.audio.default_file = "audio/nonexistent.mp3"
        config.audio.per_prayer = {}
        player = AdhanPlayer(config)
        assert player.play_adhan("isha") is False
