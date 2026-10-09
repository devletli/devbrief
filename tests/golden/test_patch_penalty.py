"""Golden: yama surum majordan dusuk puan alir (security muaf)."""

from devbrief.curate import prefilter


def _score(title: str) -> float:
    total, _ = prefilter.score(title, "", source_weight=0.6, published_at=None)
    return total


def test_patch_scores_below_major():
    patch = _score("vite v8.3.4")
    major = _score("vite v9.0.0")
    assert patch < major


def test_security_patch_exempt():
    plain = _score("vite v8.3.4")
    sec = _score("vite v8.3.5 security fix")
    assert sec > plain


def test_version_bump_levels():
    assert prefilter.version_bump("v9.0.0") == "major"
    assert prefilter.version_bump("v8.3.0") == "minor"
    assert prefilter.version_bump("v8.3.4") == "patch"
    assert prefilter.version_bump("no version here") is None


def test_representative_picks_newest():
    from devbrief.curate import dedupe

    members = [
        {"id": 1, "title": "vite v8.3.0"},
        {"id": 2, "title": "vite v8.3.4"},
        {"id": 3, "title": "vite v8.2.4"},
    ]
    assert dedupe.representative(members)["id"] == 2
    assert dedupe.representative([{"id": 9, "title": "plain news"}])["id"] == 9
