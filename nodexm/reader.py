"""Full-text reader: Jina Reader when a key is set, otherwise a local fetch that
respects robots.txt, extracts the main text with trafilatura, and reads PDFs."""
from __future__ import annotations

import io
import logging
import threading
import urllib.robotparser
from dataclasses import asdict, dataclass

import httpx

from . import cache
from .config import settings
from .search import domain_of

log = logging.getLogger("nodexm.reader")

MAX_CHARS = 20_000
MAX_BYTES = 8 * 1024 * 1024
UA = "Mozilla/5.0 (compatible; NodexMBot/3.0; research assistant)"
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_robots_lock = threading.Lock()


@dataclass
class Page:
    url: str
    text: str = ""
    title: str = ""
    published: str | None = None
    method: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return len(self.text) >= 200


def _allowed(url: str) -> bool:
    host = domain_of(url)
    with _robots_lock:
        if host in _robots:
            rp = _robots[host]
            return rp is None or rp.can_fetch("NodexMBot", url)
    rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
    try:
        scheme = url.split("://", 1)[0]
        r = httpx.get(f"{scheme}://{host}/robots.txt", timeout=4, headers={"User-Agent": UA}, follow_redirects=True)
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
        else:
            rp = None
    except Exception:
        rp = None
    with _robots_lock:
        _robots[host] = rp
    return rp is None or rp.can_fetch("NodexMBot", url)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages[:20]:
        parts.append(page.extract_text() or "")
        if sum(len(p) for p in parts) > MAX_CHARS:
            break
    return "\n".join(parts)


def _read_local(url: str) -> Page:
    if not _allowed(url):
        return Page(url=url, method="local", error="blocked by robots.txt")
    with httpx.Client(timeout=httpx.Timeout(15.0, connect=6.0), follow_redirects=True,
                      headers={"User-Agent": UA, "Accept-Language": "en"}) as client:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    break
            data = b"".join(chunks)
            ctype = r.headers.get("content-type", "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return Page(url=url, text=_pdf_text(data)[:MAX_CHARS], method="pdf")
    import trafilatura

    html = data.decode("utf-8", errors="replace")
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_recall=True) or ""
    title, published = "", None
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
        if meta:
            title, published = meta.title or "", meta.date
    except Exception:
        pass
    if len(text) < 200:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        title = title or (soup.title.string.strip() if soup.title and soup.title.string else "")
    return Page(url=url, text=text[:MAX_CHARS], title=title, published=published, method="local")


def _read_jina(url: str) -> Page:
    headers = {"Accept": "application/json", "X-Return-Format": "text"}
    if settings.jina_api_key:
        headers["Authorization"] = f"Bearer {settings.jina_api_key}"
    r = httpx.get(f"https://r.jina.ai/{url}", headers=headers, timeout=httpx.Timeout(30.0, connect=6.0))
    r.raise_for_status()
    data = r.json().get("data") or {}
    published = (data.get("publishedTime") or "")[:10] or None
    return Page(url=url, text=(data.get("content") or "")[:MAX_CHARS], title=data.get("title") or "",
                published=published, method="jina")


def read(url: str) -> Page:
    """Fetch the main text of a page. Never raises; check Page.ok."""
    key = cache.make_key("page", url)
    cached = cache.get(key)
    if cached:
        return Page(**cached)

    provider = settings.reader_provider
    if provider == "jina":
        order = [_read_jina, _read_local]
    elif provider == "local":
        order = [_read_local]
    else:  # auto: Jina first only when a key lifts its rate limit
        order = [_read_jina, _read_local] if settings.jina_api_key else [_read_local, _read_jina]

    page, errors = Page(url=url), []
    for fn in order:
        try:
            page = fn(url)
            if page.ok:
                break
            errors.append(page.error or f"{fn.__name__}: too little text")
        except Exception as e:
            errors.append(f"{fn.__name__}: {str(e)[:120]}")
    if not page.ok:
        page.error = "; ".join(errors)
    else:
        cache.put(key, asdict(page))
    return page
