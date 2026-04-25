#!/usr/bin/env python3
"""Test the MMR radio integration step by step.

Usage:
    # Stage 1: Test API connection only (no mpv needed)
    python scripts/test_radio.py --api-only

    # Stage 2: Test API + play one track through mpv
    python scripts/test_radio.py

    # Stage 3: Test full radio loop for N seconds
    python scripts/test_radio.py --duration 60

You can pass credentials via flags or be prompted interactively:
    python scripts/test_radio.py --api-url https://... --email user@example.com --password secret
"""

import argparse
import getpass
import json
import os
import signal
import socket
import subprocess
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api_client import MMRApiClient


def test_api(client: MMRApiClient) -> list[dict]:
    """Stage 1: Login and fetch catalog."""
    print("\n── Stage 1: API Connection ─────────────────────────")

    print(f"  API URL:  {client.api_url}")
    print(f"  Email:    {client.email}")
    print("  Logging in...", end=" ", flush=True)

    if not client.login():
        print("FAILED")
        print("  Could not authenticate. Check your credentials and API URL.")
        sys.exit(1)
    print("OK")

    print("  Fetching track catalog...", end=" ", flush=True)
    tracks = client.fetch_all_tracks()
    print(f"OK — {len(tracks)} playable tracks")

    if not tracks:
        print("  No playable tracks found (all tracks missing mediaUrl?).")
        sys.exit(1)

    # Show a sample
    print("\n  Sample tracks:")
    for t in tracks[:5]:
        artist = (t.get("artistRelation") or {}).get("name", "Unknown")
        dur = t.get("duration", 0)
        mins, secs = divmod(dur, 60)
        print(f"    • {artist} — {t['title']} ({mins}:{secs:02d})")
    if len(tracks) > 5:
        print(f"    ... and {len(tracks) - 5} more")

    return tracks


def test_playback(tracks: list[dict]) -> None:
    """Stage 2: Play one track through mpv."""
    print("\n── Stage 2: Audio Playback ─────────────────────────")

    # Check mpv is installed
    try:
        result = subprocess.run(
            ["mpv", "--version"], capture_output=True, text=True, timeout=5,
        )
        version = result.stdout.split("\n")[0] if result.stdout else "unknown"
        print(f"  mpv: {version}")
    except FileNotFoundError:
        print("  mpv not found! Install it:")
        print("    macOS:  brew install mpv")
        print("    Linux:  sudo apt install mpv")
        sys.exit(1)

    track = tracks[0]
    artist = (track.get("artistRelation") or {}).get("name", "Unknown")
    print(f"  Playing: {artist} — {track['title']}")
    print(f"  URL: {track['mediaUrl'][:80]}...")
    print("  (Press Ctrl+C to stop)\n")

    try:
        proc = subprocess.run(
            [
                "mpv",
                "--no-video",
                "--really-quiet",
                "--volume=50",
                "--end=30",  # Play only first 30 seconds
                track["mediaUrl"],
            ],
            timeout=60,
        )
        if proc.returncode == 0:
            print("\n  Playback OK!")
        else:
            print(f"\n  mpv exited with code {proc.returncode}")
    except subprocess.TimeoutExpired:
        print("\n  Playback timed out (60s)")
    except KeyboardInterrupt:
        print("\n  Stopped by user")


def test_radio_loop(client: MMRApiClient, tracks: list[dict], duration: int) -> None:
    """Stage 3: Run the full RadioPlayer for a limited time."""
    print(f"\n── Stage 3: Radio Loop ({duration}s) ────────────────────")

    from src.config import (
        AppConfig, AudioConfig, BackgroundConfig, CalculationConfig,
        LocationConfig, LoggingConfig, PrayersConfig, RadioConfig,
        RadioScheduleEntry, SchedulerConfig, VALID_DAYS,
    )
    from src.logger import setup_logging
    from src.radio import RadioPlayer

    # Build a config with a play window that's always active
    radio_cfg = RadioConfig(
        enabled=True,
        api_url=client.api_url,
        email=client.email,
        password=client.password,
        volume=50,
        fade_duration=1.0,
        schedule=[RadioScheduleEntry(
            days=list(VALID_DAYS),
            start="00:00",
            end="23:59",
        )],
        shuffle=True,
    )
    config = AppConfig(
        location=LocationConfig(34.3078, -118.4467, "America/Los_Angeles"),
        calculation=CalculationConfig(),
        audio=AudioConfig(),
        prayers=PrayersConfig(),
        background=BackgroundConfig(),
        scheduler=SchedulerConfig(),
        logging=LoggingConfig(level="DEBUG"),
        radio=radio_cfg,
        base_dir=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    setup_logging(level="DEBUG", base_dir=config.base_dir)

    print("  Starting RadioPlayer...")
    print("  (Press Ctrl+C to stop early)\n")

    player = RadioPlayer(config)
    player.start()

    try:
        time.sleep(duration)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n  Stopping RadioPlayer...")
        player.stop()
        print("  Done!")


def main():
    parser = argparse.ArgumentParser(description="Test MMR radio integration")
    parser.add_argument("--api-url", help="MMR GraphQL API endpoint")
    parser.add_argument("--email", help="MMR account email")
    parser.add_argument("--password", help="MMR account password")
    parser.add_argument(
        "--api-only", action="store_true",
        help="Only test API connection (no mpv needed)",
    )
    parser.add_argument(
        "--duration", type=int, default=0,
        help="Run full radio loop for N seconds (stage 3)",
    )
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════╗")
    print("║          MMR Radio Integration Test             ║")
    print("╚══════════════════════════════════════════════════╝")

    # Gather credentials (email/password are optional — API may allow anonymous access)
    api_url = args.api_url or input("\n  MMR API URL: ").strip()
    email = args.email if args.email is not None else input("  Email (enter to skip): ").strip()
    password = args.password if args.password is not None else getpass.getpass("  Password (enter to skip): ")

    client = MMRApiClient(api_url=api_url, email=email, password=password)

    # Stage 1: API
    tracks = test_api(client)

    if args.api_only:
        print("\n  --api-only: skipping playback tests")
        print("  All API checks passed!")
        return

    # Stage 2: Single track playback
    test_playback(tracks)

    # Stage 3: Full radio loop (optional)
    if args.duration > 0:
        test_radio_loop(client, tracks, args.duration)

    print("\n  All tests passed!")


if __name__ == "__main__":
    main()
