"""Control a connected phone's media playback over Bluetooth AVRCP.

When a phone is connected as a Bluetooth audio source it streams independently
of this daemon's own players, so muting the radio/background does nothing to it.
BlueZ exposes the phone's player as an ``org.bluez.MediaPlayer1`` object on the
system D-Bus, which lets us send AVRCP Play/Pause — used to pause the phone for
the duration of an adhan and resume it afterwards.

Everything shells out to ``busctl`` (same approach as the PulseAudio polling in
bluetooth_monitor.py) so there's no python-dbus dependency. All failures are
swallowed and logged: media control is best-effort and must never break adhan
playback.
"""

from __future__ import annotations

import logging
import re
import subprocess

logger = logging.getLogger("adhan.bt_media")

_PLAYER_RE = re.compile(r"/org/bluez/hci\d+/dev_[0-9A-F_]+/player\d+")
_QUOTED_RE = re.compile(r'"([^"]+)"')
_IFACE = "org.bluez.MediaPlayer1"


def _find_player_path() -> str | None:
    """Return the D-Bus object path of a connected phone's media player, if any."""
    try:
        result = subprocess.run(
            ["busctl", "--system", "tree", "org.bluez"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("Could not list bluez objects: %s", e)
        return None
    match = _PLAYER_RE.search(result.stdout)
    return match.group(0) if match else None


def _get_status(path: str) -> str | None:
    """Return the player's status ('playing', 'paused', ...) or None."""
    try:
        result = subprocess.run(
            ["busctl", "--system", "get-property", "org.bluez", path, _IFACE, "Status"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    match = _QUOTED_RE.search(result.stdout)
    return match.group(1) if match else None


def _call(path: str, method: str) -> bool:
    """Invoke a no-argument MediaPlayer1 method. Returns True on success."""
    try:
        result = subprocess.run(
            ["busctl", "--system", "call", "org.bluez", path, _IFACE, method],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("bluez %s call failed: %s", method, e)
        return False
    if result.returncode != 0:
        logger.debug("bluez %s returned %d: %s", method, result.returncode, result.stderr.strip())
        return False
    return True


def pause_if_playing() -> str | None:
    """Pause the connected phone if it's currently playing.

    Returns the player path if we actually paused it (so the caller can resume
    the same player later), or None if nothing was playing / no phone is present.
    """
    path = _find_player_path()
    if not path:
        return None
    if _get_status(path) != "playing":
        return None
    logger.info("Pausing Bluetooth phone media for adhan")
    return path if _call(path, "Pause") else None


def resume(path: str | None) -> None:
    """Resume playback on a player previously paused by pause_if_playing()."""
    if not path:
        return
    logger.info("Resuming Bluetooth phone media after adhan")
    _call(path, "Play")
