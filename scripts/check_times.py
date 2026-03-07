#!/usr/bin/env python3
"""Print today's prayer times for debugging. Run from project root."""

import os
import sys
from datetime import date

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.prayer_times import get_prayer_times


def main():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config.yaml",
    )
    config = load_config(config_path)

    today = date.today()
    print(f"Prayer Times for {today.strftime('%A, %B %d, %Y')}")
    print(f"Location: {config.location.latitude}, {config.location.longitude}")
    print(f"Timezone: {config.location.timezone}")
    print(f"Method: {config.calculation.method}")
    print("-" * 40)

    times = get_prayer_times(
        calc_date=today,
        latitude=config.location.latitude,
        longitude=config.location.longitude,
        method=config.calculation.method,
        timezone=config.location.timezone,
    )

    for prayer, dt in times.items():
        print(f"  {prayer.capitalize():10s} {dt.strftime('%I:%M %p %Z')}")


if __name__ == "__main__":
    main()
