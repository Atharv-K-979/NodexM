from nodexm.credibility import score_source
from nodexm.evidence import (
    audit_citations,
    group_claims,
    pack_batches,
    cluster,
    confidence,
    find_duplicates,
    lexical_vectors,
    locate_quote,
    numbers_in,
    parse_citation,
)

TEXT = (
    "The Commission estimates that compliance costs for a high-risk AI system range from €6,000 to €7,000 per year.\n"
    "Small  and medium-sized enterprises may face a proportionally higher burden, according to the impact assessment."
)


def test_locate_exact_quote_with_whitespace_and_case():
    start, end, score = locate_quote(TEXT, "small and medium-sized enterprises may face a proportionally higher burden")
    assert score == 1.0
    assert TEXT[start:end].lower().startswith("small  and medium")


def test_locate_fuzzy_quote():
    start, end, score = locate_quote(TEXT, "compliance costs for a high risk AI system range from €6,000 to €7,000 a year")
    assert start >= 0 and score >= 0.8


def test_invented_quote_is_rejected():
    start, _, score = locate_quote(TEXT, "SMEs will pay over one million euros in fines every month")
    assert start == -1


def test_duplicates_keep_the_more_credible_source():
    base = " ".join(f"word{i}" for i in range(200))
    texts = {0: base, 1: base + " extra tail", 2: " ".join(f"other{i}" for i in range(200))}
    dups = find_duplicates(texts, {0: 0.5, 1: 0.9, 2: 0.5})
    assert dups == {0: 1}


def test_lexical_clustering_groups_paraphrases():
    claims = [
        "SME compliance costs under the AI Act are estimated at 6000 euros per system",
        "The AI Act compliance costs for SMEs are estimated at 6000 euros per system per year",
        "The EU Parliament adopted the AI Act in March 2024",
    ]
    groups = cluster(lexical_vectors(claims), threshold=0.45)
    assert sorted(len(g) for g in groups) == [1, 2]


def test_confidence_orders_statuses():
    agreed = confidence("agreed", [0.9, 0.85])
    single = confidence("single_source", [0.9])
    disputed = confidence("disputed", [0.9], [0.9])
    assert agreed > single > disputed


def test_credibility_rules():
    gov, _ = score_source("https://eur-lex.europa.eu/eli/reg/2024/1689/oj")
    blog, _ = score_source("https://someone.medium.com/post")
    assert gov > 0.85 and blog < 0.4


def test_citation_parsing_and_numbers():
    assert parse_citation("1, 3-5") == [1, 3, 4, 5]
    assert numbers_in("costs rose 12.5% to €6,000 in 2024") == {"12.5", "6000"}


def test_audit_flags_unsupported_numbers():
    report = (
        "## Findings\n"
        "Compliance costs for a high-risk system range from €6,000 to €7,000 per year [1].\n"
        "Compliance costs for a high-risk system are €90,000 per year [1].\n"
        "Fines can reach 35 million euros.\n"
    )
    audit = audit_citations(report, {0: TEXT}, {0: []})
    assert audit["checked"] == 2 and audit["supported"] == 1
    problems = " ".join(i["problem"] for i in audit["issues"])
    assert "90000" in problems and "without a citation" in problems


def test_fallback_grouping_never_merges_different_years():
    texts = [
        "Global data centre electricity consumption was 415 TWh in 2024.",
        "In 2024 data centres consumed about 415 TWh of electricity worldwide.",
        "Global data centre electricity consumption will reach 945 TWh in 2030.",
        "Data centre electricity use is projected to hit 945 TWh by 2030.",
    ]
    groups = group_claims(texts, lexical_vectors(texts), lexical=True)
    assert sorted(map(sorted, groups)) == [[0, 1], [2, 3]]


def test_pack_batches_respects_the_limit():
    vecs = lexical_vectors([f"claim about topic {i % 3} number {i}" for i in range(130)])
    batches = pack_batches(vecs, 60, lexical=True)
    assert all(len(b) <= 60 for b in batches)
    assert sorted(i for b in batches for i in b) == list(range(130))
