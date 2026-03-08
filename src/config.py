"""YAML configuration loading with typed dataclasses and validation."""

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import yaml


PRAYER_NAMES = ["fajr", "dhuhr", "asr", "maghrib", "isha"]

VALID_METHODS = {
    "NORTH_AMERICA",
    "MUSLIM_WORLD_LEAGUE",
    "EGYPTIAN",
    "KARACHI",
    "UMM_AL_QURA",
    "DUBAI",
    "MOON_SIGHTING_COMMITTEE",
    "KUWAIT",
    "QATAR",
    "SINGAPORE",
}


@dataclass
class LocationConfig:
    latitude: float
    longitude: float
    timezone: str

    def __post_init__(self):
        if not (-90 <= self.latitude <= 90):
            raise ValueError(f"Latitude must be between -90 and 90, got {self.latitude}")
        if not (-180 <= self.longitude <= 180):
            raise ValueError(f"Longitude must be between -180 and 180, got {self.longitude}")
        # Validate timezone
        ZoneInfo(self.timezone)


@dataclass
class CalculationConfig:
    method: str = "NORTH_AMERICA"

    def __post_init__(self):
        self.method = self.method.upper()
        if self.method not in VALID_METHODS:
            raise ValueError(
                f"Unknown calculation method '{self.method}'. "
                f"Valid: {sorted(VALID_METHODS)}"
            )


@dataclass
class AudioConfig:
    default_file: str = "audio/adhan_default.mp3"
    per_prayer: dict[str, str] = field(default_factory=dict)
    volume: int = 100

    def __post_init__(self):
        if not (0 <= self.volume <= 100):
            raise ValueError(f"Volume must be 0-100, got {self.volume}")
        for prayer in self.per_prayer:
            if prayer not in PRAYER_NAMES:
                raise ValueError(
                    f"Unknown prayer '{prayer}' in per_prayer. "
                    f"Valid: {PRAYER_NAMES}"
                )


@dataclass
class PrayersConfig:
    disabled: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.disabled = [p.lower() for p in self.disabled]
        for prayer in self.disabled:
            if prayer not in PRAYER_NAMES:
                raise ValueError(
                    f"Unknown prayer '{prayer}' in disabled list. "
                    f"Valid: {PRAYER_NAMES}"
                )


VALID_DAYS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}


@dataclass
class QuietHoursConfig:
    days: list[str] = field(default_factory=list)
    start: str = "00:00"
    end: str = "00:00"

    def __post_init__(self):
        self.days = [d.lower() for d in self.days]
        for day in self.days:
            if day not in VALID_DAYS:
                raise ValueError(f"Unknown day '{day}'. Valid: {sorted(VALID_DAYS)}")
        # Validate time formats
        for t in (self.start, self.end):
            parts = t.split(":")
            if len(parts) != 2:
                raise ValueError(f"Quiet hours time must be HH:MM, got '{t}'")
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError(f"Invalid quiet hours time: {t}")


@dataclass
class BackgroundConfig:
    enabled: bool = False
    file: str = ""
    volume: int = 30
    fade_duration: float = 3.0
    quiet_hours: list[QuietHoursConfig] = field(default_factory=list)

    def __post_init__(self):
        if not (0 <= self.volume <= 100):
            raise ValueError(f"Background volume must be 0-100, got {self.volume}")
        if self.fade_duration < 0:
            raise ValueError(f"Fade duration must be >= 0, got {self.fade_duration}")


@dataclass
class SchedulerConfig:
    daily_recalc_time: str = "00:05"
    misfire_grace_seconds: int = 300

    def __post_init__(self):
        # Validate time format
        parts = self.daily_recalc_time.split(":")
        if len(parts) != 2:
            raise ValueError(f"daily_recalc_time must be HH:MM, got '{self.daily_recalc_time}'")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"Invalid time: {self.daily_recalc_time}")


@dataclass
class LoggingConfig:
    file: str = "logs/adhan.log"
    max_bytes: int = 5_242_880
    backup_count: int = 3
    level: str = "INFO"


@dataclass
class AppConfig:
    location: LocationConfig
    calculation: CalculationConfig
    audio: AudioConfig
    prayers: PrayersConfig
    background: BackgroundConfig
    scheduler: SchedulerConfig
    logging: LoggingConfig
    base_dir: str = ""


def load_config(config_path: str) -> AppConfig:
    """Load and validate configuration from a YAML file.

    Args:
        config_path: Path to config.yaml.

    Returns:
        Validated AppConfig instance.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If config values are invalid.
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML mapping")

    base_dir = os.path.dirname(os.path.abspath(config_path))

    loc_raw = raw.get("location", {})
    location = LocationConfig(
        latitude=float(loc_raw["latitude"]),
        longitude=float(loc_raw["longitude"]),
        timezone=loc_raw["timezone"],
    )

    calc_raw = raw.get("calculation", {})
    calculation = CalculationConfig(method=calc_raw.get("method", "NORTH_AMERICA"))

    audio_raw = raw.get("audio", {})
    audio = AudioConfig(
        default_file=audio_raw.get("default_file", "audio/adhan_default.mp3"),
        per_prayer=audio_raw.get("per_prayer", {}),
        volume=int(audio_raw.get("volume", 100)),
    )

    prayers_raw = raw.get("prayers", {})
    prayers = PrayersConfig(
        disabled=prayers_raw.get("disabled", []),
    )

    bg_raw = raw.get("background", {})
    quiet_hours = []
    for qh in bg_raw.get("quiet_hours", []):
        quiet_hours.append(QuietHoursConfig(
            days=qh.get("days", []),
            start=qh.get("start", "00:00"),
            end=qh.get("end", "00:00"),
        ))
    background = BackgroundConfig(
        enabled=bool(bg_raw.get("enabled", False)),
        file=bg_raw.get("file", ""),
        volume=int(bg_raw.get("volume", 30)),
        fade_duration=float(bg_raw.get("fade_duration", 3.0)),
        quiet_hours=quiet_hours,
    )

    sched_raw = raw.get("scheduler", {})
    scheduler = SchedulerConfig(
        daily_recalc_time=sched_raw.get("daily_recalc_time", "00:05"),
        misfire_grace_seconds=int(sched_raw.get("misfire_grace_seconds", 300)),
    )

    log_raw = raw.get("logging", {})
    logging_cfg = LoggingConfig(
        file=log_raw.get("file", "logs/adhan.log"),
        max_bytes=int(log_raw.get("max_bytes", 5_242_880)),
        backup_count=int(log_raw.get("backup_count", 3)),
        level=log_raw.get("level", "INFO"),
    )

    return AppConfig(
        location=location,
        calculation=calculation,
        audio=audio,
        prayers=prayers,
        background=background,
        scheduler=scheduler,
        logging=logging_cfg,
        base_dir=base_dir,
    )


def validate_audio_files(config: AppConfig) -> list[str]:
    """Check that configured audio files exist. Returns list of missing files."""
    missing = []
    default_path = os.path.join(config.base_dir, config.audio.default_file)
    if not os.path.isfile(default_path):
        missing.append(config.audio.default_file)

    for prayer, path in config.audio.per_prayer.items():
        full_path = os.path.join(config.base_dir, path)
        if not os.path.isfile(full_path):
            missing.append(f"{prayer}: {path}")

    return missing
