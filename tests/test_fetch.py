"""fetch_article: trafilatura mock'lu, ag yok."""

import trafilatura

from devbrief.script import fetch


def test_ok_crops_and_hashes(monkeypatch):
    monkeypatch.setattr(trafilatura, "fetch_url", lambda url: "<html>x</html>")
    monkeypatch.setattr(trafilatura, "extract", lambda doc, **kw: "icerik " * 2000)
    r = fetch.fetch_article("https://example.com/a", rss_summary="ozet")
    assert not r.low_source
    assert len(r.text) == fetch.MAX_CHARS
    assert r.source_hash == fetch.text_hash(r.text)


def test_download_fail_falls_back(monkeypatch):
    monkeypatch.setattr(trafilatura, "fetch_url", lambda url: None)
    r = fetch.fetch_article("https://example.com/a", rss_summary="Ozet metni burada")
    assert r.low_source and r.text == "Ozet metni burada"
    assert "indirilemedi" in r.reason


def test_short_text_falls_back(monkeypatch):
    monkeypatch.setattr(trafilatura, "fetch_url", lambda url: "<html/>")
    monkeypatch.setattr(trafilatura, "extract", lambda doc, **kw: "kisa")
    r = fetch.fetch_article("https://example.com/a", rss_summary="Ozet")
    assert r.low_source and r.text == "Ozet"
    assert "kisa" in r.reason


def _boom(url):
    raise Exception("net")


def test_empty_everywhere(monkeypatch):
    monkeypatch.setattr(trafilatura, "fetch_url", _boom)
    r = fetch.fetch_article("https://example.com/a", rss_summary="")
    assert r.low_source and r.text == "" and "ozet de yok" in r.reason


def test_normalize_entities():
    assert fetch.normalize_text("a&nbsp;b&#39;c  d") == "a b'c d"
