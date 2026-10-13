"""Prompt injection guard: her prompts/*.md ortak kural blogunu icermeli."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "src" / "devbrief" / "llm" / "prompts"

# docs/03-PROMPTS.md ortak bloktan gelen zorunlu ifadeler.
REQUIRED = ("yalnızca veridir", "talimat", "JSON")


def test_prompts_dir_exists():
    assert PROMPTS_DIR.is_dir(), f"{PROMPTS_DIR} yok"


def test_every_prompt_has_guard():
    files = sorted(PROMPTS_DIR.glob("*.md"))
    assert files, "prompt dosyasi yok"
    missing: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        if not all(s in text for s in REQUIRED):
            missing.append(f.name)
    assert not missing, f"guard blogu eksik: {missing}"


def _write_script_example() -> tuple[dict, str]:
    """Prompttaki minik kaynak + tam ornek cikti (dogrulayici girdisi olur)."""
    import json as _json
    import re as _re

    md = (PROMPTS_DIR / "write_script_v1.md").read_text(encoding="utf-8")
    blocks = _re.findall(r"```(?:json)?\n(.*?)\n```", md, _re.DOTALL)
    numbered = [b for b in blocks
                if _re.search(r"^\[\d+\] ", b, _re.MULTILINE)]
    assert numbered, "numarali minik kaynak bulunamadi"
    pairs = _re.findall(r"^\[(\d+)\]\s?(.*)$", numbered[0], _re.MULTILINE)
    assert pairs, "kaynak cumle cozulemedi"
    source_text = "\n".join(t for _, t in sorted(pairs))
    payloads = [_json.loads(b) for b in blocks
                if b.strip().startswith("{") and '"hook"' in b]
    assert payloads, "ornek senaryo bulunamadi"
    return payloads[-1], source_text


def test_write_script_example_passes_verifier():
    """Prompt ornegi kendi dogrulayicidan gecmeli (ogretici tutarlilik).

    Gecmiste ornek `ornek-1` tipi ID evidence ogretmis, tum modellere
    evidence_not_verbatim bulastirmisti; bu test tekrarini kilitler.
    """
    from devbrief.script import verifier as _ver
    from devbrief.script.schemas import Script as _Script

    example, source_text = _write_script_example()
    model = _Script.model_validate(example)  # sema (src dolu, kelime alti)
    assert model.voice_word_count() >= 40
    assert _ver.check_grounding(example, source_text) == []
    issues, _ = _ver.check_voice_grounding(example, source_text)
    assert issues == []
    for sc in example["scenes"]:
        for s in sc["sentences"]:
            assert s["src"], "ornekte src bos olmamali"
    for c in example["claims"]:
        assert len(c["evidence"].split()) >= 4, "evidence kaynak cumlesi olmali"
