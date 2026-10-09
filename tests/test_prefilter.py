"""Prefilter: release/pozitif yukari, webinar/is ilani asagi, taze + HN puanli yukari."""

from devbrief.curate import prefilter


def test_keyword_score():
    assert prefilter.keyword_score("Python 3.14 release announced") > 0
    assert prefilter.keyword_score("Join our webinar, sponsored") < 0
    assert prefilter.keyword_score("plain post") == 0
    assert prefilter.keyword_score("CVE-2024-1 security vulnerability") >= 2


def test_freshness_decays():
    assert prefilter.freshness(0) == 1.0
    assert prefilter.freshness(24) > prefilter.freshness(72) > 0
    assert prefilter.freshness(None) == 0.5


def test_score_components_sum_to_total():
    total, comp = prefilter.score(
        "Vite v7.0.0 release",
        "Breaking changes in build",
        source_weight=0.6,
        hn_points=400,
        published_at=None,
    )
    assert abs(total - sum(comp.values())) < 1e-9
    assert comp["pop"] > 0 and comp["kw"] > 0


def test_hn_points_and_negatives():
    noisy, _ = prefilter.score("Sponsored webinar", hn_points=500, source_weight=0.5)
    plain, _ = prefilter.score("Some refactor notes", hn_points=0, source_weight=0.5)
    assert noisy < plain  # negatif anahtar HN puanini bastirir


def test_terms_yaml_hn_lists():
    tell = prefilter.keyword_score("Tell HN: my routine", source="hn")
    show = prefilter.keyword_score("Show HN: my tool", source="hn")
    assert tell < 0 < show


def test_normalize_and_cap_blocks_hn_dominance():
    # 22 yuksek-puanli HN + 6 GitHub girdisi: kota HN'i sinirlar.
    scored = [
        {"id": f"hn{i}", "source": "hn", "raw": 4.5 - i * 0.01} for i in range(22)
    ] + [{"id": f"gh{i}", "source": "github:x", "raw": 2.0} for i in range(6)]
    normed = prefilter.normalize_within_source(scored)
    assert all(0.0 <= s["norm"] <= 1.0 for s in normed)
    picked, reasons = prefilter.select(normed, per_source_cap=10, total=40)
    hn_picked = [s for s in picked if s["source"] == "hn"]
    assert len(hn_picked) <= 10
    assert any(s["source"] == "github:x" for s in picked)
    assert reasons["hn0"] == "secildi"
    assert reasons["hn10"] == "kaynak-kotasi"
