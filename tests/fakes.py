"""A scripted stand-in for LLMClient so the whole pipeline runs offline."""
from __future__ import annotations

import json
import re
from collections import Counter

from nodexm import agents
from nodexm.evidence import numbers_in
from nodexm.llm import Usage
from nodexm.reader import Page
from nodexm.search import Hit

GOV = ("SMEs pay compliance costs of 6,000 euros per high-risk system per year under the AI Act. "
       "The regulation entered into force in August 2024 after adoption by the Parliament. ") * 3
NEWS = ("Reporting confirms SMEs pay compliance costs of 6,000 euros per high-risk system per year under the AI Act. "
        "Industry groups lobbied for simpler rules for small companies across the union. ") * 3
BLOG = ("Our analysis says SMEs pay compliance costs of 12,000 euros per high-risk system per year under the AI Act. "
        "Consultants expect demand for audits to grow quickly over the next two years. ") * 3
CHALLENGE = ("A later review found SMEs pay compliance costs of 6,000 euros per high-risk system per year under the "
             "AI Act, matching the original estimate made by the Commission impact assessment. ") * 3

PAGES = {
    "https://eur-lex.europa.eu/ai-act-costs": GOV,
    "https://www.reuters.com/ai-act-smes": NEWS,
    "https://someblog.medium.com/ai-act": BLOG,
    "https://copycat.example.com/ai-act": GOV + " Shared from the official site.",
    "https://www.ft.com/ai-act-review": CHALLENGE,
}


def fake_search(name, query, ctx):
    ctx.usage.add_search(1)
    if "against" in query or "criticism" in query:
        return [Hit(url="https://www.ft.com/ai-act-review", title="Review of AI Act costs", source_type="news",
                    searcher=name, published="2026-05-01")]
    return [
        Hit(url="https://eur-lex.europa.eu/ai-act-costs", title="AI Act impact assessment", source_type="regulatory",
            searcher=name, published="2025-02-01"),
        Hit(url="https://www.reuters.com/ai-act-smes", title="SMEs and the AI Act", source_type="news", searcher=name),
        Hit(url="https://someblog.medium.com/ai-act", title="What the AI Act really costs", searcher=name),
        Hit(url="https://copycat.example.com/ai-act", title="AI Act costs (copy)", searcher=name),
    ]


def fake_read(url):
    return Page(url=url, text=PAGES.get(url, ""), method="fake")


class FakeLLM:
    def __init__(self, critic_scores=(8,), report=None, verifier_down=False, leave_out=0):
        self.verifier_down = verifier_down
        self.leave_out = leave_out
        self.usage = Usage()
        self.critic_scores = list(critic_scores)
        self.report = report
        self.calls = []

    def embed(self, texts):
        return None  # forces the lexical fallback path

    def structured(self, tier, schema, system, user, *, agent="", temperature=0.2):
        self.calls.append(agent)
        self.usage.add_llm(tier, agent, 1000, 200)
        if schema is agents.RouteOut:
            return agents.RouteOut(mode="deep", standalone_question="What do SMEs pay to comply with the AI Act?",
                                   reason="Regulation question")
        if schema is agents.PlanOut:
            return agents.PlanOut(sub_questions=[
                agents.SubQuestion(question="SME compliance cost estimates for the AI Act",
                                   searchers=["regulatory", "news"])])
        if schema is agents.ExtractOut:
            out = []
            for number, text in re.findall(r"### Source (\d+): [^\n]*\n(.+?)(?=\n### Source |\Z)", user, re.S):
                first = text.strip().split(". ")[0] + "."
                out.append(agents.SourceClaims(source_number=int(number), claims=[
                    agents.ExtractedClaim(claim=first, quote=first, kind="figure")]))
            return agents.ExtractOut(sources=out)
        if schema is agents.GroupOut:
            if self.verifier_down:
                from nodexm.llm import LLMError
                raise LLMError("verifier model unavailable")
            statements = json.loads(user.split("Statements:\n", 1)[1])
            numbers = {st["id"]: frozenset(numbers_in(st["text"])) for st in statements}
            majority = Counter(numbers.values()).most_common(1)[0][0]
            ids = [st["id"] for st in statements][: len(statements) - self.leave_out]
            against = [i for i in ids if numbers[i] != majority]
            return agents.GroupOut(groups=[agents.FactGroup(
                statement_ids=ids,
                canonical_claim=("Estimates of SME compliance costs differ." if against
                                 else statements[0]["text"]),
                status="disputed" if against else "agreed",
                contradicting_ids=against,
                note="Most sources give 6,000 euros; one blog gives 12,000." if against else "")])
        if schema is agents.GapOut:
            return agents.GapOut(sufficient=True, queries=[])
        if schema is agents.ChallengeOut:
            return agents.ChallengeOut(queries=[agents.FollowUpQuery(
                query="evidence against the AI Act SME cost estimate", searcher="news", reason="check")])
        if schema is agents.FiguresOut:
            return agents.FiguresOut(figures=[])
        if schema is agents.CritiqueOut:
            score = self.critic_scores.pop(0) if self.critic_scores else 8
            return agents.CritiqueOut(accuracy=score, completeness=score, clarity=score, overall=score,
                                      issues=["Tighten the summary"] if score < 7 else [])
        raise AssertionError(f"unexpected schema {schema}")

    def text(self, tier, system, user, *, agent="", temperature=0.3):
        self.calls.append(agent)
        self.usage.add_llm(tier, agent, 2000, 400)
        return self.report or (
            "## Summary\nSMEs pay compliance costs of 6,000 euros per high-risk system per year [1, 2].\n\n"
            "## Where sources disagree\n**Disputed:** One analysis says SMEs pay 12,000 euros per high-risk "
            "system per year [3], while official figures give 6,000 euros [1].\n"
        )
