"""Email delivery through Resend. Quietly does nothing when no key is configured."""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger("nodexm.notify")


def email_enabled() -> bool:
    return bool(settings.resend_api_key)


def send_email(to: str, subject: str, html: str) -> bool:
    if not email_enabled():
        return False
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.email_from, "to": [to], "subject": subject, "html": html},
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("email to %s failed: %s", to, e)
        return False
