"""Audio playback via mpv subprocess with per-prayer file selection."""

import logging
import os
import subprocess

from src.config import AppConfig

logger = logging.getLogger("adhan.player")

PLAYBACK_TIMEOUT = 600  # 10 minutes max


class AdhanPlayer:
    """Plays adhan audio files using mpv."""

    def __init__(self, config: AppConfig):
        self.config = config

    def _resolve_audio_path(self, prayer_name: str) -> str:
        """Get the audio file path for a given prayer.

        Uses per-prayer override if configured and file exists,
        otherwise falls back to default.
        """
        per_prayer = self.config.audio.per_prayer
        if prayer_name in per_prayer:
            path = os.path.join(self.config.base_dir, per_prayer[prayer_name])
            if os.path.isfile(path):
                return path
            logger.warning(
                "Per-prayer audio for %s not found at %s, using default",
                prayer_name,
                path,
            )

        return os.path.join(self.config.base_dir, self.config.audio.default_file)

    def play_adhan(self, prayer_name: str) -> bool:
        """Play the adhan for a specific prayer.

        Args:
            prayer_name: One of fajr, dhuhr, asr, maghrib, isha.

        Returns:
            True if playback succeeded, False otherwise.
        """
        audio_path = self._resolve_audio_path(prayer_name)

        if not os.path.isfile(audio_path):
            logger.error("Audio file not found: %s", audio_path)
            return False

        volume = self.config.audio.volume
        cmd = [
            "mpv",
            "--no-video",
            "--really-quiet",
            f"--volume={volume}",
            audio_path,
        ]

        logger.info("Playing adhan for %s: %s", prayer_name, audio_path)

        try:
            result = subprocess.run(
                cmd,
                timeout=PLAYBACK_TIMEOUT,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.error(
                    "mpv exited with code %d: %s",
                    result.returncode,
                    result.stderr.strip(),
                )
                return False
            logger.info("Adhan playback complete for %s", prayer_name)
            return True
        except subprocess.TimeoutExpired:
            logger.error("mpv playback timed out after %ds for %s", PLAYBACK_TIMEOUT, prayer_name)
            return False
        except FileNotFoundError:
            logger.error("mpv not found. Install it: sudo apt install mpv")
            return False
