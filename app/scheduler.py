"""Daily-brief scheduler (stub).

TODO:
  * run `run_daily_brief` every day at DAILY_BRIEF_TIME in TZ_NAME (APScheduler or a cron job)
  * deliver the brief (email / Telegram / web push) instead of only storing it
  * pull Calendar + Gmail once app/tools/integrations.py is wired
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.agent import Agent

BRIEF_PROMPT = "Brief me for today. Follow the daily-brief skill."


def next_run(now: datetime, at: str = "08:00", tz: str = "Asia/Karachi") -> datetime:
    """Next occurrence of HH:MM in the given timezone, strictly after `now`."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    hour, minute = (int(x) for x in at.split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


def run_daily_brief(agent: Agent) -> str:
    result = agent.run(BRIEF_PROMPT)
    try:
        agent.memory.add(result.reply, kind="daily_brief")
    except Exception:
        pass  # storing the brief is best-effort
    return result.reply


def start_scheduler(agent: Agent, at: str = "08:00", tz: str = "Asia/Karachi") -> None:
    # TODO: start a background job that sleeps until next_run(...) and calls run_daily_brief.
    raise NotImplementedError("daily brief scheduler is not wired yet")
