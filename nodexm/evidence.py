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


def find_duplicates(texts: dict[int, str], credibility: dict[int, float], threshold: float = 0.45) -> dict[int, int]:
    """Map source idx -> idx of the source it copies. The more credible (then earlier) source is kept."""
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
            if b and len(a & b) / len(a | b) >= threshold:
                dups[i] = k
                break
        else:
            kept.append(i)
    return dups


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
