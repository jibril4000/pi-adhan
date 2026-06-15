"""YAML configuration loading with typed dataclasses and validation."""

import os
from dataclasses import dataclass, field
from datetime import datetime
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
class RadioScheduleEntry:
    days: list[str] = field(default_factory=list)
    start: str = "07:00"
    end: str = "19:00"

    def __post_init__(self):
        self.days = [d.lower() for d in self.days]
        for day in self.days:
            if day not in VALID_DAYS:
                raise ValueError(f"Unknown day '{day}' in radio schedule. Valid: {sorted(VALID_DAYS)}")
        for t_name, t_val in [("start", self.start), ("end", self.end)]:
            parts = t_val.split(":")
            if len(parts) != 2:
                raise ValueError(f"Radio schedule {t_name} must be HH:MM, got '{t_val}'")
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError(f"Invalid radio schedule {t_name}: {t_val}")


@dataclass
class RadioConfig:
    enabled: bool = False
    api_url: str = ""
    email: str = ""
    password: str = ""
    volume: int = 50
    fade_duration: float = 3.0
    schedule: list[RadioScheduleEntry] = field(default_factory=list)
    shuffle: bool = True

    def __post_init__(self):
        if self.enabled:
            if not self.api_url:
                raise ValueError("radio.api_url is required when radio is enabled")
        if not (0 <= self.volume <= 100):
            raise ValueError(f"Radio volume must be 0-100, got {self.volume}")
        if self.fade_duration < 0:
            raise ValueError(f"Radio fade duration must be >= 0, got {self.fade_duration}")


@dataclass
class AlertConfig:
    enabled: bool = False
    resend_api_key: str = ""
    email_from: str = "live@omninine.studio"
    email_to: list[str] = field(default_factory=lambda: ["sufimeditationzawiya@gmail.com"])
    # Number of ERROR log lines within error_window_minutes that trips an alert.
    error_threshold: int = 10
    error_window_minutes: int = 15
    # Send one "all healthy" email per day at this local hour (0-23).
    heartbeat: bool = True
    heartbeat_hour: int = 9

    def __post_init__(self):
        if isinstance(self.email_to, str):
            self.email_to = [e.strip() for e in self.email_to.split(",") if e.strip()]
        if self.enabled and not self.resend_api_key:
            raise ValueError("alerts.resend_api_key is required when alerts are enabled")
        if self.enabled and not self.email_to:
            raise ValueError("alerts.email_to is required when alerts are enabled")
        if not (0 <= self.heartbeat_hour <= 23):
            raise ValueError(f"alerts.heartbeat_hour must be 0-23, got {self.heartbeat_hour}")


@dataclass
class AppConfig:
    location: LocationConfig
    calculation: CalculationConfig
    audio: AudioConfig
    prayers: PrayersConfig
    background: BackgroundConfig
    scheduler: SchedulerConfig
    logging: LoggingConfig
    radio: RadioConfig
    alerts: AlertConfig = field(default_factory=AlertConfig)
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

    radio_raw = raw.get("radio", {})
    radio_schedule = []
    for entry in radio_raw.get("schedule", []):
        radio_schedule.append(RadioScheduleEntry(
            days=entry.get("days", []),
            start=entry.get("start", "07:00"),
            end=entry.get("end", "19:00"),
        ))
    # Backward compatibility: schedule_start/schedule_end → single all-days entry
    if not radio_schedule and "schedule_start" in radio_raw:
        radio_schedule = [RadioScheduleEntry(
            days=list(VALID_DAYS),
            start=radio_raw["schedule_start"],
            end=radio_raw.get("schedule_end", "19:00"),
        )]
    radio = RadioConfig(
        enabled=bool(radio_raw.get("enabled", False)),
        api_url=radio_raw.get("api_url", ""),
        email=radio_raw.get("email", ""),
        password=radio_raw.get("password", ""),
        volume=int(radio_raw.get("volume", 50)),
        fade_duration=float(radio_raw.get("fade_duration", 3.0)),
        schedule=radio_schedule,
        shuffle=bool(radio_raw.get("shuffle", True)),
    )

    alerts_raw = raw.get("alerts", {})
    alerts = AlertConfig(
        enabled=bool(alerts_raw.get("enabled", False)),
        resend_api_key=alerts_raw.get("resend_api_key", ""),
        email_from=alerts_raw.get("email_from", "live@omninine.studio"),
        email_to=alerts_raw.get("email_to", ["sufimeditationzawiya@gmail.com"]),
        error_threshold=int(alerts_raw.get("error_threshold", 10)),
        error_window_minutes=int(alerts_raw.get("error_window_minutes", 15)),
        heartbeat=bool(alerts_raw.get("heartbeat", True)),
        heartbeat_hour=int(alerts_raw.get("heartbeat_hour", 9)),
    )

    return AppConfig(
        location=location,
        calculation=calculation,
        audio=audio,
        prayers=prayers,
        background=background,
        scheduler=scheduler,
        logging=logging_cfg,
        radio=radio,
        alerts=alerts,
        base_dir=base_dir,
    )


def is_quiet_time(quiet_hours: list[QuietHoursConfig], timezone: str) -> bool:
    """Check if the current time falls within any configured quiet hours."""
    now = datetime.now(ZoneInfo(timezone))
    day_name = now.strftime("%A").lower()
    current_minutes = now.hour * 60 + now.minute

    for qh in quiet_hours:
        if day_name not in qh.days:
            continue
        start_parts = qh.start.split(":")
        end_parts = qh.end.split(":")
        start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
        end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])

        if start_minutes <= current_minutes < end_minutes:
            return True
    return False


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
