"""The LLM agents. Each function takes an LLMClient plus plain inputs and returns
typed output. Code-only roles (credibility, clustering, citation audit, change
detection) live in credibility.py, evidence.py and ledger.py."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from .llm import LLMClient

SearcherName = Literal["web", "academic", "regulatory", "news", "data"]

SEARCHER_GUIDE = (
    "web = general web search; academic = papers and abstracts (OpenAlex, arXiv); "
    "regulatory = laws, regulators and official guidance; news = recent reporting; "
    "data = official statistics and data series."
)


# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------
class RouteOut(BaseModel):
    mode: Literal["quick", "deep"]
    standalone_question: str = Field(description="The question rewritten to make sense without the conversation")
    reason: str


class SubQuestion(BaseModel):
    question: str
    searchers: list[SearcherName] = Field(description="1-2 searchers best suited to this sub-question")


class PlanOut(BaseModel):
    sub_questions: list[SubQuestion]


class ExtractedClaim(BaseModel):
    claim: str = Field(description="One self-contained, checkable statement")
    quote: str = Field(description="Exact words copied from the source that state this claim")
    kind: Literal["fact", "figure", "date", "forecast"]


class SourceClaims(BaseModel):
    source_number: int
    claims: list[ExtractedClaim]


class ExtractOut(BaseModel):
    sources: list[SourceClaims]


class FactGroup(BaseModel):
    statement_ids: list[str] = Field(description="Ids of the statements that assert this same fact")
    canonical_claim: str = Field(description="One precise sentence stating the fact, or the dispute")
    status: Literal["agreed", "disputed"]
    contradicting_ids: list[str] = Field(description="Ids of statements that conflict with the canonical claim")
    note: str = Field(description="For disputed: who says what. Otherwise empty or a short caveat")


class GroupOut(BaseModel):
    groups: list[FactGroup]


class FollowUpQuery(BaseModel):
    query: str
    searcher: SearcherName
    reason: str


class GapOut(BaseModel):
    sufficient: bool
    queries: list[FollowUpQuery]


class ChallengeOut(BaseModel):
    queries: list[FollowUpQuery]


class Figure(BaseModel):
    label: str = Field(description="What is measured, worded the same way for comparable figures")
    value: float
    unit: str = Field(description="Normalised unit, e.g. 'EUR', 'USD bn', '%', 'people'")
    period: str = Field(description="Year or period the figure refers to, or '' if unknown")
    cluster_id: str


class FiguresOut(BaseModel):
    figures: list[Figure]


class CritiqueOut(BaseModel):
    accuracy: int
    completeness: int
    clarity: int
    overall: int
    issues: list[str]

    def clamp(self) -> "CritiqueOut":
        for name in ("accuracy", "completeness", "clarity", "overall"):
            setattr(self, name, max(1, min(10, int(getattr(self, name)))))
        self.issues = self.issues[:6]
        return self


# ---------------------------------------------------------------------------
# 1. Router and clarifier
# ---------------------------------------------------------------------------
def route(llm: LLMClient, question: str, context: str, allow_deep: bool) -> RouteOut:
    system = (
        "You route research questions. Pick 'quick' for a single fact, definition or simple lookup. "
        "Pick 'deep' for comparisons, regulation, markets, forecasts, contested topics or anything that needs "
        "several kinds of sources. Also rewrite the question so it stands alone: resolve pronouns and "
        "follow-ups using the conversation so far, keep the user's intent, do not add new scope."
        + ("" if allow_deep else " Deep mode is not available for this user, so always pick 'quick'.")
    )
    user = f"Conversation so far:\n{context or '(none)'}\n\nNew question: {question}"
    out = llm.structured("lite", RouteOut, system, user, agent="router")
    if not allow_deep:
        out.mode = "quick"
    return out


# ---------------------------------------------------------------------------
# 2. Planner
# ---------------------------------------------------------------------------
def plan(llm: LLMClient, question: str, mode: str, profile: dict) -> PlanOut:
    n = "2 to 3" if mode == "quick" else "4 to 5"
    system = (
        f"You plan research. Break the question into {n} specific sub-questions that together answer it. "
        "Each must be answerable by searching, and they should not overlap. Assign each to the searchers "
        f"most likely to find primary evidence. Searchers: {SEARCHER_GUIDE} "
        "Prefer regulatory and data searchers for official figures, academic for research findings."
    )
    user = f"Question: {question}\nRegion focus: {profile.get('region', 'Global')}\nDepth: {profile.get('depth', 'analyst')}"
    out = llm.structured("lite", PlanOut, system, user, agent="planner")
    limit = 3 if mode == "quick" else 5
    out.sub_questions = [sq for sq in out.sub_questions if sq.question.strip()][:limit]
    for sq in out.sub_questions:
        sq.searchers = list(dict.fromkeys(sq.searchers))[:2] or ["web"]
    return out


# ---------------------------------------------------------------------------
# 4. Reader and extractor
# ---------------------------------------------------------------------------
def extract(llm: LLMClient, question: str, sources: list[dict], max_claims: int) -> ExtractOut:
    system = (
        "You extract evidence from sources for a research question. For each source, list up to "
        f"{max_claims} checkable factual claims that are relevant to the question: facts, figures, dates, "
        "official forecasts. For every claim copy a short exact quote (one sentence, 10 to 45 words) from the "
        "source text that states it. Copy the quote character for character; never paraphrase or merge "
        "sentences in the quote. Write the claim itself as one self-contained sentence that names its subject, "
        "and keep units, currency and the period. Skip opinions, marketing, navigation text and anything "
        "not stated in the text. If a source is irrelevant, return an empty claims list for it."
    )
    blocks = [f"### Source {s['number']}: {s['title']}\n{s['text']}" for s in sources]
    user = f"Research question: {question}\n\n" + "\n\n".join(blocks)
    return llm.structured("lite", ExtractOut, system, user, agent="extractor", temperature=0.0)


# ---------------------------------------------------------------------------
# 7. Verifier
# ---------------------------------------------------------------------------
def group_and_verify(llm: LLMClient, question: str, statements: list[dict]) -> GroupOut:
    system = (
        "You verify research evidence. You get numbered statements, each extracted from a source.\n"
        "1. Group the statements that assert the SAME specific fact: the same measure or event, the same "
        "subject, and the same time period. Keep different years, different measures (for example total "
        "consumption in TWh versus share of demand in %), different regions and different subjects in separate "
        "groups. A statement with nothing in common with the others is its own group.\n"
        "2. Within each group decide the status. 'agreed' when the statements are compatible (rounding, wording "
        "or extra detail is fine). 'disputed' when sources give incompatible values beyond rounding, opposite "
        "conclusions, or different estimates or forecasts of the same quantity for the same period. List the "
        "statements that conflict with your canonical claim in contradicting_ids. Never invent a dispute.\n"
        "3. canonical_claim: one precise, self-contained sentence with units and period. For a disputed group "
        "state the disagreement neutrally, for example 'Forecasts of X for 2030 differ: A projects 945 TWh, B "
        "projects 1,065 TWh.' and say who says what in note.\n"
        "Every statement id must appear in exactly one group."
    )
    user = f"Research question: {question}\n\nStatements:\n{json.dumps(statements, ensure_ascii=False, indent=1)}"
    return llm.structured("flash", GroupOut, system, user, agent="verifier", temperature=0.0)


# ---------------------------------------------------------------------------
# 8. Gap finder and 9. Devil's advocate
# ---------------------------------------------------------------------------
def find_gaps(llm: LLMClient, question: str, coverage: list[dict]) -> GapOut:
    system = (
        "You review research coverage. Given each sub-question and the verified claims found for it, decide "
        "whether the evidence is sufficient to answer the main question well. If not, propose at most 3 new, "
        "specific search queries aimed at the weakest areas, each with the best searcher. "
        f"Searchers: {SEARCHER_GUIDE} Do not repeat queries that were already run."
    )
    user = f"Main question: {question}\n\nCoverage:\n{json.dumps(coverage, ensure_ascii=False, indent=1)}"
    out = llm.structured("lite", GapOut, system, user, agent="gap_finder")
    out.queries = out.queries[:3]
    return out


def challenge(llm: LLMClient, question: str, claims: list[str]) -> ChallengeOut:
    system = (
        "You are a devil's advocate. Write at most 3 search queries that test the most important claims. "
        "For an AGREED claim, look for credible evidence against it: newer figures, official corrections, "
        "critiques, or studies with different results. For a DISPUTED claim, look for the authoritative source "
        f"that would settle it. Pick the best searcher for each. Searchers: {SEARCHER_GUIDE}"
    )
    user = f"Question: {question}\n\nClaims:\n" + "\n".join(f"- {c}" for c in claims)
    out = llm.structured("lite", ChallengeOut, system, user, agent="devils_advocate")
    out.queries = out.queries[:3]
    return out


# ---------------------------------------------------------------------------
# 10. Quant analyst
# ---------------------------------------------------------------------------
def figures(llm: LLMClient, question: str, numeric_claims: list[dict]) -> FiguresOut:
    system = (
        "You are a quantitative analyst. From the verified claims, pull out the figures that matter for the "
        "question. Normalise units and currencies consistently (state the unit you used), keep the period, and "
        "give comparable figures the exact same label so they can be compared. Only use numbers that appear in "
        "the claims; do not calculate new ones. At most 12 figures."
    )
    user = f"Question: {question}\n\nClaims:\n{json.dumps(numeric_claims, ensure_ascii=False, indent=1)}"
    out = llm.structured("flash", FiguresOut, system, user, agent="quant_analyst", temperature=0.0)
    out.figures = out.figures[:12]
    return out


# ---------------------------------------------------------------------------
# 11. Writer
# ---------------------------------------------------------------------------
WRITER_RULES = (
    "You write research answers for professionals. Rules:\n"
    "1. Use only the verified claims and figures provided. Never add facts from memory.\n"
    "2. Cite sources inline with their numbers in square brackets, e.g. [2] or [1, 4], right after the "
    "sentence they support. Every sentence with a fact or number needs a citation.\n"
    "3. For a disputed claim, write a sentence starting with **Disputed:** that gives each side with its "
    "own citation. Never merge or average conflicting figures.\n"
    "4. Hedge single-source claims (\"According to [3], ...\"). State agreed, well-supported claims plainly.\n"
    "5. Copy numbers exactly as they appear in the claims, with units and periods.\n"
    "6. No preamble, no closing remarks, no Sources section (it is added automatically).\n"
    "7. Write clear, neutral, specific prose. Use markdown headings (##), short paragraphs and bullet lists."
)


def write(llm: LLMClient, *, question: str, mode: str, profile: dict, claims_block: str, sources_block: str,
          figures_block: str, feedback: str) -> str:
    if mode == "quick":
        structure = (
            "Structure:\n## Answer\nA direct answer in 2-4 sentences.\n"
            "## Key points\n3-6 bullets.\n"
            "## Where sources disagree\nOnly if there are disputed claims.\n"
        )
    else:
        structure = (
            "Structure:\n## Summary\n3-5 sentences, including the biggest open dispute if any.\n"
            "## Key findings\nGrouped by theme with ### subheadings where useful.\n"
            "## Where sources disagree\nEach disputed claim, both sides, with citations. Say 'No material "
            "disagreements found.' if there are none.\n"
            "## Key figures\nA markdown table (Figure | Value | Period | Source) when figures are provided.\n"
            "## What is still uncertain\nSingle-source claims that matter and gaps in the evidence.\n"
        )
    depth = {"summary": "Keep it brief and non-technical.",
             "technical": "Include technical detail and precise terminology."}.get(profile.get("depth", ""),
                                                                                   "Write for an analyst.")
    user = (
        f"Question: {question}\nRegion focus: {profile.get('region', 'Global')}\n{depth}\n\n{structure}\n"
        f"Sources:\n{sources_block}\n\nVerified claims:\n{claims_block}\n\n"
        f"Key figures:\n{figures_block or '(none)'}\n\n"
        f"Reviewer feedback to fix in this revision:\n{feedback or '(first draft)'}"
    )
    tier = "lite" if mode == "quick" else "flash"
    return llm.text(tier, WRITER_RULES, user, agent="writer")


def rewrite_section(llm: LLMClient, *, question: str, section_md: str, instruction: str, claims_block: str,
                    sources_block: str) -> str:
    user = (
        f"Question: {question}\n\nRewrite ONLY this section. Keep its heading line exactly as it is.\n"
        f"Instruction: {instruction or 'Make it clearer and tighter, keep every citation.'}\n\n"
        f"Section:\n{section_md}\n\nSources:\n{sources_block}\n\nVerified claims:\n{claims_block}"
    )
    return llm.text("flash", WRITER_RULES, user, agent="section_rewriter")


# ---------------------------------------------------------------------------
# 12. Critic
# ---------------------------------------------------------------------------
def critique(llm: LLMClient, question: str, report: str, claims_block: str) -> CritiqueOut:
    system = (
        "You review a research answer against the verified claims it was written from. Score 1-10 for "
        "accuracy (matches the claims, disputes shown honestly), completeness (answers the question), and "
        "clarity, plus an overall score. List specific, actionable issues (at most 6). Be strict but fair."
    )
    user = f"Question: {question}\n\nVerified claims:\n{claims_block}\n\nAnswer:\n{report}"
    return llm.structured("lite", CritiqueOut, system, user, agent="critic", temperature=0.0).clamp()
