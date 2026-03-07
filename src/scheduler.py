"""APScheduler wrapper for scheduling daily prayer time recalculation and adhan playback."""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from src.config import AppConfig, PRAYER_NAMES
from src.player import AdhanPlayer
from src.prayer_times import get_prayer_times

logger = logging.getLogger("adhan.scheduler")


class AdhanScheduler:
    """Manages scheduling of adhan playback at prayer times."""

    def __init__(self, config: AppConfig, player: AdhanPlayer):
        self.config = config
        self.player = player
        self.tz = ZoneInfo(config.location.timezone)
        self.scheduler = BackgroundScheduler(timezone=self.tz)

    def _schedule_prayers_for_today(self) -> None:
        """Fetch today's prayer times and schedule adhan playback for future prayers."""
        # Remove any existing prayer jobs
        for job in self.scheduler.get_jobs():
            if job.id.startswith("prayer_"):
                job.remove()

        now = datetime.now(self.tz)
        today = date.today()

        times = get_prayer_times(
            calc_date=today,
            latitude=self.config.location.latitude,
            longitude=self.config.location.longitude,
            method=self.config.calculation.method,
            timezone=self.config.location.timezone,
        )

        scheduled_count = 0
        for prayer in PRAYER_NAMES:
            if prayer in self.config.prayers.disabled:
                logger.info("Skipping %s (disabled in config)", prayer.capitalize())
                continue

            prayer_time = times[prayer]

            if prayer_time <= now:
                logger.debug("Skipping %s at %s (already past)", prayer, prayer_time.strftime("%H:%M"))
                continue

            self.scheduler.add_job(
                func=self.player.play_adhan,
                trigger=DateTrigger(run_date=prayer_time),
                args=[prayer],
                id=f"prayer_{prayer}",
                name=f"Adhan for {prayer.capitalize()}",
                misfire_grace_time=self.config.scheduler.misfire_grace_seconds,
                replace_existing=True,
            )
            scheduled_count += 1
            logger.info(
                "Scheduled %s at %s",
                prayer.capitalize(),
                prayer_time.strftime("%H:%M:%S %Z"),
            )

        if scheduled_count == 0:
            logger.info("No remaining prayers to schedule today")
        else:
            logger.info("Scheduled %d prayer(s) for today", scheduled_count)

    def start(self) -> None:
        """Start the scheduler with daily recalculation and today's prayer jobs."""
        # Parse daily recalc time
        recalc_parts = self.config.scheduler.daily_recalc_time.split(":")
        recalc_hour = int(recalc_parts[0])
        recalc_minute = int(recalc_parts[1])

        # Schedule daily recalculation
        self.scheduler.add_job(
            func=self._schedule_prayers_for_today,
            trigger=CronTrigger(hour=recalc_hour, minute=recalc_minute, timezone=self.tz),
            id="daily_recalc",
            name="Daily prayer time recalculation",
            replace_existing=True,
        )
        logger.info(
            "Daily recalculation scheduled at %s",
            self.config.scheduler.daily_recalc_time,
        )

        # Schedule today's remaining prayers immediately
        self._schedule_prayers_for_today()

        self.scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        """Gracefully shut down the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler shut down")
