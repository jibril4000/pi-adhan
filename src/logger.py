"""Rotating file + console logging setup."""

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(
    log_file: str = "logs/adhan.log",
    max_bytes: int = 5_242_880,
    backup_count: int = 3,
    level: str = "INFO",
    base_dir: str = "",
) -> logging.Logger:
    """Configure rotating file + console logging.

    Args:
        log_file: Path to log file (relative to base_dir).
        max_bytes: Max size per log file before rotation.
        backup_count: Number of rotated backup files to keep.
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        base_dir: Project root directory for resolving relative paths.

    Returns:
        Configured root logger.
    """
    logger = logging.getLogger("adhan")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handler
    if log_file:
        log_path = os.path.join(base_dir, log_file) if base_dir else log_file
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
