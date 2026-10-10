"""Senaryo semasi (docs/02 §7). Tum sinirlar burada zorlanir.

- Konusma metni: 70-100 hedef, 55-125 sert sinir.
- Sahne sayisi: 3-5. Hook: <=12 kelime (uyari). Ekran metni: <=8 kelime.
- Her claim'de evidence zorunlu (uydurma yasagi).
- insufficient_source=true ise sahne/kelime sinirlari uygulanmaz; bolum ilerlemez.
- Claim turleri: fact (kaynakta destek sart) | inference (yorum, en fazla 2,
  sayi/surum/tarih/ozel ad iceremez).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MIN_WORDS, TARGET_WORDS, MAX_WORDS = 70, 100, 110
HARD_MIN_WORDS, HARD_MAX_WORDS = 55, 125
MIN_SCENES, MAX_SCENES = 3, 5
MAX_HOOK_WORDS, MAX_SCREEN_WORDS = 12, 8
ONSCREEN_REPEAT_THR = 0.8
MAX_INFERENCE_CLAIMS = 2


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
    kind: Literal["fact", "inference"] = "fact"

    @field_validator("evidence")
    @classmethod
    def _evidence_nonblank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("evidence bos olamaz")
        return v


_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def _sentences_of(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


_PROPER_NOUN = re.compile(r"[A-ZÇĞİÖŞÜ][a-zçğıöşü]+")
_MID_CAP_ALLOW = frozenset({
    # Cumle ortasinda buyuk harfle baslamasi normal olanlar (kisaltma degil).
    "Pazartesi", "Salı", "Sali", "Çarşamba", "Carsamba", "Perşembe", "Persembe",
    "Cuma", "Cumartesi", "Pazar", "Ocak", "Şubat", "Subat", "Mart", "Nisan",
    "Mayıs", "Mayis", "Haziran", "Temmuz", "Ağustos", "Agustos", "Eylül", "Eylul",
    "Ekim", "Kasım", "Kasim", "Aralık", "Aralik",
})


def find_inference_violations(text: str) -> list[str]:
    """Yorum iddiasinda yasak ogeler: sayi/surum/tarih/ozel ad.

    Donus: ihlal aciklamalari (bos = temiz). Kural tabanli, deterministik.
    """
    from devbrief.script import verifier as _ver

    out: list[str] = []
    if _ver._VERSION.search(text) or _ver._PERCENT.search(text) or _ver._DATE.search(text):
        out.append("sayi/surum/tarih iceriyor")
    if _ver._NUMBER.search(text):
        out.append("rakam iceriyor")
    for sent in _sentences_of(text):
        toks = sent.split()
        for tok in toks[1:]:
            word = tok.strip(".,;:!?()\"'")
            if (word and _PROPER_NOUN.fullmatch(word) and not word.isupper()
                    and word not in _MID_CAP_ALLOW):
                out.append(f"ozel ad olabilir: {word!r}")
                break
    return out


class Script(BaseModel):
    hook: str = Field(min_length=1)
    scenes: list[Scene] = Field(min_length=1)
    impact: str = Field(min_length=1)
    action: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    claims: list[Claim] = Field(default_factory=list)
    duration_estimate_s: int = Field(ge=20, le=90)
    insufficient_source: bool = False
    reason: str = ""

    @model_validator(mode="after")
    def _limits(self) -> Script:
        if self.insufficient_source:
            return self  # cikis yolu: sahne/kelime sinirlari uygulanmaz
        if not (MIN_SCENES <= len(self.scenes) <= MAX_SCENES):
            raise ValueError(f"sahne sayisi 3-5 olmali ({len(self.scenes)})")
        total = sum(_words(s.voice) for s in self.scenes)
        if total < HARD_MIN_WORDS:
            raise ValueError(f"konusma metni cok kisa ({total} < {HARD_MIN_WORDS})")
        if total > HARD_MAX_WORDS:
            raise ValueError(f"konusma metni cok uzun ({total} > {HARD_MAX_WORDS})")
        n_inf = sum(1 for c in self.claims if c.kind == "inference")
        if n_inf > MAX_INFERENCE_CLAIMS:
            raise ValueError(f"en fazla {MAX_INFERENCE_CLAIMS} yorum iddiasi ({n_inf})")
        return self

    def voice_text(self) -> str:
        return "\n".join(s.voice for s in self.scenes)

    def voice_word_count(self) -> int:
        return sum(_words(s.voice) for s in self.scenes)

    def content_warnings(self, threshold: float = ONSCREEN_REPEAT_THR) -> list[str]:
        out = check_repetition(self.model_dump(), threshold)
        if not self.insufficient_source:
            total = self.voice_word_count()
            if total < MIN_WORDS or total > MAX_WORDS:
                out.append(f"kelime_hedef_disi:{total}")
        if _words(self.hook) > MAX_HOOK_WORDS:
            out.append("kanca_uzun")
        return out

    def inferences(self) -> list[Claim]:
        """Onay ekraninda 'yorum' olarak ayri gosterilir."""
        return [c for c in self.claims if c.kind == "inference"]


class IndexVerdict(BaseModel):
    i: int = Field(ge=0)
    verdict: Literal["entailed", "contradicted", "not_stated"] = "not_stated"
    note: str = ""


class VerifyOut(BaseModel):
    verdicts: list[IndexVerdict]
    overall: Literal["pass", "fail"] = "fail"

    def unsupported(self) -> list[IndexVerdict]:
        return [v for v in self.verdicts if v.verdict in ("contradicted", "not_stated")]
