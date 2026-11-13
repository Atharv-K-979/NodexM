import pytest

from nodexm import pipeline
from nodexm.llm import Usage
from nodexm.pipeline import RunContext, run_pipeline
from nodexm.search import SearchContext

from .fakes import FakeLLM, fake_read, fake_search


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(pipeline, "run_searcher", fake_search)
    monkeypatch.setattr(pipeline, "read", fake_read)
    monkeypatch.setattr(pipeline, "search_available", lambda key: True)


def make_ctx(llm):
    events = []
    ctx = RunContext(run_id="t1", llm=llm, search=SearchContext(usage=llm.usage, tavily_key="x"),
                     emit=lambda node, status, summary, extra=None: events.append((node, status, summary)))
    return ctx, events


def test_deep_run_end_to_end():
    llm = FakeLLM()
    ctx, events = make_ctx(llm)
    result = run_pipeline(ctx, question="what do smes pay?", requested_mode="auto")

    assert result["mode"] == "deep"
    by_url = {s["url"]: s for s in result["sources"]}
    # the copy of the official page is set aside, not counted as agreement
    assert by_url["https://copycat.example.com/ai-act"]["duplicate_of"] == by_url["https://eur-lex.europa.eu/ai-act-costs"]["idx"]
    # the devil's advocate round found and read the counter-evidence search result
    assert "https://www.ft.com/ai-act-review" in by_url

    statuses = {c["status"] for c in result["clusters"]}
    assert "disputed" in statuses
    disputed = next(c for c in result["clusters"] if c["status"] == "disputed")
    assert {e["stance"] for e in disputed["evidence"]} == {"supports", "contradicts"}
    for e in disputed["evidence"]:
        src = result["sources"][e["source_idx"]]
        assert src["text"][e["start"]:e["end"]] == e["quote"]

    assert result["audit"]["checked"] == 2 and result["audit"]["score"] == 1.0
    assert "## Sources" in result["report_md"] and "<h2>" in result["report_html"]
    nodes = {n for n, _, _ in events}
    assert {"router", "planner", "searcher", "reader", "credibility", "extractor", "clusterer", "verifier",
            "gap_finder", "devils_advocate", "writer", "citation_auditor", "critic", "finalize"} <= nodes
    # Deep mode verifies before the devil's advocate and once more after it, not every round
    assert llm.calls.count("verifier") == 2
    assert llm.usage.cost() > 0


def test_low_critic_score_triggers_one_revision_in_quick_mode():
    llm = FakeLLM(critic_scores=(4, 4, 4))
    ctx, _ = make_ctx(llm)
    result = run_pipeline(ctx, question="q", requested_mode="quick")
    assert result["mode"] == "quick"
    assert result["revisions"] == 1
    assert llm.calls.count("writer") == 2
    assert "router" not in llm.calls  # explicit mode and no context: no router call


def test_deep_falls_back_to_quick_without_allowance():
    ctx, events = make_ctx(FakeLLM())
    result = run_pipeline(ctx, question="q", requested_mode="deep", allow_deep=False)
    assert result["mode"] == "quick"
    assert any(s == "warning" for _, s, _ in events)


def test_unsupported_numbers_are_flagged_by_the_audit():
    report = "## Answer\nSMEs pay compliance costs of 45,000 euros per high-risk system per year [1].\n"
    ctx, _ = make_ctx(FakeLLM(report=report))
    result = run_pipeline(ctx, question="q", requested_mode="quick")
    assert result["audit"]["score"] == 0.0
    assert "45000" in result["audit"]["issues"][0]["problem"]


def test_statements_the_verifier_leaves_out_are_kept_as_single_source():
    ctx, _ = make_ctx(FakeLLM(leave_out=1))
    result = run_pipeline(ctx, question="q", requested_mode="quick")
    statuses = sorted(c["status"] for c in result["clusters"])
    assert "single_source" in statuses and len(result["clusters"]) == 2


def test_verifier_outage_falls_back_to_automatic_grouping():
    ctx, events = make_ctx(FakeLLM(verifier_down=True))
    result = run_pipeline(ctx, question="q", requested_mode="quick")
    assert result["clusters"]
    assert any(n == "verifier" and s == "warning" for n, s, _ in events)
    # the number check still spots the conflicting figure
    assert any(c["status"] == "disputed" for c in result["clusters"])


def test_figure_ranges_only_compare_the_same_period():
    figs = [
        {"label": "Data centre electricity", "unit": "TWh", "period": "2024", "value": 415.0},
        {"label": "Data centre electricity", "unit": "TWh", "period": "2030", "value": 945.0},
        {"label": "data centre electricity", "unit": "TWh", "period": "by 2030", "value": 1065.0},
        {"label": "Data centre electricity", "unit": "TWh", "period": "2022", "value": 460.0},
    ]
    assert pipeline.mark_ranges(figs) == 1
    assert "range" not in figs[0] and "range" not in figs[3]
    assert figs[1]["range"] == figs[2]["range"] == [945.0, 1065.0]
