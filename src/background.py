"""Background ambient audio playback with mpv IPC control."""

import json
import logging
import os
import signal as sig
import socket
import subprocess
import threading
import time

from src.config import AppConfig, is_quiet_time

logger = logging.getLogger("adhan.background")

SOCKET_PATH = "/tmp/adhan-mpv-socket"
FADE_STEPS = 20
WATCHDOG_INTERVAL = 10  # seconds


class BackgroundPlayer:
    """Manages background ambient audio via a persistent mpv process with IPC control."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.volume = config.background.volume
        self.fade_duration = config.background.fade_duration
        self.audio_path = os.path.join(config.base_dir, config.background.file)
        self._process = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._watchdog_thread = None
        self._paused = False
        self.adhan_active = False
        self.bluetooth_active = False
        self.quiet_active = False

    def start(self) -> None:
        """Launch the background mpv process and watchdog."""
        if not os.path.isfile(self.audio_path):
            logger.error("Background audio file not found: %s", self.audio_path)
            return

        self._start_mpv()

        # Check quiet hours immediately on startup (don't wait for watchdog cycle)
        if self._is_quiet_time():
            logger.info("Starting during quiet hours — pausing background audio")
            self._freeze()
            self.quiet_active = True

        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

    def _start_mpv(self) -> None:
        """Launch the mpv subprocess."""
        # Remove stale socket
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

        cmd = [
            "mpv",
            "--no-video",
            "--really-quiet",
            f"--volume={self.volume}",
            "--loop=inf",
            f"--input-ipc-server={SOCKET_PATH}",
            self.audio_path,
        ]

        logger.info("Starting background audio: %s (volume: %d)", self.audio_path, self.volume)
        self._process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # Wait for socket to become available
        for _ in range(50):
            if os.path.exists(SOCKET_PATH):
                break
            time.sleep(0.1)
        else:
            logger.warning("mpv IPC socket not ready after 5s")

    def _is_quiet_time(self) -> bool:
        """Check if the current time falls within any configured quiet hours."""
        return is_quiet_time(
            self.config.background.quiet_hours,
            self.config.location.timezone,
        )

    def _freeze(self) -> None:
        """Freeze mpv process using SIGSTOP (guaranteed to stop audio output)."""
        if self._process and self._process.poll() is None and not self._paused:
            os.kill(self._process.pid, sig.SIGSTOP)
            self._paused = True
            logger.debug("mpv process frozen (SIGSTOP)")

    def _unfreeze(self) -> None:
        """Resume mpv process using SIGCONT."""
        if self._process and self._process.poll() is None and self._paused:
            os.kill(self._process.pid, sig.SIGCONT)
            self._paused = False
            logger.debug("mpv process resumed (SIGCONT)")

    def _restart_mpv(self) -> None:
        """Kill the current mpv process and start a fresh one.

        Used instead of SIGCONT after long pauses (e.g. Bluetooth disconnect)
        to avoid choppy audio from stale buffers.
        """
        logger.info("Restarting background mpv for clean audio state")
        if self._process:
            # Unfreeze first so terminate can reach it
            if self._paused:
                try:
                    os.kill(self._process.pid, sig.SIGCONT)
                except OSError:
                    pass
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except (ProcessLookupError, OSError):
                pass  # Process already dead
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        self._paused = False
        self._start_mpv()

    def _watchdog_loop(self) -> None:
        """Restart mpv if it dies unexpectedly, and enforce quiet hours."""
        while not self._stop_event.is_set():
            self._stop_event.wait(WATCHDOG_INTERVAL)
            if self._stop_event.is_set():
                break

            try:
                # Only restart if not intentionally paused
                if self._process and self._process.poll() is not None and not self._paused:
                    logger.warning("Background mpv process died (exit code %d), restarting...",
                                   self._process.returncode)
                    self._start_mpv()

                # Check quiet hours
                in_quiet = self._is_quiet_time()
                with self._lock:
                    was_quiet = self.quiet_active
                    self.quiet_active = in_quiet

                if in_quiet and not was_quiet:
                    logger.info("Entering quiet hours — pausing background audio")
                    self._freeze()
                elif not in_quiet and was_quiet:
                    logger.info("Quiet hours ended — restarting background audio")
                    if not self.adhan_active and not self.bluetooth_active:
                        self._restart_mpv()
            except Exception:
                logger.exception("Error in background watchdog loop")

    def stop(self) -> None:
        """Stop the background mpv process and watchdog."""
        self._stop_event.set()
        if self._process:
            # Unfreeze first so terminate can reach it
            if self._paused:
                try:
                    os.kill(self._process.pid, sig.SIGCONT)
                except OSError:
                    pass
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        logger.info("Background audio stopped")

    def _send_command(self, command: list) -> dict | None:
        """Send a command to mpv via IPC socket."""
        if not os.path.exists(SOCKET_PATH):
            return None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(SOCKET_PATH)
            payload = json.dumps({"command": command}) + "\n"
            sock.sendall(payload.encode())
            response = sock.recv(4096)
            sock.close()
            return json.loads(response.decode().strip().split("\n")[0])
        except (socket.error, json.JSONDecodeError, OSError) as e:
            logger.debug("mpv IPC error: %s", e)
            return None

    def _set_volume(self, volume: int) -> None:
        self._send_command(["set_property", "volume", volume])

    def fade_out(self) -> None:
        """Gradually reduce volume to 0, then freeze the process."""
        logger.info("Fading out background audio")
        step_delay = self.fade_duration / FADE_STEPS
        volume_step = self.volume / FADE_STEPS

        for i in range(FADE_STEPS):
            vol = max(0, int(self.volume - volume_step * (i + 1)))
            self._set_volume(vol)
            time.sleep(step_delay)

        self._freeze()
        logger.info("Background audio faded out and stopped")

    def fade_in(self) -> None:
        """Unfreeze the process and gradually increase volume."""
        logger.info("Fading in background audio")
        self._set_volume(0)
        self._unfreeze()

        step_delay = self.fade_duration / FADE_STEPS
        volume_step = self.volume / FADE_STEPS

        for i in range(FADE_STEPS):
            vol = min(self.volume, int(volume_step * (i + 1)))
            self._set_volume(vol)
            time.sleep(step_delay)

        logger.info("Background audio faded in (volume: %d)", self.volume)

    def notify_adhan_start(self) -> None:
        """Called when adhan is about to play."""
        with self._lock:
            self.adhan_active = True
        self.fade_out()

    def notify_adhan_end(self) -> None:
        """Called when adhan is done."""
        with self._lock:
            self.adhan_active = False
            should_resume = not self.bluetooth_active and not self.quiet_active
        if should_resume:
            self.fade_in()
        else:
            logger.info("Not resuming background after adhan (bluetooth=%s, quiet=%s)",
                        self.bluetooth_active, self.quiet_active)

    def notify_bluetooth_connect(self) -> None:
        """Called when a Bluetooth audio source connects."""
        with self._lock:
            self.bluetooth_active = True
            should_pause = not self.adhan_active
        if should_pause:
            logger.info("Bluetooth connected — stopping background audio")
            self._freeze()

    def notify_bluetooth_disconnect(self) -> None:
        """Called when Bluetooth audio source disconnects."""
        with self._lock:
            self.bluetooth_active = False
            should_resume = not self.adhan_active and not self.quiet_active
        if should_resume:
            self._restart_mpv()
            logger.info("Bluetooth disconnected — background audio restarted fresh")
        elif self.quiet_active:
            logger.info("Bluetooth disconnected but in quiet hours, staying paused")
