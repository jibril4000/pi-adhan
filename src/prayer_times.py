"""Two-tier prayer time fetching: Aladhan API with adhanpy offline fallback."""

from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from adhanpy.calculation.CalculationMethod import CalculationMethod
from adhanpy.PrayerTimes import PrayerTimes

from src.config import PRAYER_NAMES

logger = logging.getLogger("adhan.prayer_times")

# Map config method names to Aladhan API method integers
_ALADHAN_METHOD_MAP = {
    "NORTH_AMERICA": 2,
    "MUSLIM_WORLD_LEAGUE": 3,
    "EGYPTIAN": 5,
    "KARACHI": 1,
    "UMM_AL_QURA": 4,
    "DUBAI": 8,
    "MOON_SIGHTING_COMMITTEE": 15,
    "KUWAIT": 9,
    "QATAR": 10,
    "SINGAPORE": 11,
    "TEHRAN": 7,
    "TURKEY": 13,
}

# Map config method names to adhanpy CalculationMethod enum
_ADHANPY_METHOD_MAP = {
    "NORTH_AMERICA": CalculationMethod.NORTH_AMERICA,
    "MUSLIM_WORLD_LEAGUE": CalculationMethod.MUSLIM_WORLD_LEAGUE,
    "EGYPTIAN": CalculationMethod.EGYPTIAN,
    "KARACHI": CalculationMethod.KARACHI,
    "UMM_AL_QURA": CalculationMethod.UMM_AL_QURA,
    "DUBAI": CalculationMethod.DUBAI,
    "MOON_SIGHTING_COMMITTEE": CalculationMethod.MOON_SIGHTING_COMMITTEE,
    "KUWAIT": CalculationMethod.KUWAIT,
    "QATAR": CalculationMethod.QATAR,
    "SINGAPORE": CalculationMethod.SINGAPORE,
}

ALADHAN_BASE_URL = "https://api.aladhan.com/v1/timings"


def fetch_from_api(
    calc_date: date,
    latitude: float,
    longitude: float,
    method: str,
    timezone: str,
) -> dict[str, datetime] | None:
    """Fetch prayer times from the Aladhan API.

    Returns dict of prayer_name -> timezone-aware datetime, or None on failure.
    """
    tz = ZoneInfo(timezone)
    date_str = calc_date.strftime("%d-%m-%Y")
    api_method = _ALADHAN_METHOD_MAP.get(method, 2)

    url = f"{ALADHAN_BASE_URL}/{date_str}"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "method": api_method,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Aladhan API request failed: %s", e)
        return None

    try:
        timings = data["data"]["timings"]
    except (KeyError, TypeError):
        logger.warning("Unexpected Aladhan API response format")
        return None

    result = {}
    api_keys = {
        "fajr": "Fajr",
        "dhuhr": "Dhuhr",
        "asr": "Asr",
        "maghrib": "Maghrib",
        "isha": "Isha",
    }

    for prayer, api_key in api_keys.items():
        time_str = timings.get(api_key)
        if not time_str:
            logger.warning("Missing %s in API response", api_key)
            return None

        # Aladhan returns "HH:MM" or "HH:MM (TZA)" — strip timezone abbreviation
        time_str = time_str.split(" ")[0]
        hour, minute = map(int, time_str.split(":"))
        dt = datetime(calc_date.year, calc_date.month, calc_date.day, hour, minute, tzinfo=tz)
        result[prayer] = dt

    logger.info("Fetched prayer times from Aladhan API for %s", calc_date)
    return result


def calculate_offline(
    calc_date: date,
    latitude: float,
    longitude: float,
    method: str,
    timezone: str,
) -> dict[str, datetime]:
    """Calculate prayer times offline using adhanpy.

    Returns dict of prayer_name -> timezone-aware datetime.
    """
    tz = ZoneInfo(timezone)
    coords = (latitude, longitude)
    adhanpy_method = _ADHANPY_METHOD_MAP.get(method, CalculationMethod.NORTH_AMERICA)

    pt = PrayerTimes(coords, calc_date, adhanpy_method, time_zone=tz)

    result = {}
    for prayer in PRAYER_NAMES:
        dt = getattr(pt, prayer)
        # adhanpy returns timezone-aware datetimes when time_zone is passed
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        result[prayer] = dt

    logger.info("Calculated prayer times offline (adhanpy) for %s", calc_date)
    return result


def get_prayer_times(
    calc_date: date,
    latitude: float,
    longitude: float,
    method: str,
    timezone: str,
) -> dict[str, datetime]:
    """Get prayer times with API-first, offline-fallback strategy.

    Args:
        calc_date: Date to calculate for.
        latitude: Location latitude.
        longitude: Location longitude.
        method: Calculation method name (e.g. "NORTH_AMERICA").
        timezone: IANA timezone string (e.g. "America/Los_Angeles").

    Returns:
        Dict mapping prayer names to timezone-aware datetimes.
    """
    # Try online first
    result = fetch_from_api(calc_date, latitude, longitude, method, timezone)
    if result:
        return result

    # Fallback to offline
    logger.warning("Falling back to offline prayer time calculation")
    return calculate_offline(calc_date, latitude, longitude, method, timezone)
