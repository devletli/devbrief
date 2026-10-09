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
             "evidence": ["e1"]}
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
    s = Script.model_validate(SAMPLE)
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
    with pytest.raises(ValidationError):
        Script.model_validate(_base(hook=" ".join("w" for _ in range(13))))
    bad = _base()
    bad["scenes"][0]["on_screen"] = " ".join("w" for _ in range(9))
    with pytest.raises(ValidationError):
        Script.model_validate(bad)


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
