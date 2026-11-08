"""Rule-based source credibility. Cheap, explainable, and shown to the user."""
from __future__ import annotations

from datetime import date

from .search import domain_of

OFFICIAL_SUFFIXES = (".gov", ".gov.uk", ".gouv.fr", ".gov.au", ".gov.sg", ".gov.in", ".go.jp", ".gc.ca", ".bund.de",
                     ".mil", ".int")
OFFICIAL_DOMAINS = ("europa.eu", "who.int", "oecd.org", "imf.org", "worldbank.org", "un.org", "bis.org", "iea.org",
                    "federalregister.gov", "legislation.gov.uk", "fred.stlouisfed.org", "ourworldindata.org")
ACADEMIC_DOMAINS = ("doi.org", "nature.com", "science.org", "sciencedirect.com", "springer.com", "link.springer.com",
                    "wiley.com", "onlinelibrary.wiley.com", "thelancet.com", "nejm.org", "pnas.org", "acm.org",
                    "ieee.org", "jstor.org", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "ssrn.com", "nber.org",
                    "aclanthology.org", "openreview.net", "cell.com", "bmj.com", "tandfonline.com", "sagepub.com")
PREPRINT_DOMAINS = ("arxiv.org", "biorxiv.org", "medrxiv.org", "researchgate.net")
NEWS_DOMAINS = ("reuters.com", "apnews.com", "bbc.co.uk", "bbc.com", "ft.com", "nytimes.com", "wsj.com",
                "economist.com", "bloomberg.com", "theguardian.com", "washingtonpost.com", "npr.org", "politico.com",
                "politico.eu", "axios.com", "cnbc.com", "nikkei.com", "asia.nikkei.com", "lemonde.fr", "spiegel.de",
                "aljazeera.com", "theverge.com", "wired.com", "arstechnica.com", "techcrunch.com", "euractiv.com",
                "forbes.com", "fortune.com", "scmp.com", "thehindu.com", "abc.net.au", "cbc.ca")
LOW_DOMAINS = ("reddit.com", "quora.com", "medium.com", "substack.com", "blogspot.com", "wordpress.com",
               "linkedin.com", "x.com", "twitter.com", "facebook.com", "youtube.com", "tiktok.com", "pinterest.com",
               "fandom.com")


def _matches(domain: str, candidates: tuple[str, ...]) -> bool:
    return any(domain == c or domain.endswith("." + c) for c in candidates)


def _parse_year(published: str | None) -> int | None:
    if not published:
        return None
    try:
        year = int(str(published)[:4])
        return year if 1900 < year <= date.today().year + 1 else None
    except ValueError:
        return None


def score_source(url: str, source_type: str = "web", published: str | None = None, text_len: int = 0,
                 extra: dict | None = None) -> tuple[float, list[str]]:
    domain = domain_of(url)
    extra = extra or {}
    reasons: list[str] = []

    if _matches(domain, OFFICIAL_DOMAINS) or domain.endswith(OFFICIAL_SUFFIXES):
        score = 0.9
        reasons.append("Official or intergovernmental source")
    elif _matches(domain, ACADEMIC_DOMAINS) or (source_type == "academic" and extra.get("venue")):
        score = 0.85
        reasons.append("Peer-reviewed or scholarly publisher")
    elif _matches(domain, PREPRINT_DOMAINS) or source_type == "preprint":
        score = 0.7
        reasons.append("Preprint, not yet peer-reviewed")
    elif domain.endswith((".edu", ".ac.uk", ".edu.au", ".ac.in")):
        score = 0.75
        reasons.append("University domain")
    elif _matches(domain, NEWS_DOMAINS):
        score = 0.75
        reasons.append("Established news outlet")
    elif domain.endswith("wikipedia.org"):
        score = 0.6
        reasons.append("Encyclopedia, check its references")
    elif _matches(domain, LOW_DOMAINS):
        score = 0.35
        reasons.append("User-generated or social platform")
    elif domain.endswith(".org"):
        score = 0.55
        reasons.append("Organisation website")
    else:
        score = 0.5
        reasons.append("General website")

    cited = int(extra.get("cited_by") or 0)
    if cited >= 100:
        score += 0.05
        reasons.append(f"Cited {cited} times")

    year = _parse_year(published)
    if year:
        age = date.today().year - year
        if age <= 1:
            score += 0.05
            reasons.append(f"Recent ({year})")
        elif age >= 6:
            score -= 0.1
            reasons.append(f"Older source ({year})")

    if 0 < text_len < 600:
        score -= 0.1
        reasons.append("Very little readable text")

    return round(max(0.05, min(score, 0.98)), 2), reasons
