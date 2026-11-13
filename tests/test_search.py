from nodexm.credibility import score_source
from nodexm.search import Hit, iso_date, normalize_url


def test_dates_are_normalised():
    assert iso_date("Mon, 05 Jan 2026 10:00:00 GMT") == "2026-01-05"
    assert iso_date("2025-02-01T08:30:00Z") == "2025-02-01"
    assert iso_date("2024") == "2024"
    assert iso_date("published sometime") is None
    assert iso_date(None) is None
    assert Hit(url="https://x.org", title="t", published="Tue, 14 Oct 2025 09:00:00 GMT").published == "2025-10-14"


def test_normalised_dates_feed_credibility():
    _, reasons = score_source("https://example.org/a", published=iso_date("Mon, 05 Jan 2015 10:00:00 GMT"))
    assert any("Older source (2015)" in r for r in reasons)


def test_url_normalisation_drops_tracking():
    assert normalize_url("https://www.Example.com/a/?utm_source=x&id=2#top") == "https://example.com/a?id=2"
