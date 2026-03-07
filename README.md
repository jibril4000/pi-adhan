# Pi Adhan

A dedicated Adhan (Islamic call to prayer) playback appliance for the Raspberry Pi 4. The system automatically plays the Adhan audio at the exact times of the 5 daily prayers — Fajr, Dhuhr, Asr, Maghrib, and Isha — outputting audio via the 3.5mm jack (through a 1/4" adapter to a mixer, or any speaker).

Configured for **Sylmar, Los Angeles, CA** by default, but works anywhere — just update the coordinates in `config.yaml`.

---

## How It Works

### The Big Picture

The app runs as a **Python daemon** (long-running background process) managed by **systemd** on the Pi. Every day at 00:05, it fetches that day's 5 prayer times, then schedules an audio playback job for each one. When a prayer time arrives, it plays the Adhan MP3 through `mpv`.

```
                  ┌──────────────────────────────────────┐
                  │           main.py (daemon)           │
                  │                                      │
                  │  1. Load config.yaml                 │
                  │  2. Set up logging                   │
                  │  3. Start scheduler                  │
                  │  4. Block on shutdown signal          │
                  └───────────────┬──────────────────────┘
                                  │
                  ┌───────────────▼──────────────────────┐
                  │         scheduler.py                 │
                  │                                      │
                  │  Daily at 00:05:                     │
                  │    → Fetch today's prayer times      │
                  │    → Skip any that already passed    │
                  │    → Schedule a DateTrigger job for   │
                  │      each remaining prayer           │
                  │                                      │
                  │  At each prayer time:                │
                  │    → Call player.play_adhan("fajr")  │
                  └──────┬──────────────┬────────────────┘
                         │              │
          ┌──────────────▼──┐    ┌──────▼──────────────┐
          │ prayer_times.py │    │    player.py         │
          │                 │    │                      │
          │ Try Aladhan API │    │ Resolve audio file   │
          │ ↓ on failure    │    │ ↓                    │
          │ Fall back to    │    │ Run mpv subprocess   │
          │ adhanpy offline │    │ with 10-min timeout  │
          └─────────────────┘    └──────────────────────┘
```

### Why APScheduler Instead of Cron

Prayer times change every day. With cron, you'd need to rewrite the crontab daily (race conditions), have no misfire recovery if the Pi reboots near a prayer time, and split logic across shell scripts + Python + crontab. APScheduler handles all of this in-process:

- **`DateTrigger`** fires at an exact datetime — no polling, no cron expressions.
- **`misfire_grace_time=300`** — if the Pi reboots and comes back within 5 minutes of a prayer time, it still plays.
- **`CronTrigger`** runs the daily recalculation at 00:05 automatically.

---

## Project Structure

```
pi-adhan/
├── config.yaml              # Your configuration (location, audio, etc.)
├── config.example.yaml      # Documented example with all options
├── requirements.txt         # Python dependencies
├── src/
│   ├── main.py              # Entry point — wires everything, signal handling
│   ├── config.py            # YAML → typed dataclasses, validation
│   ├── prayer_times.py      # Aladhan API + adhanpy offline fallback
│   ├── scheduler.py         # APScheduler wrapper, daily recalc + per-prayer jobs
│   ├── player.py            # mpv subprocess playback, per-prayer audio selection
│   └── logger.py            # Rotating file + console logging
├── audio/                   # Adhan MP3 files (you supply these)
│   ├── adhan_default.mp3    # Used for Dhuhr, Asr, Maghrib, Isha
│   └── adhan_fajr.mp3       # Optional separate Fajr adhan
├── setup/
│   ├── install.sh           # One-command Pi setup
│   └── adhan.service        # systemd unit file
├── scripts/
│   ├── check_times.py       # Print today's prayer times (debug)
│   └── test_audio.py        # Test audio output through mpv
└── tests/
    ├── test_config.py       # 17 tests
    ├── test_prayer_times.py # 10 tests
    ├── test_scheduler.py    # 4 tests
    └── test_player.py       # 8 tests
```

---

## Module-by-Module Breakdown

### `src/config.py` — Configuration

Loads `config.yaml` into typed Python dataclasses with validation at startup:

- **`LocationConfig`** — latitude (-90 to 90), longitude (-180 to 180), IANA timezone (validated via `ZoneInfo`)
- **`CalculationConfig`** — prayer calculation method name, validated against supported methods
- **`AudioConfig`** — default audio file path, optional per-prayer overrides, volume (0-100)
- **`SchedulerConfig`** — daily recalculation time (HH:MM), misfire grace seconds
- **`LoggingConfig`** — log file path, rotation settings, log level

If anything is invalid (bad coordinates, unknown method, volume out of range), it raises a `ValueError` immediately — fail fast, don't discover problems at 5 AM.

`validate_audio_files()` checks that configured MP3 files actually exist on disk and returns a list of any missing ones.

### `src/prayer_times.py` — Two-Tier Prayer Time Fetching

This is the core calculation module. It has two strategies:

**1. Online — Aladhan API (primary)**
```
GET https://api.aladhan.com/v1/timings/{DD-MM-YYYY}?latitude=X&longitude=Y&method=2
```
- Free, no auth required
- Returns prayer times as `HH:MM` strings (sometimes with timezone abbreviation like `"05:30 (PST)"` — the code strips that)
- 10-second request timeout

**2. Offline — `adhanpy` library (fallback)**
- Pure Python prayer time calculation using astronomical formulas
- Zero network dependency
- Uses the same calculation method angles as the API
- Activated automatically if the API request fails for any reason

Both return the same thing: `dict[str, datetime]` mapping `"fajr"` through `"isha"` to timezone-aware datetimes.

The method mapping works like this:
- `"NORTH_AMERICA"` → Aladhan API method `2` (ISNA: 15° Fajr, 15° Isha)
- `"NORTH_AMERICA"` → adhanpy `CalculationMethod.NORTH_AMERICA`
- Other methods (MWL, Egyptian, Karachi, etc.) have their own mappings

### `src/scheduler.py` — APScheduler Wrapper

The `AdhanScheduler` class manages two types of scheduled jobs:

**Daily recalculation job** (CronTrigger at 00:05):
1. Remove all existing `prayer_*` jobs
2. Fetch today's prayer times (API → offline fallback)
3. Compare each prayer time to `now` — skip any that already passed
4. Add a `DateTrigger` job for each remaining prayer

**Per-prayer jobs** (DateTrigger at exact prayer time):
- Calls `player.play_adhan("fajr")` (or whichever prayer)
- `misfire_grace_time=300` — if the scheduler was down at the scheduled time but comes back within 5 minutes, the job still fires
- `replace_existing=True` — idempotent, no duplicate jobs

On startup, it runs the recalculation immediately (not just at 00:05), so if you start the service at 2 PM, it schedules Asr, Maghrib, and Isha right away.

### `src/player.py` — Audio Playback

The `AdhanPlayer` class handles audio file selection and playback:

**File resolution** (`_resolve_audio_path`):
1. Check if `config.audio.per_prayer` has an override for this prayer name
2. If the override file exists → use it
3. If the override file is missing → log a warning, fall back to default
4. If no override configured → use `config.audio.default_file`

This means you can have a different Adhan for Fajr (traditional practice) while using the same one for the other four prayers.

**Playback** (`play_adhan`):
```
mpv --no-video --really-quiet --volume=100 /path/to/adhan.mp3
```
- `--no-video` — don't try to open a video window (headless Pi)
- `--really-quiet` — suppress all console output
- `--volume=100` — keep digital volume at max; control actual level at the mixer
- 10-minute `subprocess.run()` timeout as a safety net against mpv hanging
- Returns `True`/`False` so the caller knows if playback succeeded

### `src/logger.py` — Logging

Sets up dual output:
- **Console** (stdout) — for `journalctl` when running under systemd
- **Rotating file** (`logs/adhan.log`) — 5 MB max per file, 3 backups

Format: `2026-02-26 05:18:00 [INFO] adhan.scheduler: Scheduled Fajr at 05:18:00 PST`

### `src/main.py` — Entry Point

The glue that wires everything together:

1. Finds `config.yaml` relative to the project root
2. Loads and validates config
3. Sets up logging
4. Warns about missing audio files (but doesn't exit — you might add them later)
5. Creates `AdhanPlayer` and `AdhanScheduler`
6. Registers `SIGTERM`/`SIGINT` handlers for clean shutdown
7. Starts the scheduler
8. Blocks on `threading.Event.wait()` until a shutdown signal arrives

This design means `systemctl stop adhan` sends SIGTERM, which gracefully shuts down the scheduler and exits cleanly.

---

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit it:

```yaml
location:
  latitude: 34.3078          # Your GPS latitude
  longitude: -118.4467       # Your GPS longitude
  timezone: "America/Los_Angeles"  # Your IANA timezone

calculation:
  method: "NORTH_AMERICA"    # ISNA angles (Fajr 15°, Isha 15°)

audio:
  default_file: "audio/adhan_default.mp3"
  per_prayer:
    fajr: "audio/adhan_fajr.mp3"  # Different adhan for Fajr
  volume: 100                     # Keep at 100, control at mixer

scheduler:
  daily_recalc_time: "00:05"      # When to recalculate daily
  misfire_grace_seconds: 300      # Play if missed by up to 5 min

logging:
  file: "logs/adhan.log"
  max_bytes: 5242880              # 5 MB per log file
  backup_count: 3
  level: "INFO"                   # DEBUG for troubleshooting
```

### Supported Calculation Methods

| Method | Description |
|---|---|
| `NORTH_AMERICA` | ISNA — Fajr 15°, Isha 15° (default) |
| `MUSLIM_WORLD_LEAGUE` | MWL — Fajr 18°, Isha 17° |
| `EGYPTIAN` | Egyptian General Authority — Fajr 19.5°, Isha 17.5° |
| `KARACHI` | University of Islamic Sciences, Karachi — Fajr 18°, Isha 18° |
| `UMM_AL_QURA` | Umm al-Qura University, Makkah |
| `DUBAI` | Dubai |
| `MOON_SIGHTING_COMMITTEE` | Moon Sighting Committee |
| `KUWAIT` | Kuwait |
| `QATAR` | Qatar |
| `SINGAPORE` | Singapore |

---

## Edge Cases Handled

| Scenario | What Happens |
|---|---|
| **WiFi is down** | Aladhan API fails → automatic fallback to offline `adhanpy` calculation. Logs a warning. |
| **Pi reboots mid-day** | On startup, fetches today's times, skips past prayers, schedules remaining ones. |
| **Reboot near prayer time** | APScheduler's `misfire_grace_time=300` plays the Adhan if missed by <5 minutes. |
| **DST transition** | All datetimes are timezone-aware via `ZoneInfo("America/Los_Angeles")`. Spring forward / fall back handled correctly. |
| **mpv hangs** | 10-minute `subprocess.run()` timeout kills the process. |
| **Isha after midnight** | `DateTrigger` accepts any future datetime — no date boundary issues. |
| **Audio file missing** | Logged as an error; playback returns `False`. The daemon keeps running for future prayers. |
| **Per-prayer audio missing** | Falls back to `default_file` with a warning. |

---

## Local Development (Mac)

No Pi required for development and testing.

```bash
# Clone and set up
cd pi-adhan
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run tests (39 tests)
pip install pytest
python -m pytest tests/ -v

# Check today's prayer times
python scripts/check_times.py

# Run the daemon locally (Ctrl+C to stop)
python -m src.main
```

You'll need `mpv` installed for actual audio playback (`brew install mpv` on Mac).

---

## Raspberry Pi Deployment

### Prerequisites

- Raspberry Pi 4 Model B
- Pi OS Lite (Bookworm 64-bit) flashed via Raspberry Pi Imager
- WiFi configured, SSH enabled, timezone set in Imager
- 3.5mm audio jack connected to speaker/mixer (via 1/4" adapter if needed)

### Quick Setup

```bash
# SSH into your Pi
ssh pi@adhan-pi.local

# Install system dependencies
sudo apt update && sudo apt install -y python3-pip python3-venv mpv

# Force audio output to 3.5mm jack
sudo raspi-config
# → System Options → Audio → Headphones

# Test audio output
speaker-test -t wav -c 2 -l 1

# Deploy the project (from your dev machine)
# Option A: rsync
rsync -avz --exclude venv --exclude __pycache__ ./ pi@adhan-pi.local:/opt/adhan/

# Option B: git clone on Pi
sudo mkdir -p /opt/adhan && sudo chown pi:pi /opt/adhan
git clone <your-repo> /opt/adhan

# Set up Python environment
cd /opt/adhan
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Place your adhan MP3 files
cp /path/to/your/adhan.mp3 audio/adhan_default.mp3
cp /path/to/your/fajr_adhan.mp3 audio/adhan_fajr.mp3  # optional

# Edit config for your location
nano config.yaml

# Install and start the systemd service
sudo cp setup/adhan.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adhan

# Watch the logs
journalctl -u adhan -f
```

Or use the automated install script:

```bash
cd /opt/adhan
bash setup/install.sh
```

### systemd Service

The service file (`setup/adhan.service`) runs the daemon with:

- **`Restart=on-failure`** — auto-restarts if the process crashes
- **`RestartSec=10`** — waits 10 seconds before restarting
- **`WantedBy=multi-user.target`** — starts on boot
- **`ProtectSystem=strict`** — security hardening, filesystem is read-only except logs
- **`NoNewPrivileges=true`** — prevents privilege escalation

```bash
# Service management
sudo systemctl start adhan      # Start
sudo systemctl stop adhan       # Stop (sends SIGTERM → clean shutdown)
sudo systemctl restart adhan    # Restart
sudo systemctl status adhan     # Check status
journalctl -u adhan -f          # Live logs
journalctl -u adhan --since today  # Today's logs
```

---

## Verification Checklist

1. **Unit tests pass**: `python -m pytest tests/ -v` — 39 tests
2. **Prayer times are accurate**: Run `python scripts/check_times.py` and compare to [aladhan.com](https://aladhan.com) for your location
3. **Audio plays**: Run `python scripts/test_audio.py` on the Pi with speakers connected
4. **Daemon runs**: Start with `python -m src.main`, check logs show scheduled times
5. **systemd works**: `sudo systemctl start adhan && journalctl -u adhan -f`
6. **Reboot recovery**: `sudo reboot`, then verify service auto-starts and schedules correctly
7. **Offline fallback**: Disconnect WiFi, restart service, verify offline calculation in logs
8. **Soak test**: Let it run for a full day, confirm all 5 Adhans play at the correct times

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `adhanpy` | >=1.0.0 | Offline prayer time calculation using astronomical formulas |
| `APScheduler` | >=3.10, <4 | In-process job scheduling with DateTrigger and misfire handling |
| `requests` | >=2.28 | HTTP client for Aladhan API |
| `PyYAML` | >=6.0 | YAML config file parsing |
| `mpv` | (system) | Audio playback — installed via `apt`, not pip |

---

## Troubleshooting

**No sound on Pi**
- Confirm audio output is set to headphones: `sudo raspi-config` → System → Audio
- Test with: `speaker-test -t wav -c 2 -l 1`
- Check mpv directly: `mpv --no-video audio/adhan_default.mp3`

**Wrong prayer times**
- Verify your coordinates and timezone in `config.yaml`
- Compare `python scripts/check_times.py` output with aladhan.com
- Try a different calculation method if your community uses different angles

**Service won't start**
- Check logs: `journalctl -u adhan --no-pager -n 50`
- Verify config: `cd /opt/adhan && venv/bin/python -c "from src.config import load_config; load_config('config.yaml')"`
- Check audio files exist: `ls -la audio/`

**API errors in logs**
- Normal if WiFi is temporarily down — the system automatically falls back to offline calculation
- Persistent errors may indicate an API outage; offline mode will keep working

---

## License

MIT
