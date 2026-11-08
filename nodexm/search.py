"""Specialist searchers.

Each searcher turns a query into a list of hits. Web search uses Tavily (or a
self-hosted SearXNG). The academic, regulatory, news and data searchers add
free primary-source APIs so reports do not rely on blogs alone.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from . import cache
from .config import settings
from .llm import Usage

log = logging.getLogger("nodexm.search")

SEARCHERS = ("web", "academic", "regulatory", "news", "data")
TIMEOUT = httpx.Timeout(12.0, connect=6.0)
UA = "NodexMBot/3.0 (+research assistant)"

REGULATORY_DOMAINS = {
    "EU": ["eur-lex.europa.eu", "europa.eu", "edpb.europa.eu", "esma.europa.eu", "eba.europa.eu"],
    "US": ["federalregister.gov", "regulations.gov", "congress.gov", "whitehouse.gov", "sec.gov", "ftc.gov", "nist.gov"],
    "UK": ["legislation.gov.uk", "gov.uk", "ico.org.uk", "fca.org.uk"],
    "APAC": ["gov.sg", "mas.gov.sg", "pdpc.gov.sg", "meity.gov.in", "gov.au", "digital.go.jp"],
}
DATA_DOMAINS = [
    "data.worldbank.org", "worldbank.org", "oecd.org", "imf.org", "ourworldindata.org",
    "ec.europa.eu", "census.gov", "bls.gov", "bea.gov", "ons.gov.uk", "un.org", "iea.org",
]
STOPWORDS = set(
    "a an the of in on for to and or vs versus with by from at as is are was were be been what which who how why when "
    "does do did between about into over under than this that these those their its it compare comparison latest".split()
)


@dataclass
class Hit:
    url: str
    title: str
    snippet: str = ""
    text: str = ""          # full text when the API returns it (abstracts, data series)
    source_type: str = "web"
    searcher: str = "web"
    published: str | None = None
    query: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchContext:
    usage: Usage
    tavily_key: str = ""
    region: str = "Global"


def normalize_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        host = parts.netloc.lower().removeprefix("www.")
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))])
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower() or "https", host, path, query, ""))
    except Exception:
        return url.strip()


def domain_of(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def keywords(query: str, limit: int = 7) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\.]*", query) if w.lower() not in STOPWORDS]
    return " ".join(words[:limit]) or query


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
def _tavily(query: str, ctx: SearchContext, *, topic: str = "general", include_domains: list[str] | None = None,
            max_results: int = 5, searcher: str = "web", source_type: str = "web") -> list[Hit]:
    if not ctx.tavily_key:
        return []
    from tavily import TavilyClient

    kwargs = dict(query=query[:380], max_results=max_results, topic=topic, search_depth="basic")
    if include_domains:
        kwargs["include_domains"] = include_domains[:20]
    res = TavilyClient(api_key=ctx.tavily_key).search(**kwargs)
    ctx.usage.add_search(1)
    return [
        Hit(url=r.get("url", ""), title=r.get("title") or r.get("url", ""), snippet=(r.get("content") or "")[:600],
            source_type=source_type, searcher=searcher, published=r.get("published_date"), query=query)
        for r in res.get("results", []) if r.get("url")
    ]


def _searxng(query: str, *, categories: str = "general", searcher: str = "web", source_type: str = "web") -> list[Hit]:
    if not settings.searxng_url:
        return []
    r = httpx.get(f"{settings.searxng_url.rstrip('/')}/search",
                  params={"q": query, "format": "json", "categories": categories}, timeout=TIMEOUT)
    r.raise_for_status()
    return [
        Hit(url=x["url"], title=x.get("title") or x["url"], snippet=(x.get("content") or "")[:600],
            source_type=source_type, searcher=searcher, published=x.get("publishedDate"), query=query)
        for x in r.json().get("results", [])[:6] if x.get("url")
    ]


def _web_or_searx(query: str, ctx: SearchContext, **kw) -> list[Hit]:
    if ctx.tavily_key:
        return _tavily(query, ctx, **kw)
    categories = "news" if kw.get("topic") == "news" else "general"
    q = query
    if kw.get("include_domains"):
        q += " (" + " OR ".join(f"site:{d}" for d in kw["include_domains"][:4]) + ")"
    return _searxng(q, categories=categories, searcher=kw.get("searcher", "web"), source_type=kw.get("source_type", "web"))


def _openalex(query: str) -> list[Hit]:
    params = {
        "search": keywords(query, 10), "per-page": 5, "filter": "has_abstract:true",
        "select": "id,doi,display_name,publication_date,abstract_inverted_index,primary_location,cited_by_count",
        "mailto": settings.contact_email,
    }
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key
    r = httpx.get("https://api.openalex.org/works", params=params, timeout=TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    hits = []
    for w in r.json().get("results", []):
        inv = w.get("abstract_inverted_index") or {}
        positions = sorted((p, word) for word, ps in inv.items() for p in ps)
        abstract = " ".join(word for _, word in positions)
        loc = w.get("primary_location") or {}
        url = w.get("doi") or loc.get("landing_page_url") or w.get("id")
        venue = ((loc.get("source") or {}).get("display_name")) or ""
        hits.append(Hit(url=url, title=w.get("display_name") or url, snippet=abstract[:600], text=abstract,
                        source_type="academic", searcher="academic", published=w.get("publication_date"),
                        query=query, extra={"venue": venue, "cited_by": w.get("cited_by_count", 0)}))
    return hits


def _arxiv(query: str) -> list[Hit]:
    q = " AND ".join(f"all:{w}" for w in keywords(query, 6).split())
    r = httpx.get("https://export.arxiv.org/api/query", params={"search_query": q, "max_results": 4},
                  timeout=TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    hits = []
    for e in ET.fromstring(r.text).findall("a:entry", ns):
        url = (e.findtext("a:id", "", ns) or "").strip()
        summary = " ".join((e.findtext("a:summary", "", ns) or "").split())
        hits.append(Hit(url=url, title=" ".join((e.findtext("a:title", "", ns) or "").split()), snippet=summary[:600],
                        text=summary, source_type="preprint", searcher="academic",
                        published=(e.findtext("a:published", "", ns) or "")[:10], query=query))
    return hits


def _semantic_scholar(query: str) -> list[Hit]:
    if not settings.semantic_scholar_api_key:
        return []
    r = httpx.get("https://api.semanticscholar.org/graph/v1/paper/search",
                  params={"query": keywords(query, 10), "limit": 5,
                          "fields": "title,url,abstract,year,venue,citationCount,externalIds"},
                  headers={"x-api-key": settings.semantic_scholar_api_key}, timeout=TIMEOUT)
    r.raise_for_status()
    hits = []
    for p in r.json().get("data", []):
        if not p.get("abstract"):
            continue
        doi = (p.get("externalIds") or {}).get("DOI")
        hits.append(Hit(url=f"https://doi.org/{doi}" if doi else p.get("url"), title=p.get("title", ""),
                        snippet=p["abstract"][:600], text=p["abstract"], source_type="academic", searcher="academic",
                        published=str(p.get("year") or ""), query=query,
                        extra={"venue": p.get("venue"), "cited_by": p.get("citationCount", 0)}))
    return hits


def _federal_register(query: str) -> list[Hit]:
    params = [("conditions[term]", keywords(query, 8)), ("per_page", "4"), ("order", "relevance")]
    params += [("fields[]", f) for f in ("title", "html_url", "abstract", "publication_date", "type")]
    r = httpx.get("https://www.federalregister.gov/api/v1/documents.json", params=params, timeout=TIMEOUT,
                  headers={"User-Agent": UA})
    r.raise_for_status()
    return [
        Hit(url=d["html_url"], title=d.get("title", ""), snippet=(d.get("abstract") or "")[:600],
            source_type="regulatory", searcher="regulatory", published=d.get("publication_date"), query=query)
        for d in r.json().get("results", []) if d.get("html_url")
    ]


def _gdelt(query: str) -> list[Hit]:
    q = keywords(query, 5)
    if len(q) < 4:
        return []
    r = httpx.get("https://api.gdeltproject.org/api/v2/doc/doc",
                  params={"query": q, "mode": "artlist", "format": "json", "maxrecords": 6, "sort": "hybridrel"},
                  timeout=TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    try:
        articles = r.json().get("articles", [])
    except ValueError:  # GDELT answers with plain text on bad queries
        return []
    hits = []
    for a in articles:
        if a.get("language") not in (None, "English"):
            continue
        seen = a.get("seendate") or ""
        published = f"{seen[:4]}-{seen[4:6]}-{seen[6:8]}" if len(seen) >= 8 else None
        hits.append(Hit(url=a["url"], title=a.get("title", ""), source_type="news", searcher="news",
                        published=published, query=query))
    return hits


def _fred(query: str) -> list[Hit]:
    if not settings.fred_api_key:
        return []
    base = "https://api.stlouisfed.org/fred"
    r = httpx.get(f"{base}/series/search", params={"search_text": keywords(query, 6), "api_key": settings.fred_api_key,
                                                    "file_type": "json", "limit": 2, "order_by": "popularity"},
                  timeout=TIMEOUT)
    r.raise_for_status()
    hits = []
    for s in r.json().get("seriess", []):
        obs = httpx.get(f"{base}/series/observations", params={"series_id": s["id"], "api_key": settings.fred_api_key,
                                                               "file_type": "json", "sort_order": "desc", "limit": 8},
                        timeout=TIMEOUT).json().get("observations", [])
        rows = "; ".join(f"{o['date']}: {o['value']}" for o in obs)
        text = (f"{s['title']} ({s.get('units', '')}, {s.get('frequency', '')}, seasonal adjustment: "
                f"{s.get('seasonal_adjustment', '')}). Latest observations: {rows}. {s.get('notes', '')[:1500]}")
        hits.append(Hit(url=f"https://fred.stlouisfed.org/series/{s['id']}", title=f"FRED: {s['title']}",
                        snippet=text[:600], text=text, source_type="data", searcher="data",
                        published=s.get("last_updated", "")[:10], query=query))
    return hits


# ---------------------------------------------------------------------------
# Searchers
# ---------------------------------------------------------------------------
def _safe(fn, *args, **kwargs) -> list[Hit]:
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        log.info("%s failed: %s", getattr(fn, "__name__", fn), e)
        return []


def _parallel(*calls) -> list[Hit]:
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(_safe, fn, *args, **kwargs) for fn, args, kwargs in calls]
        return [h for f in futures for h in f.result()]


def search_web(query: str, ctx: SearchContext) -> list[Hit]:
    return _safe(_web_or_searx, query, ctx, max_results=6)


def search_academic(query: str, ctx: SearchContext) -> list[Hit]:
    return _parallel((_openalex, (query,), {}), (_arxiv, (query,), {}), (_semantic_scholar, (query,), {}))


def search_regulatory(query: str, ctx: SearchContext) -> list[Hit]:
    region = ctx.region if ctx.region in REGULATORY_DOMAINS else "Global"
    domains = REGULATORY_DOMAINS[region] if region != "Global" else [d for ds in REGULATORY_DOMAINS.values() for d in ds[:3]]
    calls = [(_web_or_searx, (query, ctx), {"include_domains": domains, "max_results": 5,
                                            "searcher": "regulatory", "source_type": "regulatory"})]
    if region in ("US", "Global"):
        calls.append((_federal_register, (query,), {}))
    return _parallel(*calls)


def search_news(query: str, ctx: SearchContext) -> list[Hit]:
    return _parallel(
        (_web_or_searx, (query, ctx), {"topic": "news", "max_results": 5, "searcher": "news", "source_type": "news"}),
        (_gdelt, (query,), {}),
    )


def search_data(query: str, ctx: SearchContext) -> list[Hit]:
    return _parallel(
        (_fred, (query,), {}),
        (_web_or_searx, (query, ctx), {"include_domains": DATA_DOMAINS, "max_results": 5,
                                       "searcher": "data", "source_type": "data"}),
    )


_DISPATCH = {
    "web": search_web,
    "academic": search_academic,
    "regulatory": search_regulatory,
    "news": search_news,
    "data": search_data,
}


def run_searcher(name: str, query: str, ctx: SearchContext) -> list[Hit]:
    """Run one searcher with a 24h cache. Unknown names fall back to web search."""
    name = name if name in _DISPATCH else "web"
    key = cache.make_key("search", name, query.strip().lower(), ctx.region, bool(ctx.tavily_key))
    cached = cache.get(key)
    if cached is not None:
        return [Hit(**h) for h in cached]
    hits = _DISPATCH[name](query, ctx)
    seen, unique = set(), []
    for h in hits:
        if not h.url:
            continue
        n = normalize_url(h.url)
        if n not in seen:
            seen.add(n)
            unique.append(h)
    if unique:
        cache.put(key, [h.to_dict() for h in unique])
    return unique


def search_available(tavily_key: str) -> bool:
    return bool(tavily_key or settings.searxng_url)
