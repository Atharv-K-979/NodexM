"""LLM access for the agents.

Two tiers: "lite" for high-volume work (extraction, planning, critique) and
"flash" for judgement-heavy work (verification, numbers, writing). Each call
tries the tier's model, then the configured fallbacks, and records token usage
so every run has a cost estimate.
"""
from __future__ import annotations

import logging
import threading
import warnings
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .config import settings

log = logging.getLogger("nodexm.llm")
# Newer Gemini models ignore temperature and warn on every call; the warning is noise here.
warnings.filterwarnings("ignore", message=r".*uses fixed sampling defaults.*")

Tier = Literal["lite", "flash"]
T = TypeVar("T", bound=BaseModel)

# Fail over quickly: an overloaded model can otherwise hang for ~2 minutes before a 504.
REQUEST_TIMEOUT = 75
# Model names the API answered 404 for. Shared across runs in this process.
_MISSING: set[str] = set()


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    """Thread-safe token and credit counter for one run."""

    lite_in: int = 0
    lite_out: int = 0
    flash_in: int = 0
    flash_out: int = 0
    embed_tokens: int = 0
    search_credits: int = 0
    calls: int = 0
    by_agent: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_llm(self, tier: Tier, agent: str, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            self.calls += 1
            if tier == "lite":
                self.lite_in += tokens_in
                self.lite_out += tokens_out
            else:
                self.flash_in += tokens_in
                self.flash_out += tokens_out
            a = self.by_agent.setdefault(agent or "other", {"calls": 0, "in": 0, "out": 0})
            a["calls"] += 1
            a["in"] += tokens_in
            a["out"] += tokens_out

    def add_embed(self, tokens: int) -> None:
        with self._lock:
            self.embed_tokens += tokens

    def add_search(self, credits: int = 1) -> None:
        with self._lock:
            self.search_credits += credits

    def cost(self) -> float:
        s = settings
        return round(
            (self.lite_in * s.price_lite_in + self.lite_out * s.price_lite_out
             + self.flash_in * s.price_flash_in + self.flash_out * s.price_flash_out
             + self.embed_tokens * s.price_embed) / 1_000_000
            + self.search_credits * s.price_search_credit,
            5,
        )

    def as_dict(self) -> dict:
        with self._lock:
            return {
                "calls": self.calls,
                "lite_tokens": {"in": self.lite_in, "out": self.lite_out},
                "flash_tokens": {"in": self.flash_in, "out": self.flash_out},
                "embedding_tokens": self.embed_tokens,
                "search_credits": self.search_credits,
                "by_agent": {k: dict(v) for k, v in self.by_agent.items()},
                "cost_usd": self.cost(),
            }


def message_text(msg) -> str:
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text", ""))
        return "".join(parts)
    return str(content)


def _tokens(msg) -> tuple[int, int]:
    meta = getattr(msg, "usage_metadata", None) or {}
    return int(meta.get("input_tokens", 0) or 0), int(meta.get("output_tokens", 0) or 0)


class LLMClient:
    """Gemini client bound to one API key and one run's usage counter."""

    def __init__(self, api_key: str, usage: Usage | None = None):
        if not api_key:
            raise LLMError("No Gemini API key configured. Set GEMINI_API_KEY or add your own key in Settings.")
        self.api_key = api_key
        self.usage = usage or Usage()
        self._models: dict[str, object] = {}
        self._skip: set[str] = set()
        self._lock = threading.Lock()

    def _model(self, name: str, temperature: float):
        key = f"{name}:{temperature}"
        with self._lock:
            if key not in self._models:
                from langchain_google_genai import ChatGoogleGenerativeAI

                self._models[key] = ChatGoogleGenerativeAI(
                    model=name,
                    google_api_key=self.api_key,
                    temperature=temperature,
                    max_retries=1,
                    timeout=REQUEST_TIMEOUT,
                )
            return self._models[key]

    @staticmethod
    def _chain(tier: Tier) -> list[str]:
        first = settings.lite_model if tier == "lite" else settings.flash_model
        fallbacks = list(settings.fallback_models)
        # Flash work prefers full Flash fallbacks; Lite work prefers cheaper Lite fallbacks.
        fallbacks.sort(key=lambda n: ("lite" in n) if tier == "flash" else ("lite" not in n))
        names = [first] + fallbacks
        return list(dict.fromkeys(n for n in names if n))

    def _candidates(self, tier: Tier) -> list[str]:
        """Models to try, skipping ones that do not exist for this key or already failed in this run."""
        chain = self._chain(tier)
        with self._lock:
            usable = [n for n in chain if n not in _MISSING and n not in self._skip]
        return usable or [n for n in chain if n not in _MISSING] or chain

    def _failed(self, name: str, agent: str, kind: str, e: Exception) -> str:
        msg = str(e)
        if "NOT_FOUND" in msg or "404" in msg[:40]:
            with self._lock:
                _MISSING.add(name)  # wrong model name: never try it again in this process
            log.error("model %s does not exist for this API key; check GEMINI_*_MODEL in .env", name)
        else:
            with self._lock:
                self._skip.add(name)  # overloaded, timed out or unparseable: skip it for the rest of this run
            log.warning("%s call failed on %s (%s): %s", kind, name, agent, msg[:300])
        return f"{name}: {msg[:200]}"

    def structured(self, tier: Tier, schema: type[T], system: str, user: str, *, agent: str = "",
                   temperature: float = 0.2) -> T:
        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        errors = []
        for name in self._candidates(tier):
            try:
                runnable = self._model(name, temperature).with_structured_output(schema, include_raw=True)
                out = runnable.invoke(messages)
                raw = out.get("raw") if isinstance(out, dict) else None
                if raw is not None:
                    self.usage.add_llm(tier, agent, *_tokens(raw))
                parsed = out.get("parsed") if isinstance(out, dict) else out
                if parsed is None:
                    raise LLMError(f"could not parse output: {out.get('parsing_error') if isinstance(out, dict) else ''}")
                if isinstance(parsed, dict):
                    parsed = schema.model_validate(parsed)
                return parsed
            except Exception as e:  # try the next model
                errors.append(self._failed(name, agent, "structured", e))
        raise LLMError("; ".join(errors) or "no models configured")

    def text(self, tier: Tier, system: str, user: str, *, agent: str = "", temperature: float = 0.3) -> str:
        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        errors = []
        for name in self._candidates(tier):
            try:
                msg = self._model(name, temperature).invoke(messages)
                self.usage.add_llm(tier, agent, *_tokens(msg))
                text = message_text(msg).strip()
                if not text:
                    raise LLMError("empty response")
                return text
            except Exception as e:
                errors.append(self._failed(name, agent, "text", e))
        raise LLMError("; ".join(errors) or "no models configured")

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Embeddings for claim matching. Returns None on failure so callers can fall back to lexical vectors."""
        if not texts:
            return []
        try:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings

            emb = GoogleGenerativeAIEmbeddings(model=settings.embedding_model, google_api_key=self.api_key)
            vectors = emb.embed_documents(texts)
            self.usage.add_embed(sum(len(t) for t in texts) // 4)
            return [list(map(float, v)) for v in vectors]
        except Exception as e:
            log.warning("embedding failed, using lexical vectors: %s", e)
            return None
