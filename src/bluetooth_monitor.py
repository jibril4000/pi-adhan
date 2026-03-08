"""Monitor PulseAudio for Bluetooth audio source connections."""

import logging
import subprocess
import threading

logger = logging.getLogger("adhan.bluetooth")

POLL_INTERVAL = 2  # seconds


def _has_bluez_source() -> bool:
    """Check if any Bluetooth audio source is currently active in PulseAudio."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=5,
        )
        return "bluez_source" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


class BluetoothMonitor:
    """Watches for Bluetooth A2DP sources by reacting to PulseAudio events."""

    def __init__(self, background_player):
        self.background = background_player
        self._thread = None
        self._stop_event = threading.Event()
        self._bt_connected = False
        self._process = None

    def start(self) -> None:
        """Start monitoring in a background thread."""
        # Check initial state
        self._bt_connected = _has_bluez_source()
        if self._bt_connected:
            logger.info("Bluetooth audio already connected at startup")
            self.background.notify_bluetooth_connect()

        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("Bluetooth monitor started")

    def stop(self) -> None:
        """Stop monitoring."""
        self._stop_event.set()
        if self._process:
            self._process.terminate()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Bluetooth monitor stopped")

    def _check_bt_state(self) -> None:
        """Check current Bluetooth source state and notify on changes."""
        bt_now = _has_bluez_source()
        if bt_now and not self._bt_connected:
            self._bt_connected = True
            logger.info("Bluetooth audio source connected")
            self.background.notify_bluetooth_connect()
        elif not bt_now and self._bt_connected:
            self._bt_connected = False
            logger.info("Bluetooth audio source disconnected")
            self.background.notify_bluetooth_disconnect()

    def _monitor_loop(self) -> None:
        """Use pactl subscribe to react to audio events, with polling fallback."""
        while not self._stop_event.is_set():
            try:
                self._process = subprocess.Popen(
                    ["pactl", "subscribe"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )

                for line in self._process.stdout:
                    if self._stop_event.is_set():
                        break
                    # React to any source or card event (connect/disconnect)
                    if "source" in line or "card" in line:
                        self._check_bt_state()

                self._process.wait()
            except FileNotFoundError:
                logger.warning(
                    "pactl not found — falling back to polling. "
                    "Install PulseAudio: sudo apt install pulseaudio"
                )
                self._poll_loop()
                break
            except Exception as e:
                logger.error("Bluetooth monitor error: %s", e)
                if not self._stop_event.is_set():
                    self._stop_event.wait(5)

    def _poll_loop(self) -> None:
        """Fallback: poll for Bluetooth sources periodically."""
        while not self._stop_event.is_set():
            self._check_bt_state()
            self._stop_event.wait(POLL_INTERVAL)
