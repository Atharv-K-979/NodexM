"""Central settings, read once from the environment (.env is loaded here)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _str(name: str, default: str = "") -> str:
    """Blank values in .env (e.g. `DATABASE_URL=`) mean 'use the default'."""
    return (os.getenv(name) or "").strip() or default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in (os.getenv(name) or default).split(",") if x.strip()]


def _load_secret() -> str:
    """JWT secret from env; otherwise a local secret file so tokens survive restarts in dev."""
    value = _str("NODEXM_JWT_SECRET")
    if value:
        return value
    path = ROOT / ".nodexm_secret"
    if path.exists():
        return path.read_text().strip()
    value = secrets.token_hex(32)
    path.write_text(value)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


@dataclass(frozen=True)
class Settings:
    # Core
    database_url: str = field(default_factory=lambda: _str("DATABASE_URL", f"sqlite:///{ROOT / 'nodexm_v3.db'}"))
    jwt_secret: str = field(default_factory=_load_secret)
    jwt_ttl_hours: int = field(default_factory=lambda: _int("JWT_TTL_HOURS", 72))
    cors_origins: list[str] = field(default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:5173"))
    admin_emails: list[str] = field(default_factory=lambda: [e.lower() for e in _list("ADMIN_EMAILS")])

    # LLM
    gemini_api_key: str = field(default_factory=lambda: _str("GEMINI_API_KEY") or _str("GOOGLE_API_KEY"))
    lite_model: str = field(default_factory=lambda: _str("GEMINI_LITE_MODEL", "gemini-3.5-flash-lite"))
    flash_model: str = field(default_factory=lambda: _str("GEMINI_FLASH_MODEL", "gemini-3.6-flash"))
    fallback_models: list[str] = field(default_factory=lambda: _list("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash,gemini-3.1-flash-lite"))
    embedding_model: str = field(default_factory=lambda: _str("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001"))

    # Prices in USD per million tokens, used only for cost estimates
    price_lite_in: float = field(default_factory=lambda: _float("PRICE_LITE_INPUT", 0.10))
    price_lite_out: float = field(default_factory=lambda: _float("PRICE_LITE_OUTPUT", 0.40))
    price_flash_in: float = field(default_factory=lambda: _float("PRICE_FLASH_INPUT", 0.30))
    price_flash_out: float = field(default_factory=lambda: _float("PRICE_FLASH_OUTPUT", 2.50))
    price_embed: float = field(default_factory=lambda: _float("PRICE_EMBEDDING", 0.15))
    price_search_credit: float = field(default_factory=lambda: _float("PRICE_SEARCH_CREDIT", 0.008))

    # Search and reading
    tavily_api_key: str = field(default_factory=lambda: _str("TAVILY_API_KEY"))
    searxng_url: str = field(default_factory=lambda: _str("SEARXNG_URL"))
    jina_api_key: str = field(default_factory=lambda: _str("JINA_API_KEY"))
    reader_provider: str = field(default_factory=lambda: _str("READER_PROVIDER", "auto"))  # auto | jina | local
    semantic_scholar_api_key: str = field(default_factory=lambda: _str("S2_API_KEY"))
    fred_api_key: str = field(default_factory=lambda: _str("FRED_API_KEY"))
    openalex_api_key: str = field(default_factory=lambda: _str("OPENALEX_API_KEY"))
    contact_email: str = field(default_factory=lambda: _str("CONTACT_EMAIL", "nodexm@example.com"))
    cache_ttl_hours: int = field(default_factory=lambda: _int("CACHE_TTL_HOURS", 24))

    # Free runs and limits
    free_quick_runs: int = field(default_factory=lambda: _int("FREE_QUICK_RUNS", 2))
    free_deep_runs: int = field(default_factory=lambda: _int("FREE_DEEP_RUNS", 1))
    free_runs_per_day: int = field(default_factory=lambda: _int("FREE_RUNS_PER_DAY", 2))
    own_key_runs_per_day: int = field(default_factory=lambda: _int("OWN_KEY_RUNS_PER_DAY", 50))
    daily_budget_usd: float = field(default_factory=lambda: _float("DAILY_BUDGET_USD", 2.0))
    max_concurrent_runs: int = field(default_factory=lambda: _int("MAX_CONCURRENT_RUNS", 2))

    # Email
    resend_api_key: str = field(default_factory=lambda: _str("RESEND_API_KEY"))
    email_from: str = field(default_factory=lambda: _str("EMAIL_FROM", "NodexM <alerts@example.com>"))
    app_url: str = field(default_factory=lambda: _str("APP_URL", "http://localhost:5173"))


settings = Settings()
