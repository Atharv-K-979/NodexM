"""Watch cadences: names users pick, the cron they map to, and the next run time (UTC)."""
from __future__ import annotations

from datetime import datetime

from croniter import croniter

from .models import utcnow

CADENCES: dict[str, tuple[str, str]] = {
    "daily": ("0 7 * * *", "Every day at 07:00 UTC"),
    "workdays": ("0 7 * * 1-5", "Monday to Friday at 07:00 UTC"),
    "weekly": ("0 7 * * 1", "Every Monday at 07:00 UTC"),
    "biweekly": ("0 7 1,15 * *", "On the 1st and 15th at 07:00 UTC"),
    "monthly": ("0 7 1 * *", "On the 1st of each month at 07:00 UTC"),
    "manual": ("", "Only when you run it"),
}


def cron_for(cadence: str) -> str | None:
    return CADENCES.get(cadence, CADENCES["weekly"])[0] or None


def label_for(cadence: str) -> str:
    return CADENCES.get(cadence, ("", cadence))[1]


def next_run_time(cron: str | None, after: datetime | None = None) -> datetime | None:
    if not cron:
        return None
    try:
        return croniter(cron, after or utcnow()).get_next(datetime)
    except (ValueError, KeyError):
        return None
