#!/usr/bin/env python3
"""External health checker for the adhan service.

Run periodically by a systemd timer (adhan-healthcheck.timer), INDEPENDENT of
the main daemon — so it can still email even when the daemon is dead or wedged,
which is exactly the failure we care about. Checks:

  * service down / crash-looping (systemctl state + NRestarts delta)
  * adhan failed to play (recent audio-failure ERROR lines in the log)
  * error threshold (too many ERROR lines in a short window)

Debounced via a small JSON state file: one email when a problem appears, one
"all clear" when it resolves, plus an optional daily "all healthy" heartbeat.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.alerter import send_email
from src.config import load_config

SERVICE = "adhan"
CRASH_LOOP_DELTA = 3  # NRestarts climbing by this much between runs == looping

AUDIO_FAILURE_MARKERS = (
    "mpv exited with code",
    "Audio file not found",
    "mpv playback timed out",
    "mpv not found",
)

# Human-readable label for each condition key.
LABELS = {
    "service_down": "Adhan service is DOWN or crash-looping",
    "adhan_failed": "Adhan failed to play",
    "error_threshold": "Error threshold exceeded",
}


def service_state() -> dict:
    """Return ActiveState, SubState, NRestarts for the adhan unit."""
    try:
        out = subprocess.run(
            ["systemctl", "show", SERVICE,
             "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return {}
    state = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            state[k] = v
    return state


def recent_error_lines(log_path: str, window_minutes: int) -> list[str]:
    """ERROR log lines with a timestamp within the window. Empty if no log."""
    if not os.path.isfile(log_path):
        return []
    cutoff = datetime.now() - timedelta(minutes=window_minutes)
    hits = []
    try:
        with open(log_path, errors="replace") as f:
            for line in f:
                if "[ERROR]" not in line:
                    continue
                try:
                    ts = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if ts >= cutoff:
                    hits.append(line.rstrip())
    except OSError:
        return []
    return hits


def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError as e:
        print(f"healthcheck: could not write state: {e}", file=sys.stderr)


def main() -> int:
    base_dir = "/opt/adhan"
    config_path = os.path.join(base_dir, "config.yaml")
    if not os.path.exists(config_path):
        # Fall back to repo-relative (for local testing).
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"
        )
    cfg = load_config(config_path)
    base_dir = cfg.base_dir
    alerts = cfg.alerts

    if not alerts.enabled:
        return 0

    state_path = os.path.join(base_dir, "logs", "alert_state.json")
    state = load_state(state_path)
    prev_active = set(state.get("active_alerts", []))
    prev_restarts = state.get("last_restarts")

    host = socket.gethostname()
    now = datetime.now()

    # --- Evaluate conditions ---
    current = {}  # key -> detail string

    svc = service_state()
    active_state = svc.get("ActiveState", "unknown")
    sub_state = svc.get("SubState", "unknown")
    try:
        restarts = int(svc.get("NRestarts", "0"))
    except ValueError:
        restarts = 0

    crash_looping = (
        prev_restarts is not None and restarts - prev_restarts >= CRASH_LOOP_DELTA
    )
    # "activating" is a normal transient during a restart — don't alarm on it.
    down = active_state not in ("active", "activating")
    if down or crash_looping:
        reason = (f"crash-looping ({restarts - (prev_restarts or 0)} restarts "
                  f"since last check)" if crash_looping
                  else f"state={active_state}/{sub_state}")
        current["service_down"] = reason

    log_path = os.path.join(base_dir, cfg.logging.file)
    errors = recent_error_lines(log_path, alerts.error_window_minutes)
    audio_fails = [ln for ln in errors if any(m in ln for m in AUDIO_FAILURE_MARKERS)]
    if audio_fails:
        current["adhan_failed"] = "\n".join(audio_fails[-5:])
    if len(errors) >= alerts.error_threshold:
        current["error_threshold"] = (
            f"{len(errors)} ERROR lines in the last "
            f"{alerts.error_window_minutes} min:\n" + "\n".join(errors[-8:])
        )

    current_keys = set(current)

    def notify(subject: str, body: str) -> None:
        ok, err = send_email(
            alerts.resend_api_key, alerts.email_from, alerts.email_to,
            subject, body,
        )
        if not ok:
            print(f"healthcheck: email failed: {err}", file=sys.stderr)

    # --- New problems ---
    for key in current_keys - prev_active:
        notify(
            f"[Adhan Pi] ALERT: {LABELS[key]} ({host})",
            f"{LABELS[key]} on {host} at {now:%Y-%m-%d %H:%M:%S}.\n\n"
            f"Details:\n{current[key]}\n\n"
            f"Service: {active_state}/{sub_state}, restarts={restarts}.",
        )

    # --- Resolved problems ---
    for key in prev_active - current_keys:
        notify(
            f"[Adhan Pi] RESOLVED: {LABELS[key]} ({host})",
            f"{LABELS[key]} has cleared on {host} at {now:%Y-%m-%d %H:%M:%S}.\n"
            f"Service is now {active_state}/{sub_state}.",
        )

    # --- Daily heartbeat ---
    heartbeat_date = state.get("last_heartbeat_date")
    today = now.strftime("%Y-%m-%d")
    if (alerts.heartbeat and heartbeat_date != today
            and now.hour >= alerts.heartbeat_hour):
        if not current_keys:
            notify(
                f"[Adhan Pi] Daily check: all healthy ({host})",
                f"Adhan service on {host} is healthy at {now:%Y-%m-%d %H:%M:%S}.\n"
                f"Service: {active_state}/{sub_state}, restarts={restarts}.\n"
                f"No errors in the last {alerts.error_window_minutes} min.",
            )
        state["last_heartbeat_date"] = today

    state["active_alerts"] = sorted(current_keys)
    state["last_restarts"] = restarts
    save_state(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
