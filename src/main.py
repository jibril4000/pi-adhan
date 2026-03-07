"""Entry point: load config, wire modules, run daemon with signal handling."""

import os
import signal
import sys
import threading

from src.config import load_config, validate_audio_files
from src.logger import setup_logging
from src.player import AdhanPlayer
from src.scheduler import AdhanScheduler


def main():
    # Resolve config path
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, "config.yaml")

    if not os.path.exists(config_path):
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        print("Copy config.example.yaml to config.yaml and edit it.", file=sys.stderr)
        sys.exit(1)

    # Load config
    config = load_config(config_path)

    # Set up logging
    logger = setup_logging(
        log_file=config.logging.file,
        max_bytes=config.logging.max_bytes,
        backup_count=config.logging.backup_count,
        level=config.logging.level,
        base_dir=config.base_dir,
    )
    logger.info("Adhan playback system starting")
    logger.info(
        "Location: %.4f, %.4f (%s)",
        config.location.latitude,
        config.location.longitude,
        config.location.timezone,
    )
    logger.info("Calculation method: %s", config.calculation.method)

    # Validate audio files (warn but don't exit — files might be added later)
    missing = validate_audio_files(config)
    if missing:
        logger.warning("Missing audio files: %s", ", ".join(missing))
        logger.warning("Place MP3 files in the audio/ directory before prayer time")

    # Initialize components
    player = AdhanPlayer(config)
    scheduler = AdhanScheduler(config, player)

    # Signal handling for clean shutdown
    shutdown_event = threading.Event()

    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down...", sig_name)
        scheduler.shutdown()
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Start scheduler
    scheduler.start()
    logger.info("Adhan system running. Press Ctrl+C to stop.")

    # Block until shutdown signal
    shutdown_event.wait()
    logger.info("Adhan playback system stopped")


if __name__ == "__main__":
    main()
