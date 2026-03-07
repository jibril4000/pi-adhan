"""Monitor PulseAudio for Bluetooth audio source connections."""

import logging
import subprocess
import threading

logger = logging.getLogger("adhan.bluetooth")


class BluetoothMonitor:
    """Watches for Bluetooth A2DP sources via pactl subscribe."""

    def __init__(self, background_player):
        self.background = background_player
        self._thread = None
        self._stop_event = threading.Event()
        self._bt_connected = False
        self._process = None

    def start(self) -> None:
        """Start monitoring in a background thread."""
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

    def _monitor_loop(self) -> None:
        """Run pactl subscribe and watch for bluez source events."""
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
                    if "bluez" not in line.lower():
                        continue

                    if "'new'" in line and "source" in line:
                        if not self._bt_connected:
                            self._bt_connected = True
                            logger.info("Bluetooth audio source connected")
                            self.background.notify_bluetooth_connect()
                    elif "'remove'" in line and "source" in line:
                        if self._bt_connected:
                            self._bt_connected = False
                            logger.info("Bluetooth audio source disconnected")
                            self.background.notify_bluetooth_disconnect()

                self._process.wait()
            except FileNotFoundError:
                logger.warning(
                    "pactl not found — Bluetooth monitoring disabled. "
                    "Install PulseAudio: sudo apt install pulseaudio"
                )
                break
            except Exception as e:
                logger.error("Bluetooth monitor error: %s", e)
                if not self._stop_event.is_set():
                    self._stop_event.wait(5)  # Wait before retry
