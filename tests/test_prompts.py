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
