from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from croniter import croniter

CADENCE_OPTIONS = {
    "daily": "0 9 * * *",
    "workdays": "0 9 * * 1-5",
    "weekly": "0 9 * * 1",
    "biweekly": "0 9 * * 1,15",
    "monthly": "0 9 1 * *",
    "hourly": "0 * * * *",
    "realtime": None,  # ad-hoc only
}

ONBOARDING_STEPS: list[dict] = [
    {
        "step": 1,
        "name": "topic",
        "question": "What topic would you like this Watch to monitor?",
        "hint": "Be specific: include comparison targets, timeframe, or reporting angle.",
        "free_text": True,
    },
    {
        "step": 2,
        "name": "region",
        "question": "Which region or scope matters most for this Watch?",
        "hint": "Affects source prioritization and terminology.",
        "options": ["Global", "EU", "US", "UK", "APAC", "Other"],
    },
    {
        "step": 3,
        "name": "cadence",
        "question": "How often should this Watch run research?",
        "options": ["weekly", "daily", "workdays", "biweekly", "monthly", "hourly", "realtime"],
    },
    {
        "step": 4,
        "name": "channel",
        "question": "Where should approved reports be delivered?",
        "options": ["Email", "In-app only", "Slack (later)", "Webhook (later)"],
    },
    {
        "step": 5,
        "name": "depth",
        "question": "How deep should each report be?",
        "options": ["summary", "analyst", "technical"],
    },
    {
        "step": 6,
        "name": "format",
        "question": "Preferred report format?",
        "options": ["Markdown + highlights", "Executive PDF", "Bullet digest"],
    },
    {
        "step": 7,
        "name": "source_prefs",
        "question": "Any source-type preferences? Pick primary focus.",
        "options": [
            "general+news",
            "academic+news",
            "regulatory+news",
            "general+academic+regulatory",
            "any",
        ],
    },
]


def derive_profile(answers: dict) -> dict:
    """Flatten onboarding answers into the user_profile dict used by the pipeline."""
    profile: dict = {}
    for key in ("region", "depth", "format", "channel", "source_prefs"):
        if answers.get(key):
            profile[key] = answers[key]
    return profile


def derive_cron(cadence: Optional[str]) -> Optional[str]:
    if not cadence:
        return None
    return CADENCE_OPTIONS.get(cadence)


def next_run_time(cron_expr: Optional[str], from_time: Optional[datetime] = None) -> Optional[datetime]:
    if not cron_expr:
        return None
    try:
        base = from_time or datetime.utcnow()
        return croniter(cron_expr, base).get_next(datetime)  # type: ignore[no-any-return]
    except Exception:
        return None


def cadence_for_humans(cadence: Optional[str]) -> str:
    if not cadence:
        return "Manual only"
    mapping = {
        "daily": "Every day at 09:00 UTC",
        "workdays": "Mon–Fri at 09:00 UTC",
        "weekly": "Every Monday at 09:00 UTC",
        "biweekly": "Mondays & 15th of month",
        "monthly": "First of month at 09:00 UTC",
        "hourly": "Every hour on the hour",
        "realtime": "Manual only",
    }
    return mapping.get(cadence, cadence)
