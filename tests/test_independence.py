from nodexm import pipeline
from nodexm.evidence import find_duplicates, independence_groups, source_origin
from nodexm.pipeline import RunContext, run_pipeline
from nodexm.reader import Page
from nodexm.search import Hit, SearchContext

from .fakes import FakeLLM


def src(domain, text, cred=0.75):
    return {"domain": domain, "text": text, "origin": source_origin(text, domain), "credibility": cred}


def member(idx, sources, quote):
    text = sources[idx]["text"]
    start = text.index(quote)
    return {"source_idx": idx, "quote": quote, "start": start, "end": start + len(quote)}


def test_wire_copies_count_as_one_source():
    sources = {
        0: src("reuters.com", "Acme raised $50 million in a funding round led by Northwind, the company said on Monday."),
        1: src("thestar.com", "TORONTO (Reuters) - Acme has secured $50 million in new capital from investors led by Northwind."),
        2: src("yahoo.com", "Startup Acme pulled in $50 million this week, according to Reuters, in a round led by Northwind."),
    }
    members = [
        member(0, sources, "Acme raised $50 million in a funding round led by Northwind"),
        member(1, sources, "Acme has secured $50 million in new capital from investors led by Northwind"),
        member(2, sources, "Startup Acme pulled in $50 million this week"),
    ]
    groups, reasons = independence_groups(members, sources)
    assert len(groups) == 1
    assert any("Reuters" in r for r in reasons)


def test_a_source_citing_another_source_is_not_independent():
    sources = {
        0: src("iea.org", "Data centres consumed around 415 TWh of electricity in 2024, about 1.5% of global demand.", 0.9),
        1: src("energynews.example", "Global data centres used roughly 415 terawatt-hours in 2024, the IEA estimates in its latest report."),
    }
    members = [
        member(0, sources, "Data centres consumed around 415 TWh of electricity in 2024"),
        member(1, sources, "Global data centres used roughly 415 terawatt-hours in 2024"),
    ]
    groups, reasons = independence_groups(members, sources)
    assert len(groups) == 1 and "energynews.example cites iea.org" in reasons


def test_word_for_word_repeats_are_not_independent():
    sentence = "The regulation requires providers of general-purpose AI models to publish a summary of their training data."
    sources = {0: src("blog-one.example", "Intro. " + sentence), 1: src("blog-two.example", "News: " + sentence)}
    groups, _ = independence_groups([member(0, sources, sentence), member(1, sources, sentence)], sources)
    assert len(groups) == 1


def test_genuinely_independent_sources_stay_separate():
    sources = {
        0: src("ft.com", "Our survey of 40 lenders found mortgage approvals fell 12% in October."),
        1: src("nature.com", "A separate dataset of bank filings shows approvals dropped by 12% in October."),
    }
    members = [
        member(0, sources, "Our survey of 40 lenders found mortgage approvals fell 12% in October"),
        member(1, sources, "A separate dataset of bank filings shows approvals dropped by 12% in October"),
    ]
    groups, _ = independence_groups(members, sources)
    assert len(groups) == 2


def test_republished_press_release_is_a_duplicate():
    release = " ".join(f"Acme announced product line {i} with new features for enterprise customers today." for i in range(40))
    texts = {0: release, 1: "Breaking: our editors add a short intro before the release. " + release + " More soon."}
    assert find_duplicates(texts, {0: 0.6, 1: 0.5}) == {1: 0}


WIRE_PAGES = {
    "https://www.reuters.com/acme-funding": "Acme raised $50 million in a funding round led by Northwind, the company said on Monday. " * 3,
    "https://www.thestar.com/acme": "TORONTO (Reuters) - Acme has secured $50 million in new capital from investors led by Northwind. " * 3,
    "https://finance.yahoo.com/acme": "Startup Acme pulled in $50 million this week, according to Reuters, in a round led by Northwind. " * 3,
}


def test_pipeline_downgrades_agreement_that_rests_on_one_wire_story(monkeypatch):
    monkeypatch.setattr(pipeline, "run_searcher", lambda name, q, c: [Hit(url=u, title="Acme funding", searcher=name) for u in WIRE_PAGES])
    monkeypatch.setattr(pipeline, "read", lambda url: Page(url=url, text=WIRE_PAGES[url], method="fake"))
    monkeypatch.setattr(pipeline, "search_available", lambda key: True)
    llm = FakeLLM(report="## Answer\nAcme raised $50 million in a funding round led by Northwind [1].\n")
    events = []
    ctx = RunContext(run_id="w1", llm=llm, search=SearchContext(usage=llm.usage, tavily_key="x"),
                     emit=lambda node, status, summary, extra=None: events.append((node, summary)))
    result = run_pipeline(ctx, question="How much did Acme raise?", requested_mode="quick")

    assert {s["origin"] for s in result["sources"]} == {"Reuters"}
    claim = result["clusters"][0]
    assert claim["status"] == "single_source"
    assert claim["independence"] == {**claim["independence"], "independent": 1, "sites": 3}
    assert "one original source (Reuters)" in claim["note"]
    assert any(n == "verifier" and "rest on one original source" in s for n, s in events)
