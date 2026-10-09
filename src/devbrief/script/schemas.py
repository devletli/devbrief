"""Senaryo semasi (docs/02 §7). Tum sinirlar burada zorlanir.

- Konusma metni (sahnelerin voice toplami): 70-100 hedef, 110 sert ust sinir.
- Sahne sayisi: 3-5. Hook: <=12 kelime. Ekran metni: <=8 kelime.
- Her claim'de evidence zorunlu (uydurma yasagi).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MIN_WORDS, TARGET_WORDS, MAX_WORDS = 70, 100, 110
HARD_MIN_WORDS, HARD_MAX_WORDS = 55, 125
MIN_SCENES, MAX_SCENES = 3, 5
MAX_HOOK_WORDS, MAX_SCREEN_WORDS = 12, 8
ONSCREEN_REPEAT_THR = 0.8


def _words(text: str) -> int:
    return len(str(text or "").split())


def _ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def check_repetition(script: dict, threshold: float = ONSCREEN_REPEAT_THR) -> list[str]:
    """Uyari duzeyi: on_screen kancayi/voice'u tekrarliyorsa kod dondurur.

    Ret degil — yazar (LLM) bir sonraki turda duzeltir. Bos liste = temiz.
    """
    hook = str(script.get("hook") or "")
    warnings: list[str] = []
    for sc in script.get("scenes") or []:
        on = str(sc.get("on_screen") or "")
        voice = str(sc.get("voice") or "")
        if _ratio(on, hook) >= threshold or _ratio(on, voice) >= threshold:
            warnings.append(f"on_screen_tekrar:scene[{sc.get('id', '?')}]")
    return warnings


class Scene(BaseModel):
    id: int
    voice: str = Field(min_length=1)
    on_screen: str = Field(min_length=1)
    visual: Literal["title", "bullets", "code", "diff", "source"] = "title"
    code: str | None = None
    evidence: list[str] = Field(min_length=1)

    @field_validator("on_screen")
    @classmethod
    def _screen_len(cls, v: str) -> str:
        if _words(v) > MAX_SCREEN_WORDS:
            raise ValueError(f"ekran metni {MAX_SCREEN_WORDS} kelimeyi asamaz")
        return v

    @field_validator("evidence")
    @classmethod
    def _evidence_nonempty(cls, v: list[str]) -> list[str]:
        if any(not str(e).strip() for e in v):
            raise ValueError("evidence bos olamaz")
        return v


class Claim(BaseModel):
    text: str = Field(min_length=1)
    evidence: str = Field(min_length=1)

    @field_validator("evidence")
    @classmethod
    def _evidence_nonblank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("evidence bos olamaz")
        return v


class Script(BaseModel):
    hook: str = Field(min_length=1)
    scenes: list[Scene] = Field(min_length=MIN_SCENES, max_length=MAX_SCENES)
    impact: str = Field(min_length=1)
    action: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    claims: list[Claim] = Field(min_length=1)
    duration_estimate_s: int = Field(ge=20, le=90)

    @model_validator(mode="after")
    def _voice_len(self) -> Script:
        total = sum(_words(s.voice) for s in self.scenes)
        if total < HARD_MIN_WORDS:
            raise ValueError(f"konusma metni cok kisa ({total} < {HARD_MIN_WORDS})")
        if total > HARD_MAX_WORDS:
            raise ValueError(f"konusma metni cok uzun ({total} > {HARD_MAX_WORDS})")
        return self

    def voice_text(self) -> str:
        return "\n".join(s.voice for s in self.scenes)

    def voice_word_count(self) -> int:
        return sum(_words(s.voice) for s in self.scenes)

    def content_warnings(self, threshold: float = ONSCREEN_REPEAT_THR) -> list[str]:
        out = check_repetition(self.model_dump(), threshold)
        total = self.voice_word_count()
        if total < MIN_WORDS or total > MAX_WORDS:
            out.append(f"kelime_hedef_disi:{total}")
        if _words(self.hook) > MAX_HOOK_WORDS:
            out.append("kanca_uzun")
        return out


class IndexVerdict(BaseModel):
    i: int = Field(ge=0)
    verdict: Literal["supported", "partial", "unsupported"] = "supported"
    note: str = ""


class VerifyOut(BaseModel):
    verdicts: list[IndexVerdict]
    overall: Literal["pass", "fail"] = "fail"

    def unsupported(self) -> list[IndexVerdict]:
        return [v for v in self.verdicts if v.verdict == "unsupported"]
