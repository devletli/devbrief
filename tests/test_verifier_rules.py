"""Kural tabanli dogrulayici: kaynakta yoksa ret."""

from devbrief.script import verifier


def _script(**over) -> dict:
    d = {
        "hook": "Kutuphane cikti",
        "scenes": [
            {"id": 1, "voice": "Ornek Kutuphane 2.0 cikti.", "on_screen": "x",
             "visual": "title", "code": None, "evidence": ["e"]},
        ],
        "claims": [{"text": "Surum 2.0 yayinlandi.", "evidence": "e"}],
    }
    d.update(over)
    return d


def test_unknown_version_rejected():
    issues = verifier.check_grounding(_script(), "Ornek kutuphane yeni surumu ile burada.")
    kinds = {i.kind for i in issues}
    assert "version" in kinds
    assert any("2.0" in i.value for i in issues if i.kind == "version")


def test_matching_source_passes():
    src = "Ornek Kutuphane 2.0 cikti. Zaman asimi %50 azaldi, 2026 yilinda."
    sc = _script()
    sc["scenes"][0]["voice"] = "Ornek Kutuphane 2.0 cikti, %50 hizli, 2026 yilinda."
    sc["claims"] = [{"text": "2.0 surumu 2026 yilinda.", "evidence": "e"}]
    assert verifier.check_grounding(sc, src) == []


def test_percent_date_number_code():
    src = "sadece duz metin, sayisiz"
    sc = _script()
    sc["scenes"][0]["voice"] = "Artis %40 oldu, 09.10.2026 tarihinde, 30 istek."
    sc["scenes"][0]["code"] = "kutuphane_ozel_fonksiyon()"
    issues = verifier.check_grounding(sc, src)
    assert {i.kind for i in issues} >= {"percent", "date", "number", "code"}
    assert verifier.format_issues(issues).count("kaynakta yok") == len(issues)


def test_code_tokens_present_pass():
    src = "Kurulum icin npm install vite komutunu calistirin."
    sc = _script()
    sc["scenes"][0]["voice"] = "Guncelleme yayinlandi."
    sc["scenes"][0]["code"] = "npm install vite"
    sc["claims"] = [{"text": "Guncelleme duyuruldu.", "evidence": "e"}]
    assert verifier.check_grounding(sc, src) == []


def test_evidence_near_substring():
    src = ("Vite 8.3.4 on iki hatayi duzeltti ve kurulum boyutunu dort yuzde kuculterek "
           "otuz bir megabayta indirdi.")
    assert verifier.evidence_match(
        "Vite 8.3.4 12 hatayi duzeltti ve kurulum boyutunu kuculterek megabayta indirdi",
        src)
    assert not verifier.evidence_match(
        "Vite tamamen yeniden yazildi ve artik ucarak dagitiliyor", src)
    assert verifier.evidence_match("kisa", src)  # cok kisa: yargisiz


def test_inference_numbers_rejected_by_rule():
    sc = _script()
    sc["claims"] = [{"text": "Surum 9.9 super olacak.", "evidence": "e", "kind": "inference"}]
    issues = verifier.check_grounding(sc, "Surum 2.1 yayinda.")
    assert any(i.kind == "inference" for i in issues)


def test_percent_order_insensitive():
    src = "Rebuilds 40% quicker on big tables."
    sc = _script()
    sc["scenes"][0]["voice"] = "Yeniden kurulum %40 hizli."
    sc["claims"] = [{"text": "Hizli.", "evidence": "e"}]
    assert verifier.check_grounding(sc, src) == []
    sc["scenes"][0]["voice"] = "Yeniden kurulum %90 hizli."
    assert any(i.kind == "percent" for i in verifier.check_grounding(sc, src))
