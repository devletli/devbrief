"""Sema: sample gecer, ihlaller kalir."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from devbrief.script.schemas import Script

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "sample_script.json").read_text())


def _base(**over) -> dict:
    d = {
        "hook": "Kisa kanca metni burada",
        "scenes": [
            {"id": i + 1, "voice": " ".join(f"kelime{j}" for j in range(20)),
             "on_screen": "Ekran metni", "visual": "title", "code": None,
             "evidence": ["e1"], "src": [0]}
            for i in range(4)
        ],
        "impact": "Etki cumlesi.",
        "action": "Aksiyon cumlesi.",
        "source_url": "https://example.com/x",
        "claims": [{"text": "iddia", "evidence": "kanit"}],
        "duration_estimate_s": 45,
    }
    d.update(over)
    return d


def test_sample_script_passes():
    s = Script.model_validate(SAMPLE, context={"legacy": True})
    assert 70 <= s.voice_word_count() <= 110
    assert len(s.scenes) == 4
    assert s.content_warnings() == []


def test_two_scenes_rejected():
    with pytest.raises(ValidationError):
        Script.model_validate(_base(scenes=_base()["scenes"][:2]))


def test_long_voice_rejected():
    scenes = _base()["scenes"]
    scenes[0]["voice"] = "kelime " * 200
    with pytest.raises(ValidationError):
        Script.model_validate(_base(scenes=scenes))


def test_empty_evidence_rejected():
    bad = _base()
    bad["claims"] = [{"text": "iddia", "evidence": "  "}]
    with pytest.raises(ValidationError):
        Script.model_validate(bad)
    bad2 = _base()
    bad2["scenes"][0]["evidence"] = []
    with pytest.raises(ValidationError):
        Script.model_validate(bad2)


def test_hook_and_screen_limits():
    long_hook = _base(hook=" ".join("w" for _ in range(13)))
    s = Script.model_validate(long_hook)  # sert degil: gecer + uyari
    assert "kanca_uzun" in s.content_warnings()
    bad = _base()
    bad["scenes"][0]["on_screen"] = " ".join("w" for _ in range(9))
    with pytest.raises(ValidationError):
        Script.model_validate(bad)


def test_insufficient_source_skips_limits():
    d = _base()
    d["scenes"] = d["scenes"][:1]
    d["scenes"][0]["voice"] = "kisa ozet"
    d["insufficient_source"] = True
    d["reason"] = "somut bilgi yok"
    s = Script.model_validate(d)
    assert s.insufficient_source and s.reason == "somut bilgi yok"


def test_inference_kind_rules():
    from devbrief.script import schemas as _ss

    assert _ss.find_inference_violations("Guncelleme iyi olur.") == []
    bad_num = _ss.find_inference_violations("Surum 2.0 harika olacak.")
    assert bad_num, "sayi/surum yasak"
    bad_proper = _ss.find_inference_violations("Ozellikle Vite kullanicilari sevinir.")
    assert any("ozel ad" in v for v in bad_proper)
    d = _base()
    d["claims"] = [{"text": f"yorum {i}", "evidence": "e", "kind": "inference"} for i in range(3)]
    with pytest.raises(ValidationError):
        Script.model_validate(d)
    d["claims"] = d["claims"][:2]
    assert len(Script.model_validate(d).inferences()) == 2


def test_hard_soft_word_bounds():
    def _words(n: int) -> dict:
        d = _base()
        for sc in d["scenes"]:
            sc["voice"] = " ".join(f"k{j}" for j in range(n // 4))
        return d

    with pytest.raises(ValidationError):  # 36 < 40 sert
        Script.model_validate(_words(36))
    ok44 = Script.model_validate(_words(44))  # sert gecer + yumusak uyari
    assert "kelime_hedef_disi:44" in ok44.content_warnings()
    ok68 = Script.model_validate(_words(68))  # bant disi ama gecer + uyari
    assert "kelime_hedef_disi:68" in ok68.content_warnings()
    with pytest.raises(ValidationError):  # 200 > 125 sert
        Script.model_validate(_words(200))


def test_onscreen_repetition_warns_not_rejects():
    from devbrief.script.schemas import check_repetition

    hook = "Ornek Kutuphane 2.0 cikti"
    same = {"hook": hook, "scenes": [
        {"id": 1, "on_screen": hook, "voice": "baska bir konusma metni burada"}]}
    assert check_repetition(same) == ["on_screen_tekrar:scene[1]"]
    # Ret yok: s validating gecer.
    Script.model_validate(_base(hook="Kisa kanca metni burada"))
    diff = {"hook": hook, "scenes": [
        {"id": 1, "on_screen": "Varsayilan ayar degisti", "voice": "baska bir konusma metni"}]}
    assert check_repetition(diff) == []
