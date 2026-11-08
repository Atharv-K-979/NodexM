"""Free-run allowance.

Every account gets FREE_QUICK_RUNS Quick and FREE_DEEP_RUNS Deep runs on the
server's keys. Failed and cancelled runs do not count. Users who add their own
Gemini key run on their own keys (and their own Tavily key or the free search
APIs), so they cost the server nothing and only have a daily safety cap.
A global daily budget stops free runs if the day's spend reaches the limit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from .auth import is_admin
from .config import settings
from .models import Run, User, utcnow

ACTIVE = ("queued", "running")


@dataclass
class Allowance:
    can_run: bool
    reason: str
    allow_deep: bool
    force_deep: bool
    quick_left: int
    deep_left: int
    runs_today: int
    daily_limit: int
    own_keys: bool
    unlimited: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _count(session: Session, *conditions) -> int:
    return session.exec(select(func.count()).select_from(Run).where(*conditions)).one()


def spent_today(session: Session) -> float:
    since = utcnow() - timedelta(hours=24)
    total = session.exec(select(func.sum(Run.cost_usd)).where(Run.created_at >= since,
                                                              Run.used_own_keys == False)).one()  # noqa: E712
    return float(total or 0.0)


def allowance(session: Session, user: User) -> Allowance:
    since = utcnow() - timedelta(hours=24)
    own_keys = bool(user.gemini_key_enc)
    admin = is_admin(user)
    active = _count(session, Run.user_id == user.id, Run.status.in_(ACTIVE))
    runs_today = _count(session, Run.user_id == user.id, Run.created_at >= since,
                        Run.status.not_in(("failed", "cancelled")))

    if own_keys or admin:
        limit = settings.own_key_runs_per_day if own_keys and not admin else 10_000
        if not own_keys and not settings.gemini_api_key:
            return Allowance(False, "The server has no Gemini key yet. Add GEMINI_API_KEY to .env.",
                             False, False, 0, 0, runs_today, limit, own_keys, True)
        if active >= 2:
            reason = "Two runs are already in progress. Wait for one to finish."
        elif runs_today >= limit:
            reason = f"You reached today's limit of {limit} runs."
        else:
            reason = ""
        return Allowance(not reason, reason, True, False, -1, -1, runs_today, limit, own_keys, True)

    used = dict(session.exec(
        select(Run.mode, func.count()).where(Run.user_id == user.id, Run.used_own_keys == False,  # noqa: E712
                                             Run.status == "completed").group_by(Run.mode)).all())
    quick_left = max(0, settings.free_quick_runs - used.get("quick", 0))
    deep_left = max(0, settings.free_deep_runs - used.get("deep", 0))
    limit = settings.free_runs_per_day

    reason = ""
    if not settings.gemini_api_key:
        reason = "The server has no Gemini key yet. Add your own key in Settings to start."
    elif quick_left + deep_left == 0:
        reason = "You have used your free runs. Add your own Gemini key in Settings to keep going."
    elif active:
        reason = "A run is already in progress. Wait for it to finish."
    elif runs_today >= limit:
        reason = f"Free accounts can start {limit} runs a day. Try again tomorrow or add your own key."
    elif spent_today(session) >= settings.daily_budget_usd:
        reason = "Free runs are paused for today because the daily budget was reached. Add your own key to continue."
    return Allowance(not reason, reason, deep_left > 0, quick_left == 0 and deep_left > 0, quick_left, deep_left,
                     runs_today, limit, False, False)
