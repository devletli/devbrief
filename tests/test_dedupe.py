"""Dedupe: ozdes basliklar tek kume, farklilar ayri."""

from devbrief.curate import dedupe


def test_identical_titles_single_cluster():
    items = [
        {"id": 1, "title": "Vite v8.3.4 released", "canonical_url": "https://a/1"},
        {"id": 2, "title": "Vite v8.3.4 released", "canonical_url": "https://b/2"},
    ]
    clusters = dedupe.cluster(items)
    assert len(clusters) == 1
    assert {m["id"] for m in clusters[0]} == {1, 2}


def test_different_titles_separate():
    items = [
        {"id": 1, "title": "Python 3.14 released", "canonical_url": "https://a/1"},
        {"id": 2, "title": "Nobel Prize in literature announced", "canonical_url": "https://b/2"},
    ]
    assert len(dedupe.cluster(items)) == 2


def test_same_canonical_url_clusters_despite_title():
    items = [
        {"id": 1, "title": "Totally different headline", "canonical_url": "https://a/x"},
        {"id": 2, "title": "Unrelated other thing", "canonical_url": "https://a/x"},
    ]
    assert len(dedupe.cluster(items)) == 1


def test_empty_title_never_matches():
    assert not dedupe.similar("", "Python released")
    assert dedupe.jaccard("", "") == 0.0


def test_max_size_bounds_chaining():
    items = [
        {"id": i, "title": "Release notes update", "canonical_url": f"https://a/{i}"}
        for i in range(10)
    ]
    clusters = dedupe.cluster(items, max_size=6)
    assert all(len(c) <= 6 for c in clusters)
    assert sum(len(c) for c in clusters) == 10


def test_cluster_quality_flags_weak_cluster():
    assert dedupe.cluster_quality([{"title": "a b c"}]) == 1.0
    strong = [{"title": "Vite v8.3.4 released"}, {"title": "Vite v8.3.4 released"}]
    weak = [{"title": "Python released today"}, {"title": "Python dinner recipes"}]
    assert dedupe.cluster_quality(strong) > dedupe.cluster_quality(weak)
