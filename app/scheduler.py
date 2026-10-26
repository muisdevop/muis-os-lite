"""Daily-brief scheduler: an APScheduler job at DAILY_BRIEF_TIME in TZ_NAME."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


def parse_hhmm(at: str) -> tuple[int, int]:
    try:
        hour, minute = (int(x) for x in at.strip().split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError
        return hour, minute
    except ValueError:
        log.warning("bad DAILY_BRIEF_TIME %r, using 08:00", at)
        return 8, 0


def next_run(now: datetime, at: str = "08:00", tz: str = "Asia/Karachi") -> datetime:
    """Next occurrence of HH:MM in the given timezone, strictly after `now`."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    hour, minute = parse_hhmm(at)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


def start_scheduler(job: Callable[[], Any], at: str = "08:00", tz: str = "Asia/Karachi") -> Any:
    """Start a background scheduler that calls `job` daily. Returns the scheduler."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    hour, minute = parse_hhmm(at)
    scheduler = BackgroundScheduler(timezone=ZoneInfo(tz))
    scheduler.add_job(
        job, CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(tz)),
        id="daily_brief", replace_existing=True, misfire_grace_time=3600, coalesce=True,
    )
    scheduler.start()
    log.info("daily brief scheduled at %s %s", at, tz)
    return scheduler
