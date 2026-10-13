"""Senaryo semasi (docs/02 §7). Tum sinirlar burada zorlanir.

- Konusma metni: 70-100 hedef (yumusak uyari), 40-125 sert sinir
  (cumlelerin toplami; asil sert kisit TTS sonrasi sure <= 55 sn).
- Sahne sayisi: 3-5. Hook: <=12 kelime (uyari). Ekran metni: <=8 kelime.
- Sahne `sentences: [{text, src}]` dizisidir; `voice` kodla turetilir
  (" ".join). Her cumle tek olgu, <=25 kelime, src 1-2 indeks, bos olamaz.
- Her claim'de evidence zorunlu (uydurma yasagi).
- insufficient_source=true ise sahne/kelime/cumle sinirlari uygulanmaz;
  bolum ilerlemez. Legacy (voice alanli) kayitlar `legacy=True` ile yuklenir.
- Claim turleri: fact (kaynakta destek sart) | inference (yorum, en fazla 2,
  yalnizca kaynakta olmayan sayi/surum/tarih/ozel ad yasak).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

MIN_WORDS, TARGET_WORDS, MAX_WORDS = 70, 100, 110
# Sert alt sinir config'den gelir (SCRIPT_WORDS_HARD_MIN, varsayilan 40);
# modul sabiti yalnizca config okunamazsa kullanilir. Sert kisit TTS
# sonrasi toplam sure <= 55 sn'dir (render); kelime siniri onun vekilidir.
HARD_MIN_WORDS, HARD_MAX_WORDS = 40, 125
MIN_SCENES, MAX_SCENES = 3, 5
MAX_HOOK_WORDS, MAX_SCREEN_WORDS = 12, 8
ONSCREEN_REPEAT_THR = 0.8
MAX_INFERENCE_CLAIMS = 2
# Cumle-dizisi sinirlari: her cumle tek olgu, en fazla 25 kelime, 1-2 src.
MAX_SENTENCE_WORDS, MAX_SENTENCE_SRC = 25, 2


def _words(text: str) -> int:
    return len(str(text or "").split())


def _hard_min_words() -> int:
    """Sert alt kelime siniri: config SCRIPT_WORDS_HARD_MIN (varsayilan 40)."""
    try:
        from devbrief.config import get_settings as _gs

        return int(_gs().script_words_hard_min)
    except Exception:  # noqa: BLE001 - testsiz ortamda modul sabiti
        return HARD_MIN_WORDS


def _ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def scene_units(sc: dict) -> list[tuple[str, list]]:
    """Yazarin verdigi cumle dizisi: [(metin, src listesi)].

    Kod cumle bolucuye guvenmez; `sentences` varsa aynen kullanilir.
    Legacy (voice+src): voice bolunur, src paralel eslenir.
    """
    if not isinstance(sc, dict):
        return []
    sents = sc.get("sentences")
    if isinstance(sents, list) and sents:
        out = []
        for s in sents:
            if isinstance(s, dict):
                out.append((str(s.get("text") or ""), list(s.get("src") or [])))
            else:
                out.append((str(s or ""), []))
        return out
    voices = [s for s in _sentences_of(str(sc.get("voice") or "")) if s.strip()]
    srcs = sc.get("src") or []
    return [(v, [srcs[k]] if k < len(srcs) else []) for k, v in enumerate(voices)]


def scene_voice(sc: dict) -> str:
    """Sahne konusma metni: yeni semada cumlelerin birlesimi, legacy'de voice."""
    if not isinstance(sc, dict):
        return ""
    if isinstance(sc.get("sentences"), list) and sc.get("sentences"):
        return " ".join(t for t, _ in scene_units(sc)).strip()
    return str(sc.get("voice") or "")


def check_repetition(script: dict, threshold: float = ONSCREEN_REPEAT_THR) -> list[str]:
    """Uyari duzeyi: on_screen kancayi/voice'u tekrarliyorsa kod dondurur.

    Ret degil — yazar (LLM) bir sonraki turda duzeltir. Bos liste = temiz.
    """
    hook = str(script.get("hook") or "")
    warnings: list[str] = []
    for sc in script.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        on = str(sc.get("on_screen") or "")
        voice = scene_voice(sc)
        if _ratio(on, hook) >= threshold or _ratio(on, voice) >= threshold:
            warnings.append(f"on_screen_tekrar:scene[{sc.get('id', '?')}]")
    return warnings


class Sentence(BaseModel):
    """Tek olgu, tek konusma cumlesi (+ dayandigi 1-2 kaynak cumle indeksi).

    Kurallar (sahne duzeyinde somut mesajla denetlenir): metin bos olamaz,
    en fazla 25 kelime, src bos olamaz ve en fazla 2 indeks tasir.
    """

    text: str = ""
    src: list[int] = Field(default_factory=list)


class Scene(BaseModel):
    id: int
    sentences: list[Sentence] = Field(min_length=1)
    # `voice` yazar tarafindan uretilmez; kod cumlelerden turetilir
    # (" ".join). Legacy girdideki voice, sentences yoksa donusumde kullanilir.
    voice: str | None = None
    on_screen: str = Field(min_length=1)
    visual: Literal["title", "bullets", "code", "diff", "source"] = "title"
    code: str | None = None
    evidence: list[str] = Field(min_length=1)
    # Eski paralel gonderim (legacy donusum disinda kullanilmaz).
    src: list[int] = Field(default_factory=list)
    # Legacy isareti: voice donusumunde hic src yoktu; dogrulayici bu sahnede
    # src denetimini atlar (eski "src'siz cikti denetlenmez" davranisi).
    # Sema kurallari (bos src reddi) bundan etkilenmez.
    src_legacy: bool = False

    @model_validator(mode="before")
    @classmethod
    def _compat_voice(cls, data: object) -> object:
        """Uyumluluk: sentences yoksa voice (+paralel src) diziye cevrilir.

        Donusum kosulsuzdur; kurallar (src dolulugu dahil) baglama gore
        (`legacy` veya `insufficient_source`) uygulanir. Boylece eski
        fixture/kayitlar `legacy=True` ile yuklenir, v1/v2 testleri etkilenmez.
        Src'siz donusum `src_legacy=True` isaretlenir (dogrulayici muafiyeti).
        """
        if isinstance(data, dict) and not data.get("sentences") and data.get("voice"):
            voices = [s for s in _sentences_of(str(data.get("voice") or ""))
                      if s.strip()]
            srcs = data.get("src") or []
            data = dict(data)
            data["sentences"] = [
                {"text": v, "src": [srcs[k]] if k < len(srcs) else []}
                for k, v in enumerate(voices)
            ]
            if not srcs:
                data["src_legacy"] = True
        return data

    @model_validator(mode="after")
    def _derive_voice(self) -> Scene:
        """Konusma metni kodla turetilir: cumlelerin `" ".join` birlesimi."""
        object.__setattr__(
            self, "voice", " ".join(s.text.strip() for s in self.sentences).strip())
        return self

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


def find_inference_violations(text: str, source_text: str = "") -> list[str]:
    """Yorum iddiasinda yasak ogeler: KAYNAKTA OLMAYAN sayi/surum/tarih/ozel ad.

    Kaynakta gecen deger serbesttir (alinti/atif). Kaynak verilmezse eski
    katı davranis (bulunan her oge ihlal). Donus: ihlal aciklamalari.
    """
    from devbrief.script import verifier as _ver

    out: list[str] = []
    if not source_text:
        if _ver._VERSION.search(text) or _ver._PERCENT.search(text) or _ver._DATE.search(text):
            out.append("sayi/surum/tarih iceriyor")
        if _ver._NUMBER.search(text):
            out.append("rakam iceriyor")
    else:
        for tok in dict.fromkeys(_ver._VERSION.findall(text)
                                 + _ver._NUMBER.findall(text)):
            if _ver.match_value(tok, source_text) != "match":
                out.append(f"kaynakta yok: {tok!r}")
                break
        for tok in dict.fromkeys(m.group(0) for m in _ver._DATE.finditer(text or "")):
            digits = "".join(_ver._DIGITS.findall(tok))
            if digits and digits not in "".join(_ver._DIGITS.findall(source_text)):
                out.append(f"tarih kaynakta yok: {tok!r}")
                break
    src_roots = None
    if source_text:
        from devbrief.script.metadata import _WORD_RX
        from devbrief.script.verifier import _match_root

        src_roots = {_match_root(t) for t in _WORD_RX.findall(source_text)
                     if len(t) >= 2}
    for sent in _sentences_of(text):
        toks = sent.split()
        for tok in toks[1:]:
            word = tok.strip(".,;:!?()\"'")
            if (word and _PROPER_NOUN.fullmatch(word) and not word.isupper()
                    and word not in _MID_CAP_ALLOW):
                if not source_text:
                    out.append(f"ozel ad olabilir: {word!r}")
                    break
                from devbrief.script.metadata import _base_of_token
                from devbrief.script.verifier import _match_root

                if _match_root(_base_of_token(word)) not in (src_roots or set()):
                    out.append(f"ozel ad kaynakta yok: {word!r}")
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
        total = self.voice_word_count()
        hard_min = _hard_min_words()
        if total < hard_min:
            raise ValueError(f"konusma metni cok kisa ({total} < {hard_min})")
        if total > HARD_MAX_WORDS:
            raise ValueError(f"konusma metni cok uzun ({total} > {HARD_MAX_WORDS})")
        n_inf = sum(1 for c in self.claims if c.kind == "inference")
        if n_inf > MAX_INFERENCE_CLAIMS:
            raise ValueError(f"en fazla {MAX_INFERENCE_CLAIMS} yorum iddiasi ({n_inf})")
        return self

    @model_validator(mode="after")
    def _sentences_required(self, info: ValidationInfo) -> Script:
        """Cumle-dizisi kurallari (somut onarim mesaji: sahne + cumle sirasi).

        Her cumle: metin dolu, tek olgu icin en fazla 25 kelime, src bos
        olamaz ve en fazla 2 kaynak cumle indeksi tasir. Eski kayit/fixture
        yalnizca legacy=True ile yuklenirse gecer; insufficient_source muaf.
        """
        if self.insufficient_source:
            return self
        if info.context and info.context.get("legacy"):
            return self  # eski kayit/fixture: cumle kurallari aranmaz
        for sc in self.scenes:
            for k, sent in enumerate(sc.sentences, start=1):
                where = f"sahne {sc.id} cumle {k}"
                if not sent.text.strip():
                    raise ValueError(f"{where}: text bos olamaz")
                n = _words(sent.text)
                if n > MAX_SENTENCE_WORDS:
                    raise ValueError(
                        f"{where}: en fazla {MAX_SENTENCE_WORDS} kelime ({n})")
                if not sent.src:
                    raise ValueError(
                        f"{where}: src eksik "
                        "(1-2 kaynak cumle indeksi gerekli)")
                if len(sent.src) > MAX_SENTENCE_SRC:
                    raise ValueError(
                        f"{where}: {len(sent.src)} src verildi "
                        f"(en fazla {MAX_SENTENCE_SRC})")
        return self

    @model_validator(mode="after")
    def _impact_action(self, info: ValidationInfo) -> Script:
        """Impact/action yorumdur: birer cumle, en fazla 25 kelime.

        Eski kayit/fixture (legacy) ve insufficient_source muaf.
        """
        if self.insufficient_source:
            return self
        if info.context and info.context.get("legacy"):
            return self
        for key in ("impact", "action"):
            text = str(getattr(self, key) or "")
            sents = [s for s in _sentences_of(text) if s.strip()]
            if len(sents) != 1:
                raise ValueError(f"{key}: tek cumle olmali ({len(sents)})")
            if _words(text) > 25:
                raise ValueError(f"{key}: en fazla 25 kelime ({_words(text)})")
        return self

    def voice_text(self) -> str:
        return "\n".join(str(s.voice or "") for s in self.scenes)

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
    # Kod-tarafi: voice-turetilmis cumle raporu (LLM uretmez; dogrulayici
    # ekler, onay ekrani okur). Varsayilan bos; LLM ciktisi dogrulamayi bozmaz.
    voice_derived: list[dict] = Field(default_factory=list)

    def unsupported(self) -> list[IndexVerdict]:
        return [v for v in self.verdicts if v.verdict in ("contradicted", "not_stated")]
