"""Evidence engine: quote locating, duplicate detection, claim clustering,
confidence and citation auditing. Pure Python, no LLM calls."""
from __future__ import annotations

import hashlib
import math
import re
from difflib import SequenceMatcher

import numpy as np

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         " ": " "})
WORD = re.compile(r"[a-z0-9][a-z0-9\-\.%']*")
STOP = set(
    "the a an and or of to in on for with by from at as is are was were be been being this that these those it its "
    "their there which who whom what when where how than then also such into over under about between among per "
    "has have had not no but can could may might will would should shall more most less least very".split()
)


# ---------------------------------------------------------------------------
# Quote locating
# ---------------------------------------------------------------------------
def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Lowercase, unify quotes, collapse whitespace; keep a map back to original offsets."""
    out, index = [], []
    prev_space = True
    for i, ch in enumerate(text.translate(_QUOTES)):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            index.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            index.append(i)
            prev_space = False
    return "".join(out), index


def locate_quote(text: str, quote: str, min_score: float = 0.8) -> tuple[int, int, float]:
    """Find where `quote` appears in `text`. Returns (start, end, score); (-1, -1, score) when not found.

    An exact match scores 1.0. Otherwise the best fuzzy window around the longest
    common block is scored with SequenceMatcher, so small edits by the LLM still match
    but invented quotes do not.
    """
    if not text or not quote or len(quote.strip()) < 8:
        return -1, -1, 0.0
    ntext, idx = _normalize_with_map(text)
    nquote, _ = _normalize_with_map(quote.strip().strip('"').strip())
    if not nquote:
        return -1, -1, 0.0
    pos = ntext.find(nquote)
    if pos >= 0:
        return idx[pos], idx[pos + len(nquote) - 1] + 1, 1.0

    sm = SequenceMatcher(None, ntext, nquote, autojunk=False)
    block = sm.find_longest_match(0, len(ntext), 0, len(nquote))
    if block.size < 6:
        return -1, -1, 0.0
    best = (0.0, 0, 0)
    anchor = block.a - block.b
    for shift in (-12, -6, 0, 6, 12):
        for extra in (-8, 0, 8):
            start = max(0, anchor + shift)
            end = min(len(ntext), start + len(nquote) + extra)
            if end <= start:
                continue
            ratio = SequenceMatcher(None, ntext[start:end], nquote, autojunk=False).ratio()
            if ratio > best[0]:
                best = (ratio, start, end)
    score, start, end = best
    if score < min_score:
        return -1, -1, round(score, 3)
    return idx[start], idx[end - 1] + 1, round(score, 3)


# ---------------------------------------------------------------------------
# Duplicates ("copies don't count")
# ---------------------------------------------------------------------------
def _shingles(text: str, size: int = 6, limit: int = 6000) -> set[int]:
    words = WORD.findall(text[:limit].lower())
    return {hash(" ".join(words[i:i + size])) for i in range(max(0, len(words) - size + 1))}


def find_duplicates(texts: dict[int, str], credibility: dict[int, float], threshold: float = 0.45,
                    containment: float = 0.5) -> dict[int, int]:
    """Map source idx -> idx of the source it copies. The more credible (then earlier) source is kept.

    Two pages are copies when they share most of their wording (Jaccard), or when one contains
    most of the other (a press release republished with an extra paragraph on top).
    """
    order = sorted(texts, key=lambda i: (-credibility.get(i, 0.5), i))
    sh = {i: _shingles(texts[i]) for i in order}
    kept: list[int] = []
    dups: dict[int, int] = {}
    for i in order:
        a = sh[i]
        if len(a) < 20:
            kept.append(i)
            continue
        for k in kept:
            b = sh[k]
            if len(b) < 20:
                continue
            common = len(a & b)
            if common / len(a | b) >= threshold or common / min(len(a), len(b)) >= containment:
                dups[i] = k
                break
        else:
            kept.append(i)
    return dups


# ---------------------------------------------------------------------------
# Independence ("ten reprints of one wire story are one source")
# ---------------------------------------------------------------------------
# News agencies and press-release wires whose stories are widely republished.
WIRES: dict[str, list[str]] = {
    "Reuters": ["reuters"],
    "Associated Press": ["associated press", "ap news", "ap"],
    "AFP": ["afp", "agence france-presse", "agence france presse"],
    "Bloomberg": ["bloomberg news"],
    "PTI": ["press trust of india", "pti"],
    "ANI": ["asian news international", "ani"],
    "IANS": ["ians"],
    "dpa": ["dpa", "deutsche presse-agentur"],
    "Xinhua": ["xinhua"],
    "Kyodo": ["kyodo"],
    "PA Media": ["pa media", "press association"],
    "Business Wire": ["business wire", "businesswire"],
    "PR Newswire": ["pr newswire", "prnewswire"],
    "GlobeNewswire": ["globenewswire", "globe newswire"],
    "Accesswire": ["accesswire"],
}
WIRE_DOMAINS = {"reuters.com": "Reuters", "apnews.com": "Associated Press", "afp.com": "AFP", "ptinews.com": "PTI",
                "aninews.in": "ANI", "ians.in": "IANS", "dpa.com": "dpa", "xinhuanet.com": "Xinhua",
                "english.kyodonews.net": "Kyodo", "businesswire.com": "Business Wire", "prnewswire.com": "PR Newswire",
                "globenewswire.com": "GlobeNewswire", "accesswire.com": "Accesswire"}
_ATTRIBUTION = r"(?:according to|reported by|reporting by|told|via|citing|source:|said in a statement to|contributed to|\(|—|–|-)"
_WIRE_RE = {
    name: re.compile(
        r"(?:" + _ATTRIBUTION + r"\s*(?:the\s+)?(?:" + "|".join(re.escape(a) for a in aliases) + r")\b)"
        + r"|(?:\b(?:" + "|".join(re.escape(a) for a in aliases) + r")\s+(?:reported|reports|report|said|says|news agency|wire|analysis)\b)"
        + r"|(?:\((?:" + "|".join(re.escape(a) for a in aliases) + r")\))",
        re.IGNORECASE,
    )
    for name, aliases in WIRES.items()
}
GENERIC_LABELS = {"www", "news", "blog", "com", "org", "net", "gov", "edu", "info", "online", "media", "global",
                  "home", "site", "web", "data", "report", "reports", "press", "times", "daily", "post", "the"}


def registrable_label(domain: str) -> str:
    """'digital-strategy.ec.europa.eu' -> 'europa', 'www.iea.org' -> 'iea', 'bbc.co.uk' -> 'bbc'."""
    parts = [p for p in domain.lower().split(".") if p and p != "www"]
    if len(parts) >= 3 and parts[-2] in ("co", "com", "ac", "gov", "org", "net", "edu") and len(parts[-1]) == 2:
        parts = parts[:-2]
    elif len(parts) >= 2:
        parts = parts[:-1]
    return parts[-1] if parts else ""


def source_origin(text: str, domain: str) -> str | None:
    """The news wire a page republishes, if any. A wire's own site is its own origin."""
    domain = domain.lower().removeprefix("www.")
    for wire_domain, name in WIRE_DOMAINS.items():
        if domain == wire_domain or domain.endswith("." + wire_domain):
            return name
    head = text[:4000]
    tail = text[-1500:] if len(text) > 4000 else ""
    for name, pattern in _WIRE_RE.items():
        if pattern.search(head) or (tail and pattern.search(tail)):
            return name
    return None


def _norm_quote(q: str) -> str:
    return " ".join(q.lower().translate(_QUOTES).split())


def independence_groups(members: list[dict], sources: dict[int, dict], *, quote_ratio: float = 0.9,
                        window: int = 320) -> tuple[list[list[int]], list[str]]:
    """Group the sources behind one claim into independent groups.

    members: evidence items with source_idx, quote, start, end.
    sources: idx -> source dict with domain, text, origin, credibility.
    Two sources are dependent when (a) both republish the same wire story, (b) they quote the claim in
    near-identical words, or (c) one attributes the claim to the other ("the IEA estimates..." when
    iea.org is also a source). Returns the groups (lists of source idx) and human-readable reasons.
    """
    idxs = sorted({m["source_idx"] for m in members})
    parent = {i: i for i in idxs}
    reasons: list[str] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int, why: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
            if why not in reasons:
                reasons.append(why)

    # (a) same wire origin
    by_origin: dict[str, list[int]] = {}
    for i in idxs:
        origin = sources[i].get("origin")
        if origin:
            by_origin.setdefault(origin, []).append(i)
    for origin, group in by_origin.items():
        for other in group[1:]:
            union(group[0], other, f"{len(group)} sites carry the same {origin} story")

    # (b) near-identical quotes from different sources
    quotes = [(m["source_idx"], _norm_quote(m.get("quote", ""))) for m in members if len(m.get("quote", "")) >= 60]
    for x in range(len(quotes)):
        for y in range(x + 1, len(quotes)):
            (ia, qa), (ib, qb) = quotes[x], quotes[y]
            if ia != ib and SequenceMatcher(None, qa, qb, autojunk=False).ratio() >= quote_ratio:
                union(ia, ib, "sites repeat the same sentence word for word")

    # (c) one source attributes the claim to another source in the same group
    labels = {i: registrable_label(sources[i].get("domain", "")) for i in idxs}
    for m in members:
        a = m["source_idx"]
        text = sources[a].get("text", "")
        start, end = m.get("start", -1), m.get("end", -1)
        if start < 0 or not text:
            continue
        context = text[max(0, start - window):end + window].lower()
        for b in idxs:
            lb = labels[b]
            if b == a or len(lb) < 3 or lb in GENERIC_LABELS or lb == labels[a]:
                continue
            if re.search(r"\b" + re.escape(lb) + r"\b", context):
                union(a, b, f"{sources[a].get('domain', '')} cites {sources[b].get('domain', '')}")

    groups: dict[int, list[int]] = {}
    for i in idxs:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values()), reasons


# ---------------------------------------------------------------------------
# Vectors and clustering
# ---------------------------------------------------------------------------
def tokens(text: str) -> list[str]:
    return [w.strip(".'") for w in WORD.findall(text.lower().translate(_QUOTES)) if w.strip(".'") not in STOP]


LEXICAL_DIM = 2048


def lexical_vectors(texts: list[str], dim: int = LEXICAL_DIM) -> list[list[float]]:
    """Hashed bag of words + bigrams. Used when embeddings are unavailable."""
    out = []
    for t in texts:
        v = np.zeros(dim, dtype=np.float32)
        toks = tokens(t)
        feats = toks + [a + " " + b for a, b in zip(toks, toks[1:])]
        for f in feats:
            h = int(hashlib.md5(f.encode()).hexdigest()[:8], 16)
            v[h % dim] += 1.0 if " " not in f else 0.5
        n = np.linalg.norm(v)
        out.append((v / n if n else v).tolist())
    return out


def cosine(a, b) -> float:
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def cluster(vectors: list[list[float]], threshold: float) -> list[list[int]]:
    """Greedy centroid clustering. Returns groups of indices into `vectors`."""
    groups: list[list[int]] = []
    centroids: list[np.ndarray] = []
    for i, v in enumerate(vectors):
        vec = np.asarray(v, dtype=np.float32)
        best, best_sim = -1, threshold
        for g, c in enumerate(centroids):
            sim = cosine(vec, c)
            if sim >= best_sim:
                best, best_sim = g, sim
        if best < 0:
            groups.append([i])
            centroids.append(vec.copy())
        else:
            groups[best].append(i)
            n = len(groups[best])
            centroids[best] = centroids[best] * ((n - 1) / n) + vec / n
    return groups


YEAR_RE = re.compile(r"\b(19[5-9]\d|20\d\d|21[0-4]\d)\b")


def years_in(text: str) -> set[str]:
    return set(YEAR_RE.findall(text))


def periods_compatible(a: str, b: str) -> bool | None:
    """False when both texts name years and none overlap (different periods, so different facts).
    True when they share a year, None when at least one names no year."""
    ya, yb = years_in(a), years_in(b)
    if ya and yb:
        return bool(ya & yb)
    return None


def group_claims(texts: list[str], vectors: list[list[float]], lexical: bool) -> list[list[int]]:
    """Fallback fact grouping when the verifier model is unavailable.

    Embeddings rate every claim on a narrow topic as very similar (0.9+), so a plain threshold
    merges different facts. This uses complete linkage (similar to every member, not to a drifting
    centroid), never merges claims about different years, and asks for more similarity when a
    claim names no year.
    """
    same_year, no_year = (0.5, 0.6) if lexical else (0.94, 0.975)
    vecs = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.where(norms == 0, 1, norms)
    sims = vecs @ vecs.T

    def affinity(i: int, j: int) -> float:
        compatible = periods_compatible(texts[i], texts[j])
        if compatible is False:
            return 0.0
        need = same_year if compatible else no_year
        return float(sims[i, j]) if sims[i, j] >= need else 0.0

    groups: list[list[int]] = []
    for i in range(len(texts)):
        best, best_score = None, 0.0
        for g in groups:
            score = min(affinity(i, j) for j in g)
            if score > best_score:
                best, best_score = g, score
        if best is None:
            groups.append([i])
        else:
            best.append(i)
    return groups


def pack_batches(vectors: list[list[float]], limit: int, lexical: bool) -> list[list[int]]:
    """Split many claims into verifier batches of at most `limit`, keeping each topic together."""
    n = len(vectors)
    if n <= limit:
        return [list(range(n))]
    topics = sorted(cluster(vectors, 0.3 if lexical else 0.9), key=len, reverse=True)
    batches: list[list[int]] = []
    for topic in topics:
        for k in range(0, len(topic), limit):
            part = topic[k:k + limit]
            for b in batches:
                if len(b) + len(part) <= limit:
                    b.extend(part)
                    break
            else:
                batches.append(list(part))
    return batches


def centroid(vectors: list[list[float]]) -> list[float]:
    m = np.mean(np.asarray(vectors, dtype=np.float32), axis=0)
    n = np.linalg.norm(m)
    return (m / n if n else m).tolist()


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------
def confidence(status: str, supporting: list[float], contradicting: list[float] | None = None) -> float:
    """Combine independent sources' credibility into a 0..1 confidence."""
    contradicting = contradicting or []
    if not supporting:
        return 0.0
    if status == "single_source":
        return round(0.25 + 0.4 * max(supporting), 2)
    agree = 1 - math.prod(1 - 0.6 * c for c in supporting)
    if status == "disputed":
        against = 1 - math.prod(1 - 0.6 * c for c in contradicting) if contradicting else 0.3
        return round(max(0.05, min(0.6, agree * (1 - against))), 2)
    return round(min(0.98, 0.3 + 0.7 * agree), 2)


# ---------------------------------------------------------------------------
# Citation audit
# ---------------------------------------------------------------------------
CITE = re.compile(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:\s?%)?")
YEAR = re.compile(r"^(19|20)\d{2}$")


def parse_citation(group: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*,\s*", group):
        if re.search(r"[–-]", part):
            a, b = re.split(r"\s*[–-]\s*", part)[:2]
            if a.isdigit() and b.isdigit() and int(b) - int(a) < 20:
                out.extend(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def numbers_in(text: str) -> set[str]:
    nums = set()
    for m in NUMBER.finditer(text):
        n = m.group(1).replace(",", "")
        if YEAR.match(n) or n in ("0", "1"):
            continue
        nums.add(n.rstrip("0").rstrip(".") if "." in n else n)
    return nums


def _sentences(md: str) -> list[str]:
    body = []
    for line in md.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "|", "```", ">")) or re.match(r"^-{3,}$", s):
            continue
        body.append(re.sub(r"^[-*+]\s+|^\d+\.\s+", "", s))
    text = " ".join(body)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z*\[])", text) if len(s.strip()) > 20]


def audit_citations(report_md: str, source_texts: dict[int, str], quotes: dict[int, list[str]]) -> dict:
    """Check each cited sentence against the sources it cites.

    Citation numbers are 1-based ([1] is source idx 0). A sentence is supported when
    every number in it appears in a cited source and enough of its content words do.
    """
    checked = supported = 0
    issues = []
    for sentence in _sentences(report_md):
        cites = [c for g in CITE.findall(sentence) for c in parse_citation(g)]
        clean = CITE.sub("", sentence)
        nums = numbers_in(clean)
        if not cites:
            if nums:
                issues.append({"sentence": sentence[:300], "citations": [], "problem": "Figure without a citation"})
            continue
        checked += 1
        valid = [c - 1 for c in cites if (c - 1) in source_texts]
        if len(valid) < len(set(cites)):
            issues.append({"sentence": sentence[:300], "citations": cites,
                           "problem": "Cites a source number that does not exist"})
            if not valid:
                continue
        haystack = " ".join((source_texts[i] + " " + " ".join(quotes.get(i, []))).lower().translate(_QUOTES)
                            for i in valid)
        hay_nums = numbers_in(haystack)
        missing_nums = [n for n in nums if n not in hay_nums]
        words = {t for t in tokens(clean) if len(t) > 3 and not t[0].isdigit()}
        hay_words = set(tokens(haystack))
        overlap = len(words & hay_words) / len(words) if words else 1.0
        if missing_nums:
            issues.append({"sentence": sentence[:300], "citations": cites,
                           "problem": f"Number not found in cited source: {', '.join(sorted(missing_nums)[:4])}"})
        elif overlap < 0.35:
            issues.append({"sentence": sentence[:300], "citations": cites,
                           "problem": "Cited source does not appear to support this sentence"})
        else:
            supported += 1
    score = round(supported / checked, 3) if checked else 0.0
    return {"score": score, "checked": checked, "supported": supported, "issues": issues[:25]}
