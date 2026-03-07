#!/usr/bin/env python3
"""Test audio output through mpv. Run from project root."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.player import AdhanPlayer


def main():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config.yaml",
    )
    config = load_config(config_path)
    player = AdhanPlayer(config)

    prayer = sys.argv[1] if len(sys.argv) > 1 else "fajr"
    print(f"Testing audio playback for: {prayer}")
    print("You should hear the adhan through the audio output...")

    success = player.play_adhan(prayer)
    if success:
        print("Playback completed successfully!")
    else:
        print("Playback failed. Check logs and audio configuration.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
