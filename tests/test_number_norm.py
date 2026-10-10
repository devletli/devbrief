"""Sayi normalizasyonu tek yol + belirsiz turu (ag yok)."""

from devbrief.script import verifier


def test_percent_word_forms():
    assert verifier.match_value("%40", "hiz 40 yuzde artti") == "match"
    assert verifier.match_value("40 percent", "hiz %40 artti") == "match"
    assert verifier.match_value("%90", "hiz 40 yuzde artti") == "miss"


def test_magnitude_currency_separators():
    assert verifier.match_value("2.4M", "maliyet 2,4 milyon dolar") == "match"
    assert verifier.match_value("9 dollars", "fiyat 9 dolar") == "match"
    assert verifier.match_value("1,000", "katilim 1000 kisi") == "match"
    assert verifier.match_value("v8.3.4", "surum 8.3.4 yayinda") == "match"
    assert verifier.match_value("9.9", "surum 2.1 yayinda") == "miss"


def test_ambiguous_substring():
    assert verifier.match_value("40", "140 katilimci vardi") == "ambiguous"
    assert verifier.match_value("140", "40 katilimci vardi") == "ambiguous"


def test_rule_keeps_ambiguous_as_issue():
    sc = {"hook": "x", "scenes": [{"id": 1, "voice": "40 katilimci vardi.",
                                   "on_screen": "E", "visual": "title",
                                   "code": None, "evidence": ["e"]}],
          "claims": [{"text": "katilim.", "evidence": "e"}]}
    issues = verifier.check_grounding(sc, "140 katilimci vardi.")
    assert any(i.kind == "ambiguous" for i in issues)


def test_nearest_source_number():
    near = verifier.nearest_source_number("21", "Vite 8.3.4 12 hata duzeltti.")
    assert "12" in near
    assert verifier.nearest_source_number("99", "sayisiz metin") == "(sayisal eslesme yok)"
