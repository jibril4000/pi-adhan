"""MMR radio streaming player — drop-in replacement for BackgroundPlayer.

Streams the full Mystic Records catalog through mpv during a configurable
daily window (e.g. 7 AM – 7 PM).  Implements the same interface as
BackgroundPlayer so AdhanPlayer and BluetoothMonitor work unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import random
import signal as sig
import socket
import subprocess
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from src.api_client import MMRApiClient
from src.config import AppConfig, is_quiet_time

logger = logging.getLogger("adhan.radio")

SOCKET_PATH = "/tmp/mmr-radio-mpv-socket"
FADE_STEPS = 20
WATCHDOG_INTERVAL = 10  # seconds
CATALOG_REFRESH_HOURS = 24
SILENCE_FALLBACK_SECS = 30  # radio silent this long → hand audio to background
RESUME_STABLE_SECS = 20  # radio must play steadily this long → reclaim from background


class RadioPlayer:
    """Streams the MMR catalog via mpv, pausing for adhan and Bluetooth.

    When a BackgroundPlayer is provided, RadioPlayer acts as the front-facing
    player: during its schedule window it streams MMR tracks, and outside the
    window it delegates adhan/bluetooth notifications to the background player.
    """

    def __init__(self, config: AppConfig, background_player=None):
        self.config = config
        self.volume = config.radio.volume
        self.fade_duration = config.radio.fade_duration
        self._background = background_player
        self.api_client = MMRApiClient(
            api_url=config.radio.api_url,
            email=config.radio.email,
            password=config.radio.password,
        )

        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._event_thread: threading.Thread | None = None
        self._paused = False
        self._playing = False  # True when actively streaming (inside window)
        self._audible = False  # True when the radio is the live audible source
        self._idle_secs = 0.0  # how long the radio has produced no sound
        self._stable_secs = 0.0  # how long the radio has played steadily while in fallback

        # State flags (same as BackgroundPlayer)
        self.adhan_active = False
        self.bluetooth_active = False

        # Catalog & queue
        self._catalog: list[dict] = []
        self._queue: list[dict] = []
        self._current_track: dict | None = None
        self._catalog_refreshed_at = 0.0

    # ── Public interface (matches BackgroundPlayer) ──────────────

    def start(self) -> None:
        """Initialize radio: login, fetch catalog, start background threads."""
        if not self.api_client.login():
            logger.error("MMR login failed — radio will retry in background")
        else:
            self._refresh_catalog()

        # Event listener thread (long-lived, auto-reconnects to mpv IPC)
        self._event_thread = threading.Thread(
            target=self._event_listener_loop, daemon=True,
        )
        self._event_thread.start()

        # Watchdog thread (window transitions, catalog refresh, crash recovery)
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True,
        )
        self._watchdog_thread.start()

        # If we're already inside the play window, start immediately — unless
        # Bluetooth or an adhan currently owns the audio slot.
        if self._catalog and self._is_in_window() and not self._bt_or_adhan_active():
            if self._background:
                self._background.radio_active = True
                self._background._freeze()
            self._start_playing()

    def stop(self) -> None:
        """Shut down the radio and background player completely."""
        self._stop_event.set()
        self._stop_playing()
        if self._background:
            self._background.stop()
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        logger.info("Radio stopped")

    def notify_adhan_start(self) -> None:
        """Called when adhan is about to play."""
        if self._playing and self._audible:
            with self._lock:
                self.adhan_active = True
            if self._paused:
                logger.info("Radio already paused, skipping fade-out for adhan")
            else:
                self.fade_out()
        elif self._background:
            self._background.notify_adhan_start()

    def notify_adhan_end(self) -> None:
        """Called when adhan playback is done."""
        if self._playing and self._audible:
            with self._lock:
                self.adhan_active = False
                should_resume = not self.bluetooth_active
            if should_resume:
                self.fade_in()
            else:
                logger.info("Not resuming radio after adhan (bluetooth=%s)", self.bluetooth_active)
        elif self._background:
            self._background.notify_adhan_end()

    def notify_bluetooth_connect(self) -> None:
        """Called when a Bluetooth audio source connects."""
        if self._playing and self._audible:
            with self._lock:
                self.bluetooth_active = True
                should_pause = not self.adhan_active
            if should_pause:
                logger.info("Bluetooth connected — pausing radio")
                self._freeze()
        elif self._background:
            self._background.notify_bluetooth_connect()

    def notify_bluetooth_disconnect(self) -> None:
        """Called when Bluetooth audio source disconnects."""
        if self._playing and self._audible:
            with self._lock:
                self.bluetooth_active = False
                should_resume = not self.adhan_active
            if should_resume:
                self._restart_mpv()
                logger.info("Bluetooth disconnected — radio restarted fresh")
        elif self._background:
            self._background.notify_bluetooth_disconnect()

    # ── Fade control ─────────────────────────────────────────────

    def fade_out(self) -> None:
        """Gradually reduce volume to 0, then freeze mpv."""
        logger.info("Fading out radio")
        step_delay = self.fade_duration / FADE_STEPS
        volume_step = self.volume / FADE_STEPS
        for i in range(FADE_STEPS):
            vol = max(0, int(self.volume - volume_step * (i + 1)))
            self._set_volume(vol)
            time.sleep(step_delay)
        self._freeze()
        logger.info("Radio faded out and paused")

    def fade_in(self) -> None:
        """Unfreeze mpv and gradually restore volume."""
        logger.info("Fading in radio")
        self._unfreeze()
        self._set_volume(0)
        step_delay = self.fade_duration / FADE_STEPS
        volume_step = self.volume / FADE_STEPS
        for i in range(FADE_STEPS):
            vol = min(self.volume, int(volume_step * (i + 1)))
            self._set_volume(vol)
            time.sleep(step_delay)
        logger.info("Radio faded in (volume: %d)", self.volume)

    # ── Schedule window ──────────────────────────────────────────

    def _bt_or_adhan_active(self) -> bool:
        """True if Bluetooth or an adhan is currently taking the audio slot.

        Reads the background player's flags, which are maintained even while the
        radio is stopped (the radio's own flags are only tracked while it plays).
        This is the source of truth when deciding whether it's safe to *start*
        the radio.
        """
        if self._background:
            return self._background.bluetooth_active or self._background.adhan_active
        return self.bluetooth_active or self.adhan_active

    def _is_in_window(self) -> bool:
        """Check if the current time is within any configured schedule window.

        Quiet hours (e.g. the Friday Jummah window) override the schedule: the
        radio goes silent during them, just like the background player, so the
        whole system stays quiet.
        """
        if is_quiet_time(
            self.config.background.quiet_hours, self.config.location.timezone
        ):
            return False
        now = datetime.now(ZoneInfo(self.config.location.timezone))
        day_name = now.strftime("%A").lower()
        current = now.hour * 60 + now.minute

        for entry in self.config.radio.schedule:
            if day_name not in entry.days:
                continue
            start_h, start_m = map(int, entry.start.split(":"))
            end_h, end_m = map(int, entry.end.split(":"))
            start = start_h * 60 + start_m
            end = end_h * 60 + end_m
            if start <= current < end:
                return True
        return False

    # ── Playback control ─────────────────────────────────────────

    def _start_playing(self) -> None:
        """Start mpv and begin streaming tracks."""
        if self._playing:
            return
        logger.info("Radio starting playback")
        self._audible = True
        self._idle_secs = 0.0
        self._stable_secs = 0.0
        self._start_mpv()
        self._playing = True
        self._play_next()

    def _stop_playing(self) -> None:
        """Stop playback entirely (end of window or shutdown)."""
        if not self._playing and not self._process:
            return
        logger.info("Radio stopping playback")
        self._playing = False
        self._audible = False
        self._idle_secs = 0.0
        self._stable_secs = 0.0
        self._kill_mpv()
        self._current_track = None

    def _play_next(self) -> None:
        """Pop the next track from the queue and load it into mpv."""
        if not self._playing:
            return

        with self._lock:
            if not self._queue:
                self._build_queue()
            if not self._queue:
                logger.warning("No tracks in queue — cannot play")
                return
            track = self._queue.pop(0)
            self._current_track = track

        artist_rel = track.get("artistRelation") or {}
        artist_name = artist_rel.get("name", "Unknown Artist")
        duration = track.get("duration", 0)
        mins, secs = divmod(duration, 60)
        logger.info("Now playing: %s — %s (%d:%02d)", artist_name, track["title"], mins, secs)
        self._send_command(["loadfile", track["mediaUrl"]])

    def _build_queue(self) -> None:
        """Populate the play queue from the catalog."""
        if not self._catalog:
            return
        self._queue = list(self._catalog)
        if self.config.radio.shuffle:
            random.shuffle(self._queue)
        logger.info(
            "Built play queue: %d tracks (shuffle=%s)",
            len(self._queue), self.config.radio.shuffle,
        )

    # ── Catalog management ───────────────────────────────────────

    def _refresh_catalog(self) -> None:
        """Fetch the full track catalog from the MMR API."""
        tracks = self.api_client.fetch_all_tracks()
        if tracks:
            self._catalog = tracks
            self._catalog_refreshed_at = time.time()
            logger.info("Catalog refreshed: %d tracks available", len(tracks))
        else:
            logger.warning("Catalog refresh returned no tracks — keeping previous catalog")

    def _should_refresh_catalog(self) -> bool:
        if not self._catalog_refreshed_at:
            return True
        elapsed = time.time() - self._catalog_refreshed_at
        return elapsed > CATALOG_REFRESH_HOURS * 3600

    # ── mpv process management ───────────────────────────────────

    def _start_mpv(self) -> None:
        """Launch mpv in idle mode with IPC socket."""
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

        # Stay muted while the background is the audible fallback, so the
        # radio can keep buffering/probing without bleeding through.
        vol = self.volume if self._audible else 0
        cmd = [
            "mpv",
            "--idle=yes",
            "--no-video",
            "--really-quiet",
            f"--volume={vol}",
            f"--input-ipc-server={SOCKET_PATH}",
        ]
        logger.debug("Starting mpv: %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Wait for IPC socket to become available
        for _ in range(50):
            if os.path.exists(SOCKET_PATH):
                break
            time.sleep(0.1)
        else:
            logger.warning("mpv IPC socket not ready after 5s")

    def _kill_mpv(self) -> None:
        """Terminate the mpv process."""
        if not self._process:
            return
        if self._paused:
            try:
                os.kill(self._process.pid, sig.SIGCONT)
            except OSError:
                pass
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except (ProcessLookupError, OSError):
            pass
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._process = None
        self._paused = False

    def _restart_mpv(self) -> None:
        """Kill and restart mpv, then play the next track."""
        logger.info("Restarting radio mpv for clean state")
        self._kill_mpv()
        if self._playing:
            self._start_mpv()
            self._play_next()

    def _freeze(self) -> None:
        """Freeze mpv process with SIGSTOP."""
        if self._process and self._process.poll() is None and not self._paused:
            os.kill(self._process.pid, sig.SIGSTOP)
            self._paused = True
            logger.debug("mpv frozen (SIGSTOP)")

    def _unfreeze(self) -> None:
        """Resume mpv process with SIGCONT."""
        if self._process and self._process.poll() is None and self._paused:
            os.kill(self._process.pid, sig.SIGCONT)
            self._paused = False
            logger.debug("mpv resumed (SIGCONT)")

    # ── mpv IPC ──────────────────────────────────────────────────

    def _send_command(self, command: list) -> dict | None:
        """Send a JSON command to mpv via its IPC socket."""
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

    # ── Audible fallback ─────────────────────────────────────────

    def _is_emitting(self) -> bool:
        """True when mpv is actually producing audio right now.

        Uses mpv's ``core-idle`` property, which is true whenever playback is
        idle, buffering, or at EOF — i.e. no sound is coming out. If mpv can't
        be reached, assume it's silent.
        """
        resp = self._send_command(["get_property", "core-idle"])
        if not resp or resp.get("error") != "success":
            return False
        return not bool(resp.get("data", True))

    def _enter_fallback(self) -> None:
        """Radio went silent — hand the audible slot to the background player."""
        logger.warning(
            "Radio produced no audio for %ds — falling back to background",
            int(self._idle_secs),
        )
        self._audible = False
        self._idle_secs = 0.0
        self._stable_secs = 0.0
        self._set_volume(0)  # mute so recovery buffering stays inaudible
        if self._background:
            self._background.radio_active = False
            if (not self._background.adhan_active
                    and not self._background.bluetooth_active
                    and not self._background.quiet_active):
                self._background._restart_mpv()
                logger.info("Background audio resumed as radio fallback")
        # Refresh the catalog in the background so the muted radio probes with
        # fresh (non-expired) URLs — a common cause of mid-window stalls.
        threading.Thread(target=self._refresh_and_probe, daemon=True).start()

    def _refresh_and_probe(self) -> None:
        """Refresh the catalog and load a fresh track for the muted radio."""
        self._refresh_catalog()
        if not self._audible and self._playing:
            with self._lock:
                self._build_queue()
            self._play_next()

    def _exit_fallback(self) -> None:
        """Radio is playing steadily again — reclaim the audible slot."""
        logger.info("Radio playing steadily again — reclaiming from background")
        self._audible = True
        self._idle_secs = 0.0
        self._stable_secs = 0.0
        if self._background and self._background.radio_active is False:
            self._background.radio_active = True
            self._background._freeze()
            logger.info("Background audio frozen — radio reclaimed")
        # The current track is already playing (just muted); fade it up.
        step_delay = self.fade_duration / FADE_STEPS
        volume_step = self.volume / FADE_STEPS
        for i in range(FADE_STEPS):
            self._set_volume(min(self.volume, int(volume_step * (i + 1))))
            time.sleep(step_delay)

    # ── mpv event listener ───────────────────────────────────────

    def _event_listener_loop(self) -> None:
        """Persistent thread that listens for mpv IPC events.

        Auto-reconnects when mpv restarts.  Advances to the next track
        when the current one finishes (end-file with reason eof or error).
        """
        while not self._stop_event.is_set():
            # Wait until we're playing and the socket exists
            if not self._playing or not os.path.exists(SOCKET_PATH):
                self._stop_event.wait(1)
                continue

            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect(SOCKET_PATH)
                buffer = ""

                while not self._stop_event.is_set():
                    try:
                        data = sock.recv(4096).decode()
                        if not data:
                            break  # Socket closed (mpv died or restarted)
                        buffer += data
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                msg = json.loads(line)
                                self._handle_mpv_event(msg)
                            except json.JSONDecodeError:
                                pass
                    except socket.timeout:
                        continue

                sock.close()
            except (socket.error, OSError):
                self._stop_event.wait(1)

    def _handle_mpv_event(self, event: dict) -> None:
        """React to mpv IPC events."""
        if event.get("event") != "end-file":
            return

        reason = event.get("reason", "")
        if reason == "eof":
            logger.debug("Track ended naturally — advancing")
            self._play_next()
        elif reason == "error":
            track_title = ""
            if self._current_track:
                track_title = self._current_track.get("title", "")
            logger.warning("Track playback error (%s) — skipping", track_title)
            self._play_next()

    # ── Watchdog ─────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """Background loop that manages window transitions, crash recovery,
        catalog refreshes, and login retries.
        """
        was_in_window = self._playing

        while not self._stop_event.is_set():
            self._stop_event.wait(WATCHDOG_INTERVAL)
            if self._stop_event.is_set():
                break

            try:
                in_window = self._is_in_window()

                # ── Window transitions ───────────────────────
                if in_window and not was_in_window:
                    logger.info("Entering play window")
                    if not self._catalog:
                        self._try_login_and_fetch()
                    # Don't start over Bluetooth or an adhan — the mid-window
                    # recovery branch below starts the radio once that clears.
                    if self._catalog and not self._bt_or_adhan_active():
                        if self._background:
                            self._background.radio_active = True
                            self._background._freeze()
                            logger.info("Background audio frozen for radio window")
                        self._start_playing()
                    else:
                        logger.info(
                            "Radio start deferred (bluetooth/adhan active or no catalog)"
                        )

                elif not in_window and was_in_window:
                    logger.info("Leaving play window — stopping radio")
                    self._stop_playing()
                    if self._background:
                        self._background.radio_active = False
                        if (not self._background.adhan_active
                                and not self._background.bluetooth_active
                                and not self._background.quiet_active):
                            self._background._restart_mpv()
                            logger.info("Background audio resumed after radio window")

                # ── Recovery: catalog arrived after the window began ──
                # At window entry the catalog can be empty (e.g. the network
                # is still down after a power outage). The entry branch above
                # only fires on transition, so once the catalog repopulates we
                # must start streaming here or the radio stays idle all day.
                elif in_window and self._catalog and not self._playing:
                    bt = self._background.bluetooth_active if self._background else self.bluetooth_active
                    ad = self._background.adhan_active if self._background else self.adhan_active
                    if not bt and not ad:
                        logger.info("Catalog recovered mid-window — starting radio")
                        if self._background and not self._background.radio_active:
                            self._background.radio_active = True
                            self._background._freeze()
                            logger.info("Background audio frozen for radio window")
                        self._start_playing()

                was_in_window = in_window

                # ── Crash recovery ───────────────────────────
                if (
                    self._playing
                    and self._process
                    and self._process.poll() is not None
                    and not self._paused
                ):
                    logger.warning(
                        "Radio mpv died (exit %d), restarting...",
                        self._process.returncode,
                    )
                    self._restart_mpv()

                # ── Audible fallback ─────────────────────────
                # Background fills in whenever the radio isn't actually making
                # sound (stall, empty queue, dead stream) and steps back once
                # the radio plays steadily again. Skipped during adhan/Bluetooth
                # pauses, which are intentional silences handled elsewhere.
                bt_active = self.bluetooth_active or (
                    self._background is not None and self._background.bluetooth_active)
                ad_active = self.adhan_active or (
                    self._background is not None and self._background.adhan_active)
                if self._playing and not self._paused and not bt_active and not ad_active:
                    if self._audible:
                        if self._is_emitting():
                            self._idle_secs = 0.0
                        else:
                            self._idle_secs += WATCHDOG_INTERVAL
                            if self._idle_secs >= SILENCE_FALLBACK_SECS:
                                self._enter_fallback()
                    else:
                        if self._is_emitting():
                            self._stable_secs += WATCHDOG_INTERVAL
                            if self._stable_secs >= RESUME_STABLE_SECS:
                                self._exit_fallback()
                        else:
                            self._stable_secs = 0.0

                # ── Catalog refresh (daily) ──────────────────
                if self._should_refresh_catalog() and self.api_client.access_token:
                    logger.info("Refreshing track catalog (periodic)")
                    self._refresh_catalog()
                    # Rebuild queue with fresh presigned URLs
                    if self._playing:
                        with self._lock:
                            self._build_queue()

                # ── Login retry (if never authenticated) ─────
                if not self.api_client.access_token and not self._catalog:
                    self._try_login_and_fetch()
                    if self._catalog and in_window and not self._playing:
                        self._start_playing()

            except Exception:
                logger.exception("Error in radio watchdog loop")

    def _try_login_and_fetch(self) -> None:
        """Attempt to login and fetch catalog. Silent on failure."""
        if self.api_client.login():
            self._refresh_catalog()
