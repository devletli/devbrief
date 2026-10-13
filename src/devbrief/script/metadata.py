"""Baslik/aciklama/gonderi metinleri: write_metadata (varsayilan tier smart).

Uretim modu (`METADATA_MODE`, varsayilan extractive):
- extractive: LLM yalnizca havuz indeksleri secer (`select_metadata`,
  tur basina TEK cagri, onarimsiz); cumleler, link, beyan ve butce
  birlestirme tamamen kodladir. Cikti cumleleri normalize havuzda
  birebir bulunur; dayanak `pool#idx` yazilir.
- generative: serbest cumle taslagi + destek filtresi + LLM dogrulama
  (secenek; kayitli eval-metadata kapisi gecilmeden secilemez).

Prompt tier:fast der; kapi FAST adayi cikarmadigi icin smart kullanilir.
FAST adayi bulununca geri alinir.

LLM cumle listesi uretir (X/LinkedIn: `body_sentences`, onem sirasina gore
3-6 tam cumle); kod cumleleri sirayla ekleyip butceyi asmayan en uzun oneki
alir (deterministik, ortada kesme yok). LLM govde uzunluguna hicbir yerde
guvenilmez: olcum her zaman kodda yapilir. Hic cumle sigmiyorsa kural
(`ilk cumle en fazla N karakter`) ile tek yeniden uretim turu.

Kaynak linki + AI beyani KOD ekler (LLM metne URL/beyan yazmaz): beyan
cumleleri `config/disclosure.yaml` icindedir (uzun bicim: youtube/linkedin/
aciklama; kisa bicim: X). X sayimi `X_URL_LEN` configine gore (varsayilan
23, t.co kurali). Govde butcesi = platform limiti - (link + beyan + ayirac);
butce hesabi `metadata_json` icindeki `budget` alaninda saklanir, audit-pack
gosterir. Kod eklemeden SONRA `check_metadata` calisir: link+beyan sarti
yalnizca description/x_post/linkedin_post icin regresyon korumasidir,
baslik link icermez.

Onbellek: YALNIZCA sema + gorev denetimini (uzunluk + kural dahil) gecmis
cikti `llm_cache`'e yazilir/kullanilir; ret turu tur+hata hash'i ile ayri
anahtar alir (`cache_salt`), reddedilen yanit ikinci turda donmez.

Kayit oncesi kural denetimi (`check_metadata`, mevcut match_value/normalizasyon
ile — ikinci kod yolu yok): sayi/surum/tarih/ozel ad senaryoda veya kaynakta
olmalidir. Ret -> en fazla 2 yeniden uretim (somut hata + call_cap); olmadiysa
bolum `metadata_failed` olur, ilerlemez. Abarti uyarı duzeyindedir (beyan
denetim disi).

Cumle destegi: her govde cumlesi (baslik, aciklama, X/LinkedIn; link+beyan
haric) senaryodaki iddialardan ve kaynak metinden desteklenmelidir.
(a) deterministik: icerik sozcuklerinin (isim/fil koku `_root_of`, sayi
`match_value` — mevcut normalizasyon, ikinci kod yolu yok) en az %70'i
senaryo voice metni veya kaynakta gecer; (b) LLM: `verify_script` sozlesmesi
(indeksli entailed/contradicted/not_stated, tek istek, cap'e dahil) ile
dogruma; not_stated/contradicted cumle atilir, hic cumle kalmazsa yeniden
uretim. Degisim iddialari (artik/yeni/...) desteksizse ret. Baslik ayrica
ana varlik + ana eylemi tasir (ret); fiilsiz isim yigini yalnizca
`title_grammar_suspect` uyarisidir. Cumle dayanaklari `sentence_support`
alaninda saklanir, onay + audit-pack gosterir.
"""

from __future__ import annotations

import json as _json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from devbrief.config import get_settings
from devbrief.llm.client import complete_json

HYPE_PATH = "config/hype_terms.yaml"
STOPLIST_PATH = "config/proper_noun_stoplist.yaml"
DISCLOSURE_PATH = "config/disclosure.yaml"
MAX_TURNS = 2

# Platform limitleri (ARTIRILAMAZ): baslik <=70, X <=270, LinkedIn <=600,
# hashtag <=5, aciklama <=5000 (YouTube). X agirlikli sayim da <=270.
TITLE_LIMIT = 70
X_LIMIT = 270
LINKEDIN_LIMIT = 600
DESC_LIMIT = 5000
HASHTAG_MAX = 5
MIN_SENTENCES, MAX_SENTENCES = 3, 6

# Kodla eklenecek eklerin ayiraclari (butce + ekleme TEK kaynaktan).
DESC_LINK_SEP = "\n\nKaynak: "
NOTE_SEP_DESC = "\n"
X_LINK_SEP = " "
NOTE_SEP_X = "\n"

# X URL sayimi (docs.x.com "Counting Characters", 2026-09 teyitli):
# her URL t.co ile paketlenir ve uzunlugundan bagimsiz 23 sayilir.
# Ornek: https://example.com/very/long/path -> 23 karakter.
# Config X_URL_LEN ile degistirilebilir; <=0 ise muhafazakar tam-boy
# sayima donulur (emin olunamayan ortam icin guvenli varsayilan).
X_URL_LEN_DEFAULT = 23

URL_RX = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


def x_url_len() -> int:
    """X URL sayim birimi (config X_URL_LEN, varsayilan 23)."""
    try:
        return int(get_settings().x_url_len)
    except Exception:  # noqa: BLE001
        return X_URL_LEN_DEFAULT


class MetadataDraft(BaseModel):
    """LLM ciktisi: X/LinkedIn govdeleri cumle listesidir.

    Uzunluklara guvenilmez; kod cumleleri sirayla ekleyip butceyi asmayan
    en uzun oneki alir. Butce asimi kirpma/yeniden uretimle cozulur.
    """

    title: str = Field(min_length=1, max_length=TITLE_LIMIT)
    description: str = Field(min_length=1, max_length=DESC_LIMIT)
    hashtags: list[str] = Field(max_length=HASHTAG_MAX)
    x_sentences: list[str] = Field(min_length=MIN_SENTENCES, max_length=MAX_SENTENCES)
    linkedin_sentences: list[str] = Field(min_length=MIN_SENTENCES,
                                         max_length=MAX_SENTENCES)

    @field_validator("x_sentences", "linkedin_sentences")
    @classmethod
    def _nonblank_sentences(cls, v: list[str]) -> list[str]:
        if any(not str(s).strip() for s in v):
            raise ValueError("bos cumle olamaz")
        return v


class Metadata(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_LIMIT)
    description: str = Field(min_length=1, max_length=DESC_LIMIT)
    hashtags: list[str] = Field(max_length=HASHTAG_MAX)
    x_post: str = Field(min_length=1, max_length=X_LIMIT)
    linkedin_post: str = Field(min_length=1, max_length=LINKEDIN_LIMIT)


class MetadataFailed(RuntimeError):
    """Son turun somut ret listesini `last_errors` alaninda tasir (CLI basar)."""

    def __init__(self, message: str = "", last_errors: list[str] | None = None):
        super().__init__(message)
        self.last_errors = list(last_errors or [])


@lru_cache(maxsize=4)
def load_hype_terms(path: str = HYPE_PATH) -> list:
    import yaml

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return []
    out = []
    for t in data.get("terms") or []:
        try:
            out.append(re.compile(re.escape(str(t)), re.IGNORECASE))
        except re.error:
            continue
    return out


@lru_cache(maxsize=4)
def load_disclosure(path: str = DISCLOSURE_PATH) -> dict[str, str]:
    """Beyan cumleleri: long (youtube/linkedin/aciklama), short (X).

    Yalnizca KOD ekler; LLM metne yazmaz, denetim disi tutulur.
    """
    fallback = {
        "long": ("Yapay zekâ destekli hazırlanmıştır ve sentetik ses içerir; "
                 "hata fark edersen yaz."),
        "short": "🤖 AI destekli içerik",
    }
    import yaml

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return fallback
    long = str(data.get("long") or fallback["long"]).strip()
    short = str(data.get("short") or fallback["short"]).strip()
    return {"long": long or fallback["long"], "short": short or fallback["short"]}


def note_for(platform: str) -> str:
    """Platform beyan cumlesi: x -> kisa bicim, digerleri uzun bicim."""
    d = load_disclosure()
    return d["short"] if platform == "x" else d["long"]


def disclosure_notes() -> list[str]:
    d = load_disclosure()
    notes = [d["long"], d["short"]]
    try:
        from devbrief.publish.platforms import AI_NOTE as _legacy

        notes.append(_legacy)
    except Exception:  # noqa: BLE001
        pass
    return [n for n in dict.fromkeys(n for n in notes if n)]


@lru_cache(maxsize=4)
def load_proper_noun_stoplist(path: str = STOPLIST_PATH) -> set[str]:
    """Istisna listesi (kucuk harf duyarsiz, Turkce I/ı ile)."""
    import yaml

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return set()
    terms = data.get("terms") or []
    return {_tr_lower(str(t)) for t in terms if str(t).strip()}


def _strip_ai_note(text: str) -> str:
    """Beyan cumleleri sabit metindir; dogrulama disi tutulur (tum bicimler)."""
    out = text or ""
    for note in disclosure_notes():
        out = out.replace(note, "")
    return out


def _tr_lower(s: str) -> str:
    """Turkce I/ı kurali: I->ı, İ->i, sonra lower."""
    return s.replace("I", "ı").replace("İ", "i").lower()


_APOS_RX = re.compile(r"['’`]")


def _base_of_token(word: str) -> str:
    """Kesme isareti sonrasi ekleri at: Claude'un -> Claude."""
    return _APOS_RX.split(word or "")[0]


# Yaygin iyelik/hal ekleri + cogul; kombinasyonlar yinelemeli siyirma ile.
_TR_SUFFIXES = (
    "dan", "den", "lar", "ler", "da", "de",
    "nın", "nin", "nun", "nün", "nı", "ni", "nu", "nü", "na", "ne",
    "ın", "in", "un", "ün", "sı", "si", "su", "sü",
    "ı", "i", "u", "ü", "a", "e", "n",
)


def _root_of(word: str) -> str:
    """Sozcuk kokunu yaklasikla: ekleri sondan siyir (karsilastirma icin).

    Aday ve referans ayni islemden gecer; asiri siyirma iki tarafta da
    olur, eslesmeyi bozmaz. En az 2 karakter korunur.
    """
    w = _tr_lower(_base_of_token(word.strip()))
    changed = True
    while changed and len(w) > 2:
        changed = False
        for suf in _TR_SUFFIXES:
            if w.endswith(suf) and len(w) - len(suf) >= 2:
                w = w[: -len(suf)]
                changed = True
                break
    return w


_WORD_RX = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşü]+", re.UNICODE)


def _ref_roots(ref: str) -> set[str]:
    return {_root_of(t) for t in _WORD_RX.findall(ref or "") if len(t) >= 2}


def _clean_url(u: str) -> str:
    return (u or "").strip().strip(".,;:!?()\"'’`…–—-")


def extract_urls(text: str) -> list[str]:
    return [_clean_url(m.group(0)) for m in URL_RX.finditer(text or "")]


def x_weighted_len(text: str) -> int:
    """X agirlikli uzunluk: her URL `x_url_len()` sayilir (t.co kurali).

    Config <=0 ise muhafazakar tam-boy sayim (dokuman supheli ortamlarda).
    """
    n = x_url_len()
    if n <= 0:
        return len(text or "")
    tmp = URL_RX.sub("X" * n, text or "")
    return len(tmp)


def _ref_urls(script: dict, source_text: str) -> set[str]:
    refs = [source_text or "", str(script.get("source_url") or "")]
    for key in ("hook", "impact", "action"):
        refs.append(str(script.get(key) or ""))
    for sc in script.get("scenes") or []:
        if isinstance(sc, dict):
            refs.append(_scene_voice(sc))
    for c in script.get("claims") or []:
        if isinstance(c, dict):
            refs.append(str(c.get("text") or ""))
            refs.append(str(c.get("evidence") or ""))
    out: set[str] = set()
    for r in refs:
        out.update(extract_urls(r))
    if script.get("source_url"):
        out.add(_clean_url(str(script["source_url"])))
    return {u for u in out if u}


def budget_report(source_url: str) -> dict:
    """Butce hesabi: govde butcesi = limit - (link payi + beyan payi + ayirac).

    X link payi X sayim kuralina gore (`x_url_len`, t.co); emin olunamazsa
    tam boy sayilir. Donus `metadata_json` icindeki `budget` alanina ve
    audit-pack'e aynen yazilir.
    """
    url = (source_url or "").strip()
    xl = x_url_len()
    counted_url = xl if xl > 0 else len(url)
    d_link = len(DESC_LINK_SEP) + len(url)
    d_note = len(NOTE_SEP_DESC) + len(note_for("description"))
    li_note = len(NOTE_SEP_DESC) + len(note_for("linkedin"))
    x_link_raw = len(X_LINK_SEP) + len(url)
    x_link_counted = len(X_LINK_SEP) + counted_url
    x_note = len(NOTE_SEP_X) + len(note_for("x"))
    x_body = min(X_LIMIT - x_link_raw - x_note, X_LIMIT - x_link_counted - x_note)
    return {
        "source_url": url,
        "x_url_len": xl,
        "platforms": {
            "title": {"limit": TITLE_LIMIT, "body_budget": TITLE_LIMIT,
                      "link_share": 0, "note_share": 0},
            "description": {"limit": DESC_LIMIT,
                            "body_budget": DESC_LIMIT - d_link - d_note,
                            "link_share": d_link, "note_share": d_note},
            "x_post": {"limit": X_LIMIT, "body_budget": x_body,
                       "link_share": x_link_raw, "link_counted": x_link_counted,
                       "note_share": x_note},
            "linkedin_post": {"limit": LINKEDIN_LIMIT,
                              "body_budget": LINKEDIN_LIMIT - d_link - li_note,
                              "link_share": d_link, "note_share": li_note},
        },
    }


def body_budgets(source_url: str) -> dict[str, int]:
    """Govde butceleri (karakter): title/description/x_post/linkedin_post."""
    plats = budget_report(source_url)["platforms"]
    out = {k: int(v["body_budget"]) for k, v in plats.items()}
    out["hashtags"] = HASHTAG_MAX
    return out


_SENT_SPLIT_RX = re.compile(r"(?<=[.!?…])\s+|\n+")


def split_sentences(text: str) -> list[str]:
    """Metni tam cumlelere bol (boslar atilir)."""
    return [s.strip() for s in _SENT_SPLIT_RX.split(text or "") if s.strip()]


def normalize_sentences(items: list[str] | tuple) -> list[str]:
    """Cumle listesini duzlestir: cok cumleli ogeler bolunur, boslar atilir.

    Deterministik; sira korunur, ortada kesme yok.
    """
    out: list[str] = []
    for it in items or []:
        out.extend(split_sentences(it))
    return out


def assemble_sentences(sentences: list[str], budget: int) -> str:
    """Cumleleri sirayla ekleyip butceyi asmayan en uzun oneki al.

    Ortada kesme yok. Hic cumle sigmiyorsa "" doner (yeniden uretim gerekir).
    """
    kept: list[str] = []
    for s in sentences or []:
        cand = f"{' '.join(kept)} {s}".strip() if kept else s.strip()
        if len(cand) <= budget:
            kept.append(s.strip())
        else:
            break
    return " ".join(kept)


def truncate_to_budget(text: str, budget: int) -> str | None:
    """Govdeyi cumle sinirindan kirp (son tam cumleye kadar).

    Kelime ortasinda kesmez, "…" eklemez. Tek cumle bile sigmiyorsa
    None doner (kirpilamaz -> yeniden uretim turu gerekir).
    """
    t = (text or "").strip()
    if len(t) <= budget:
        return t
    sents = split_sentences(t)
    if not sents:
        return None
    out = assemble_sentences(sents, budget)
    return out or None


def _with_link_note(text: str, link_sep: str, note_sep: str, url: str,
                    note: str) -> str:
    t = (text or "").rstrip()
    if url and url not in t:
        t = f"{t}{link_sep}{url}"
    if note and note not in t:
        t = f"{t}{note_sep}{note}"
    # Eski bicim beyan varsa da kabul (cift ekleme yok); denetim disi.
    return t


def attach_source(meta: Metadata, source_url: str) -> Metadata:
    """LLM govdesine kaynak linki + AI beyani kodla ekler (tekrar yok).

    - description/linkedin: govde + "\\n\\nKaynak: <url>" + "\\n" + uzun beyan
    - x_post: govde + " <url>" + "\\n" + KISA beyan
    - title/hashtags: ek yok. Beyanlar `config/disclosure.yaml` icindedir.
    """
    url = (source_url or "").strip()
    if not url:
        return meta
    desc = _with_link_note(meta.description, DESC_LINK_SEP, NOTE_SEP_DESC, url,
                           note_for("description"))
    link_body = _with_link_note(meta.linkedin_post, DESC_LINK_SEP, NOTE_SEP_DESC,
                                url, note_for("linkedin"))
    x = _with_link_note(meta.x_post, X_LINK_SEP, NOTE_SEP_X, url, note_for("x"))
    return Metadata(title=meta.title, description=desc, hashtags=meta.hashtags,
                    x_post=x, linkedin_post=link_body)


def assert_final_lengths(meta: Metadata) -> None:
    """Son iddia: kod eki sonrasi her platform metni limiti asmamali."""
    assert len(meta.title) <= TITLE_LIMIT, f"title {len(meta.title)}/{TITLE_LIMIT}"
    assert len(meta.hashtags) <= HASHTAG_MAX, f"hashtags {len(meta.hashtags)}"
    assert len(meta.x_post) <= X_LIMIT, f"x_post {len(meta.x_post)}/{X_LIMIT}"
    assert x_weighted_len(meta.x_post) <= X_LIMIT, (
        f"x_post agirlikli {x_weighted_len(meta.x_post)}/{X_LIMIT}")
    assert len(meta.linkedin_post) <= LINKEDIN_LIMIT, (
        f"linkedin_post {len(meta.linkedin_post)}/{LINKEDIN_LIMIT}")
    assert len(meta.description) <= DESC_LIMIT, (
        f"description {len(meta.description)}/{DESC_LIMIT}")


def _assemble_lists(title: str, description: str, hashtags: list,
                    x_list: list[str], li_list: list[str],
                    script: dict, source_text: str, basismap: dict | None = None,
                    verdict: str = "entailed"
                    ) -> tuple[Metadata | None, list[str], list[str], list[dict]]:
    """Butce oneki + ek + assert + kural. Donus: (final|None, hatalar,
    uyarilar, dayanaklar). Dayanaklar ek-oncesi govdelerden cikarilir;
    `verdict` dayanaklara islenir (extractive: "extractive")."""
    url = str((script or {}).get("source_url") or "").strip()
    budgets = body_budgets(url)
    errs: list[str] = []
    warns: list[str] = []
    basismap = basismap or {}
    if len(title or "") > budgets["title"]:
        errs.append(f"uzunluk asildi: title {len(title)}/{budgets['title']},"
                    f" {len(title) - budgets['title']} karakter kisalt"
                    " (baslik kirpilamaz, yeniden uretim gerekli)")
    if len(hashtags or []) > HASHTAG_MAX:
        errs.append(f"uzunluk asildi: hashtags {len(hashtags)}/{HASHTAG_MAX},"
                    f" {len(hashtags) - HASHTAG_MAX} etiket cikar"
                    " (yeniden uretim gerekli)")
    desc = (description or "").strip()
    if len(desc) > budgets["description"]:
        cut = truncate_to_budget(desc, budgets["description"])
        if cut is None:
            errs.append(f"uzunluk asildi: description {len(desc)}/"
                        f"{budgets['description']} (kirpilamadi: tek cumle,"
                        " yeniden uretim gerekli)")
        else:
            desc = cut
            warns.append("truncated:description")
    bodies: dict[str, str] = {}
    for field, items in (("x_post", x_list), ("linkedin_post", li_list)):
        sents = normalize_sentences(items)
        body = assemble_sentences(sents, budgets[field])
        if not body:
            first = max((len(s) for s in sents), default=0)
            errs.append(f"uzunluk asildi: {field} hic cumle sigmadi"
                        f" (ilk cumle {first}/{budgets[field]}); ilk cumle en fazla"
                        f" {budgets[field]} karakter olmali, yeniden uretim gerekli")
        else:
            bodies[field] = body
            if len(" ".join(sents)) > budgets[field]:
                warns.append(f"truncated:{field}")
    if errs:
        return None, errs, warns, []
    try:
        body_meta = Metadata(title=title, description=desc,
                             hashtags=hashtags, x_post=bodies["x_post"],
                             linkedin_post=bodies["linkedin_post"])
    except ValidationError as e:
        return None, [f"uzunluk/sema asildi: {_short_validation(e)}"], warns, []
    try:
        meta = attach_source(body_meta, url)
        assert_final_lengths(meta)
    except ValidationError as e:
        return None, [f"uzunluk/sema asildi: {_short_validation(e)}"], warns, []
    except AssertionError as e:
        return None, [f"uzunluk asildi (final): {e}"], warns, []
    problems = check_metadata(meta, script, source_text)
    if problems:
        return None, problems, warns, []
    support: list[dict] = []
    pre = {"title": [title.strip()], "description": split_sentences(desc),
           "x_post": split_sentences(bodies["x_post"]),
           "linkedin_post": split_sentences(bodies["linkedin_post"])}
    for field, sents in pre.items():
        for s in sents:
            b = basismap.get((field, s), {})
            support.append({"field": field, "sentence": s,
                            "basis": b.get("basis", "bilinmiyor"),
                            "ref": b.get("ref", ""),
                            "coverage": b.get("coverage", 0.0),
                            "verdict": verdict})
    return meta, [], warns, support


def assemble_final(draft: MetadataDraft, script: dict, source_text: str
                   ) -> tuple[Metadata | None, list[str], list[str]]:
    """Taslak -> FINAL. Deterministik; LLM govde uzunluguna guvenilmez.

    - Destek filtresi: kapsama %70 alti + desteksiz degisim iddiasi cumleler
      atilir; bosalan platform hata doner (yeniden uretim gerekir).
    - description (string): butce asiminda cumle sinirindan kirpilir.
    - x_post/linkedin_post: cumleler sirayla eklenip butceyi asmayan en uzun
      onek alinir (ortada kesme yok).
    - title/hashtag asimi kirpilamaz (dogrudan yeniden uretim).
    Sonra link+beyan kodla eklenir, son uzunluk ve kural denetimi calisir.
    Donus: (final | None, hatalar, uyarilar [`truncated:*`]).
    LLM cumle dogrulamasi burada YOK (canli turda `produce` yapar).
    """
    units = draft_units(draft)
    kept, _dropped, basismap = filter_supported(units, script, source_text)
    by: dict[str, list[str]] = {}
    for u in kept:
        by.setdefault(u["field"], []).append(u["sentence"])
    errs: list[str] = []
    for f in SUPPORT_FIELDS:
        if not by.get(f):
            errs.append(f"{f}: hic destekli cumle kalmadi, yeniden uretim gerekli")
    if errs:
        return None, errs, []
    meta, errs, warns, _support = _assemble_lists(
        by["title"][0], " ".join(by["description"]), draft.hashtags,
        by["x_post"], by["linkedin_post"], script, source_text, basismap)
    return meta, errs, warns


async def _verify_units(kept: list[dict], source_text: str, conn=None,
                        client=None, rate_limit_per_min: float | None = None,
                        cap=None, models: list[str] | None = None,
                        verify_cap=None):
    """Tum cumleler birlikte `verify_script` sozlesmesiyle (cift-model).

    VERIFY listesinden iki model (tekse tek + kararsizlik cifti); claim
    yalnizca ikisi de entailed ise gecer. Cagrilar uretici cap'ine (`cap`)
    VE verify alt kotasina (VERIFY_CALL_CAP) dahildir; ikisi de asilmaz.
    Donus normalize edilmis VerifyOut (cift-model birlesik).
    """
    from devbrief.llm.client import CallCap
    from devbrief.script import verifier as _verifier

    claims = [{"text": u["sentence"], "evidence": u.get("ref_text") or u["sentence"],
               "kind": "fact"} for u in kept]
    if models is None:
        try:
            _s = get_settings()
            models = [m for m in _s.verify_models if _s.model_allowed(m)][:2]
            vcap_n = int(_s.verify_call_cap)
        except Exception:  # noqa: BLE001
            models, vcap_n = [], 6
    else:
        try:
            vcap_n = int(get_settings().verify_call_cap)
        except Exception:  # noqa: BLE001
            vcap_n = 6
    if not models:
        raw = await _verifier.verify_llm(source_text, claims, conn=conn, client=client,
                                         rate_limit_per_min=rate_limit_per_min, cap=cap)
        return _verifier.normalize_verdicts(raw, claims)
    vc = verify_cap if verify_cap is not None else CallCap(vcap_n)
    combined, _info = await _verifier.verify_with_models(
        source_text, claims, models, conn=conn, client=client,
        rate_limit_per_min=rate_limit_per_min, cap=cap, verify_cap=vc,
        flaky_double=(len(models) == 1))
    return combined


def retry_text(errors: list[str], budgets: dict[str, int]) -> str:
    """Yeniden uretim girdisi: somut hatalar + butce + ilk-cumle kurali."""
    base = "; ".join(errors[:6])
    return (f"{base} | kural: x ilk cumle en fazla {budgets['x_post']} karakter,"
            f" linkedin ilk cumle en fazla {budgets['linkedin_post']} karakter;"
            f" govdeler butceyi ASMAMALI (x<={budgets['x_post']},"
            f" linkedin<={budgets['linkedin_post']},"
            f" description<={budgets['description']})")


# --- Cumle destegi (iddia baglama) -------------------------------------------
# Degisim/zaman iddialari: yalnizca kaynak/senaryoda ayni iddia varsa gecer.
CHANGE_MARKERS = ("artık", "yeni", "değiştirdi", "now", "changed", "introduced")
COVERAGE_THRESHOLD = 0.70
SUPPORT_FIELDS = ("title", "description", "x_post", "linkedin_post")

# Gevsek Turkce yuklem adayi (baslik dilbilgisi uyarisi icin; ret degil).
_VERB_END_RX = re.compile(
    r"(di|dı|du|dü|ti|tı|tu|tü|miş|mış|muş|müş|yor|iyor|ıyor|uyor|üyor"
    r"|makta|mekte|malı|meli|acak|ecek|maz|mez|ar|er|ır|ir|ur|ür"
    r"|dır|dir|dur|dür|tır|tir|tur|tür|sın|sin|sun|sün"
    r"|yız|yiz|yuz|yüz|yım|yim|yum|yüm|mak|mek)$")


def _support_refs(script: dict, source_text: str) -> list[tuple[str, str]]:
    """Dayanak adaylari: (etiket, metin). Etiket claim#/hook/voice#/impact/action/kaynak#."""
    refs: list[tuple[str, str]] = []
    for j, c in enumerate(script.get("claims") or []):
        if isinstance(c, dict):
            t = str(c.get("text") or "").strip()
            if t:
                refs.append((f"claim#{j}", t))
    hook = str(script.get("hook") or "").strip()
    if hook:
        refs.append(("hook", hook))
    for i, sc in enumerate(script.get("scenes") or []):
        v = _scene_voice(sc).strip() if isinstance(sc, dict) else ""
        if v:
            refs.append((f"voice#{i}", v))
    for key in ("impact", "action"):
        v = str(script.get(key) or "").strip()
        if v:
            refs.append((key, v))
    for i, s in enumerate(split_sentences(source_text or "")):
        refs.append((f"kaynak#{i}", s))
    return refs


def _support_union(script: dict, source_text: str) -> str:
    """Destek karsilastirma metni: senaryo iddialari + voice + kaynak."""
    parts = [source_text or ""]
    for key in ("hook", "impact", "action"):
        parts.append(str(script.get(key) or ""))
    for sc in script.get("scenes") or []:
        if isinstance(sc, dict):
            parts.append(_scene_voice(sc))
    for c in script.get("claims") or []:
        if isinstance(c, dict):
            parts.append(str(c.get("text") or ""))
    return "\n".join(parts)


def sentence_coverage(sentence: str, ref_roots: set[str], ref_text: str) -> float:
    """Icerik sozcugu kapsama orani: isim/fil koku + sayi.

    `_root_of` ve `match_value` kullanilir (mevcut normalizasyon, ikinci
    kod yolu yok). Donus 0.0-1.0; iceriksiz cumle 0.0 sayilir.
    """
    from devbrief.script import verifier as _ver

    toks = [t for t in _WORD_RX.findall(sentence or "") if len(t) >= 2]
    nums = _ver._NUMBER.findall(sentence or "")
    total = len(toks) + len(nums)
    if total == 0:
        return 0.0
    hit = sum(1 for t in toks if _root_of(t) in ref_roots)
    hit += sum(1 for n in nums if _ver.match_value(n, ref_text) == "match")
    return hit / total


def best_basis(sentence: str, refs: list[tuple[str, str]]
               ) -> tuple[str, str, float]:
    """En yuksek kapsamali dayanak: (etiket, metin, kapsama). Esitlikte ilk."""
    best: tuple[str, str, float] = ("kaynak", "", 0.0)
    for label, text in refs:
        cov = sentence_coverage(sentence, _ref_roots(text), text)
        if cov > best[2]:
            best = (label, text, cov)
    return best


def draft_units(draft: MetadataDraft) -> list[dict]:
    """Taslak govde cumleleri: baslik tek birim, digerleri cumle listesi."""
    units = [{"field": "title", "sentence": (draft.title or "").strip()}]
    for s in split_sentences(draft.description or ""):
        units.append({"field": "description", "sentence": s})
    for s in normalize_sentences(draft.x_sentences):
        units.append({"field": "x_post", "sentence": s})
    for s in normalize_sentences(draft.linkedin_sentences):
        units.append({"field": "linkedin_post", "sentence": s})
    return [u for u in units if u["sentence"]]


def support_context(script: dict, source_text: str
                    ) -> tuple[str, set[str], set[str], list[tuple[str, str]]]:
    """Destek karsilastirma baglami: (birlesik metin, kokler, jetonlar, dayanaklar)."""
    union = _support_union(script, source_text)
    roots = _ref_roots(union)
    ref_toks = {_tr_lower(t) for t in _WORD_RX.findall(union)}
    refs = _support_refs(script, source_text)
    return union, roots, ref_toks, refs


def check_unit_support(unit: dict, union: str, roots: set[str],
                       ref_toks: set[str], refs: list[tuple[str, str]]
                       ) -> tuple[str | None, dict | None, str]:
    """Tek cumle destek denetimi.

    Donus: (katman | None, dayanak-bilgisi | None, mesaj).
    Katman `kural` (desteksiz degisim iddiasi) ya da `kapsama` (%70 alti);
    None = deterministik gecti. Dayanak-bilgisi `ref_text` (tam kanit),
    `basis`, `ref` (kisaltilmis) ve `coverage` tasir.
    """
    sent = unit["sentence"]
    toks = {_tr_lower(t) for t in _WORD_RX.findall(sent)}
    bad = [m for m in CHANGE_MARKERS if m in toks and m not in ref_toks]
    if bad:
        return ("kural", None,
                f"{unit['field']}: degisim iddiasi {bad[0]!r}"
                f" kaynakta/senaryoda yok (cumle: {sent[:120]})")
    cov = sentence_coverage(sent, roots, union)
    if cov < COVERAGE_THRESHOLD:
        return ("kapsama", None,
                f"{unit['field']}: cumle desteksiz atildi"
                f" ({cov:.0%} < %70) (cumle: {sent[:120]})")
    label, ref_text, _ = best_basis(sent, refs)
    return (None, {"basis": label, "ref_text": ref_text, "ref": ref_text[:160],
                   "coverage": round(cov, 2)}, "")


def filter_supported(units: list[dict], script: dict, source_text: str
                     ) -> tuple[list[dict], list[str], dict]:
    """Deterministik destek filtresi: kapsama %70 + degisim iddiasi kurali.

    Donus: (kalanlar [unit+ref_text], atilan-mesajlar, dayanak-haritasi).
    Atma sessizce ilerler; bosalan platform yeniden uretim gerektirir.
    """
    union, roots, ref_toks, refs = support_context(script, source_text)
    kept: list[dict] = []
    dropped: list[str] = []
    basis: dict = {}
    for u in units:
        layer, info, msg = check_unit_support(u, union, roots, ref_toks, refs)
        if layer is not None:
            dropped.append(msg)
            continue
        kept.append({**u, "ref_text": info["ref_text"] if info else u["sentence"]})
        basis[(u["field"], u["sentence"])] = info
    return kept, dropped, basis


def title_basis(script: dict) -> tuple[str | None, str | None]:
    """Senaryonun ana varligi + ana eylemi (kok; siklik, esitlikte ilk)."""
    texts: list[str] = []
    for c in script.get("claims") or []:
        if isinstance(c, dict) and str(c.get("text") or "").strip():
            texts.append(str(c["text"]))
    hook = str(script.get("hook") or "").strip()
    if hook:
        texts.append(hook)
    for sc in script.get("scenes") or []:
        if isinstance(sc, dict) and _scene_voice(sc).strip():
            texts.append(_scene_voice(sc))
    freq: dict[str, int] = {}
    for t in texts:
        for w in _WORD_RX.findall(t):
            if len(w) < 2:
                continue
            r = _root_of(w)
            if r not in freq:
                freq[r] = 0
            freq[r] += 1
    if not freq:
        return None, None
    ranked = sorted(freq, key=lambda r: -freq[r])  # kararli: esitlikte ilk
    entity = ranked[0]
    action = next((r for r in ranked if r != entity), None)
    return entity, action


def check_title_warnings(meta: Metadata, script: dict, source_text: str) -> list[str]:
    """Gevsek uyari (ret degil): fiilsiz isim yigini baslik suphelisi."""
    _ = (script, source_text)
    words = [w.strip(".,;:!?()\"'’`…–—-") for w in str(meta.title or "").split()]
    words = [w for w in words if w]
    if len(words) >= 4 and not any(
            _VERB_END_RX.search(_tr_lower(w)) for w in words):
        return ["title_grammar_suspect"]
    return []


def _final_body_sentences(text: str) -> list[str]:
    """FINAL metinden govde cumleleri (kod eki link/beyan arindirilir)."""
    t = URL_RX.sub(" ", _strip_ai_note(text or ""))
    out = []
    for s in split_sentences(t):
        if s.strip().lower().rstrip(":") in ("", "kaynak"):
            continue
        out.append(s)
    return out


def sentence_support_map(meta: Metadata, script: dict, source_text: str,
                         verdict: str = "") -> list[dict]:
    """Her FINAL govde cumlesinin dayanagi (saf, deterministik).

    Kayitli `sentence_support` yoksa (eski bolum) audit-pack bunu kullanir.
    """
    refs = _support_refs(script, source_text)
    out: list[dict] = []
    bodies = {"title": [str(meta.title or "")],
              "description": _final_body_sentences(meta.description),
              "x_post": _final_body_sentences(meta.x_post),
              "linkedin_post": _final_body_sentences(meta.linkedin_post)}
    for field, sents in bodies.items():
        for s in sents:
            label, ref_text, cov = best_basis(s, refs)
            out.append({"field": field, "sentence": s, "basis": label,
                        "ref": ref_text[:160], "coverage": round(cov, 2),
                        "verdict": verdict})
    return out


def _proper_noun_issues(field: str, text: str, roots: set[str]) -> list[str]:
    from devbrief.script.schemas import _MID_CAP_ALLOW as _ALLOW
    from devbrief.script.schemas import _PROPER_NOUN as _RX

    stoplist = load_proper_noun_stoplist()
    out: list[str] = []
    for sent in split_sentences(text or ""):
        toks = sent.split()
        # (a) cumle basi + basligin ilk sozcugu sayilmaz: toks[0] atlanir.
        for tok in toks[1:]:
            word = tok.strip(".,;:!?()\"'’`…–—-")
            if not word:
                continue
            base = _base_of_token(word).strip(".,;:!?()\"'’`…–—-")
            if not base or len(base) < 2:
                continue  # (c) en az iki karakterlik adaylar
            if not _RX.fullmatch(base):
                continue
            if base in _ALLOW:
                continue
            if _tr_lower(base) in stoplist:
                continue
            if base.isupper():
                continue
            if _root_of(base) in roots:
                continue
            out.append(f"{field}: ozel ad {base!r} senaryoda/kaynakta yok"
                       f" (cumle: {sent[:120]})")
            break  # cumle basina tek rapor yeterli
    return out


def _values(meta: Metadata) -> list[tuple[str, str]]:
    desc = _strip_ai_note(meta.description)
    return [("title", meta.title), ("description", desc), ("x_post", meta.x_post),
            ("linkedin_post", meta.linkedin_post),
            ("hashtags", " ".join(meta.hashtags))]


def check_metadata(meta: Metadata, script: dict, source_text: str) -> list[str]:
    """Sert ret listesi (bos = gecti). `meta` KOD eklenmis FINAL olmalidir.

    - Kaynak linki + beyan sarti yalnizca description/x_post/linkedin_post
      icin (kod ekledikten sonra; regresyon korumasi). Baslikta link sarti
      yok, aksine baslik link iceremez.
    - LLM govdesindeki kaynak/senaryoda olmayan her URL ret (uydurma link).
    - Sayi/surum/tarih/ozel ad: senaryoda VEYA kaynakta gecmeli (match_value).
    - Uzunluk: title <=70, x_post ham+agirlikli <=270, linkedin <=600,
      description <=5000, hashtags <=5.
    """
    from devbrief.script import verifier as _ver

    ref_texts = [source_text]
    for key in ("hook", "impact", "action"):
        ref_texts.append(str(script.get(key) or ""))
    for sc in script.get("scenes") or []:
        if isinstance(sc, dict):
            ref_texts.append(_scene_voice(sc))
    for c in script.get("claims") or []:
        if isinstance(c, dict):
            ref_texts.append(str(c.get("text") or ""))
    ref = "\n".join(ref_texts)
    roots = _ref_roots(ref)
    source_url = _clean_url(str(script.get("source_url") or ""))
    allowed_urls = _ref_urls(script, source_text)
    errors: list[str] = []
    vals = dict(_values(meta))
    raw = {"title": meta.title, "description": meta.description,
           "x_post": meta.x_post, "linkedin_post": meta.linkedin_post}
    # Link sarti: yalnizca govde alanlari, FINAL uzerinde.
    for field in ("description", "x_post", "linkedin_post"):
        text = vals.get(field, "")
        if source_url:
            if source_url not in text:
                errors.append(f"{field}: kaynak linki yok")
        elif "http" not in text:
            errors.append(f"{field}: kaynak linki yok")
    # Beyan sarti: platform bicimine gore (kod ekler; regresyon korumasi).
    for field, platform in (("description", "description"), ("x_post", "x"),
                            ("linkedin_post", "linkedin")):
        note = note_for(platform)
        if note and note not in raw.get(field, ""):
            errors.append(f"{field}: beyan cumlesi yok")
    # Baslik: senaryonun ana varligi + ana eylemini tasimali (ret).
    entity, action = title_basis(script)
    if entity is not None or action is not None:
        troots = {_root_of(w) for w in _WORD_RX.findall(vals.get("title", ""))
                  if len(w) >= 2}
        if entity is not None and entity not in troots:
            errors.append(f"title: ana varlik {entity!r} baslikta yok")
        if action is not None and action not in troots:
            errors.append(f"title: ana eylem {action!r} baslikta yok")
    # Baslik link icermez.
    if URL_RX.search(vals.get("title", "")):
        errors.append("title: link icermemeli")
    # Uydurma URL: kaynak/senaryoda olmayan her URL ret.
    for field, text in _values(meta):
        if field == "hashtags":
            continue
        check_text = text
        for u in extract_urls(check_text):
            if u not in allowed_urls:
                errors.append(f"{field}: uydurma URL {u!r} kaynakta/senaryoda yok")
    # Uzunluk kapilari (ARTIRILAMAZ).
    if len(vals.get("title", "")) > TITLE_LIMIT:
        errors.append(f"title: uzunluk asildi"
                      f" {len(vals['title'])}/{TITLE_LIMIT}")
    if len(meta.hashtags or []) > HASHTAG_MAX:
        errors.append(f"hashtags: uzunluk asildi"
                      f" {len(meta.hashtags)}/{HASHTAG_MAX}")
    if len(vals.get("x_post", "")) > X_LIMIT:
        errors.append(f"x_post: uzunluk asildi"
                      f" {len(vals['x_post'])}/{X_LIMIT}")
    if x_weighted_len(vals.get("x_post", "")) > X_LIMIT:
        errors.append(f"x_post: agirlikli uzunluk {x_weighted_len(vals['x_post'])}"
                      f" > {X_LIMIT} (link 23 sayilir)")
    if len(vals.get("linkedin_post", "")) > LINKEDIN_LIMIT:
        errors.append(f"linkedin_post: uzunluk asildi"
                      f" {len(vals['linkedin_post'])}/{LINKEDIN_LIMIT}")
    if len(vals.get("description", "")) > DESC_LIMIT:
        errors.append(f"description: uzunluk asildi"
                      f" {len(vals['description'])}/{DESC_LIMIT}")
    for field, text in _values(meta):
        if field == "hashtags":
            continue  # #Python3/#Web3: etiketler sayi/surum denetimi disi
        # URL icindeki rakam/surum/tarih kod eklentisidir, topraklama disi.
        gtext = URL_RX.sub(" ", _strip_ai_note(text))
        for v in dict.fromkeys(_ver._VERSION.findall(gtext) + _ver._PERCENT.findall(gtext)):
            if _ver.match_value(v, ref) == "miss":
                errors.append(f"{field}: {v!r} senaryoda/kaynakta yok")
        for v in dict.fromkeys(_ver._NUMBER.findall(gtext)):
            if not _ver._VERSION.fullmatch(v or "") and _ver.match_value(v, ref) == "miss":
                errors.append(f"{field}: {v!r} senaryoda/kaynakta yok")
        for v in dict.fromkeys(_ver._DATE.findall(gtext)):
            if _ver.match_value(v, ref) == "miss":
                errors.append(f"{field}: {v!r} senaryoda/kaynakta yok")
        errors.extend(_proper_noun_issues(field, gtext, roots))
    return errors


def check_hype(meta: Metadata, script: dict, source_text: str) -> list[str]:
    """Uyari duzeyi (reddetmez): abarti terimi metinde var, kaynakta/senaryoda yok."""
    from devbrief.script import verifier as _ver

    ref = _ver._norm(source_text + "\n" + _json.dumps(script, ensure_ascii=False))
    out = []
    for field, text in [("title", meta.title), ("x_post", meta.x_post)]:
        low = text.lower()
        for rx in load_hype_terms():
            m = rx.search(low)
            if m and m.group(0).lower() not in ref:
                code = "hype_title" if field == "title" else "hype_post"
                out.append(f"{code}:{m.group(0).lower()}")
    return sorted(set(out))


async def write_metadata(script: dict, source_url: str = "",
                         previous_errors: str = "yok",
                         source_text: str = "",
                         conn=None, client=None,
                         rate_limit_per_min: float | None = None,
                         tier: str = "smart",
                         cache_salt: str = "",
                         cache_info: dict | None = None,
                         cache_validator=None) -> MetadataDraft:
    """Onayli senaryodan yayin taslagi (X/LinkedIn cumle listeli).

    LLM metne URL/beyan yazmaz; cumle derleme + link/beyan `produce`
    asamasinda kodla yapilir. Govde butceleri ve onceki hatalar prompta
    karakter/kural olarak verilir. `cache_salt` (tur + hata hash'i) ret
    turune ayri onbellek anahtari verir. Varsayilan dogrulayici derleme +
    kural kapisidir (baglam eksikse fail-closed: saklamaz).
    """
    import hashlib as _hashlib

    src = (source_url or str(script.get("source_url") or "")).strip()
    budgets = body_budgets(src)
    if cache_validator is None:
        cache_validator = _validator_for(script, source_text)
    salt = cache_salt or f"turn=1|err={_hashlib.sha256(b'yok').hexdigest()[:16]}"
    out = await complete_json(
        "write_metadata", "write_metadata_v1",
        {"script": _json.dumps(script, ensure_ascii=False),
         "title_budget": str(budgets["title"]),
         "description_body_budget": str(budgets["description"]),
         "x_body_budget": str(budgets["x_post"]),
         "linkedin_body_budget": str(budgets["linkedin_post"]),
         "hashtag_max": str(HASHTAG_MAX),
         "previous_errors": previous_errors},
        MetadataDraft, tier=tier, temperature=0.6,
        conn=conn, client=client, rate_limit_per_min=rate_limit_per_min,
        cache_validator=cache_validator, cache_salt=salt, cache_info=cache_info,
    )
    return out


def _validator_for(script: dict, source_text: str):
    """Onbellek kapisi: YALNIZCA derleme + kural denetimini gecen saklanir."""

    def _check(obj: BaseModel) -> list[str]:
        if not isinstance(obj, MetadataDraft):
            return ["beklenmeyen sema"]
        _, errs, _ = assemble_final(obj, script, source_text)
        return errs

    return _check


def _call_snapshot(conn, last_id: int, info: dict | None = None) -> list[str]:
    """Son turun cagri izi: model(durum). '?' birakmaz.

    "cache(tekrar-kullanim)" YALNIZCA dogrulanmis onbellek vurusunda
    yazilir (basarili cikti); istemciye gidilmemis baska durumlarda
    "cagri-yok(hata-oncesi)" yazilir.
    """
    if info is not None and info.get("hit"):
        return ["cache(tekrar-kullanim)"]
    if conn is None:
        return ["baglanti-yok(bilinmiyor)"]
    try:
        cur = conn.execute("SELECT COALESCE(MAX(id), 0) FROM llm_calls").fetchone()
        max_id = int(cur[0] or 0)
    except Exception:  # noqa: BLE001
        return ["sorgu-hatasi(bilinmiyor)"]
    if max_id <= last_id:
        return ["cagri-yok(hata-oncesi)"]
    try:
        rows = conn.execute(
            "SELECT model, status, ok FROM llm_calls WHERE id > ?"
            " AND (task LIKE 'write_metadata%' OR task LIKE 'select_metadata%')"
            " ORDER BY id ASC LIMIT 10",
            (last_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return ["sorgu-hatasi(bilinmiyor)"]
    if not rows:
        return ["cache(tekrar-kullanim)"]
    out = []
    for r in rows:
        try:
            model = r["model"] or "model-yok"
            status = r["status"] or ("ok" if int(r["ok"] or 0) == 1 else "hata")
            if status == "?":
                status = "ok" if int(r["ok"] or 0) == 1 else "hata"
        except Exception:  # noqa: BLE001
            model, status = "model-yok", "hata"
        out.append(f"{model}({status})")
    return out


def _max_call_id(conn) -> int:
    if conn is None:
        return 0
    try:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM llm_calls").fetchone()
        return int(row[0] or 0)
    except Exception:  # noqa: BLE001
        return 0


async def produce(script: dict, source_text: str, conn=None, client=None,
                  rate_limit_per_min: float | None = None, tier: str = "smart",
                  cap=None, max_turns: int = MAX_TURNS
                  ) -> tuple[Metadata, list[str], dict, list[dict]]:
    """Denetimli uretim: ret -> somut hatayla en fazla `max_turns` yeniden deneme.

    Sira: (1) LLM cumle taslagi alinir. (2) Deterministik destek filtresi
    (kapsama %70 + degisim iddiasi); bosalan platform yeniden uretim.
    (3) TEK LLM dogrulama istegi (`verify_script` sozlesmesi, cap'e dahil);
    not_stated/contradicted cumle atilir, bosalan platform yeniden uretim.
    (4) Butce oneki + link/beyan + assert + kural denetimi.
    Donus: (metadata, uyari_listesi, butce_raporu, cumle_dayanaklari).
    Basarisizlikta MetadataFailed (son turun listesi `last_errors` alaninda).
    Cagri sayaci `cap` uzerinden bolum kotasina islenir. ValidationError
    dahil her uretim/uzunluk hatasi tura ve cap'e dahildir; islenmemis
    istisna sizdirmaz. Onbellek: ret turu tur+hata hash'i ile ayri anahtar
    alir; reddedilen yanit ikinci turda onbellekten GELMEZ.
    """
    import hashlib as _hashlib

    from devbrief.llm.client import CallCap, CallCapExceeded

    if cap is None:
        cap = CallCap(8)
    source_url = str(script.get("source_url") or "").strip()
    budgets = body_budgets(source_url)
    report = budget_report(source_url)
    validator = _validator_for(script, source_text)
    errors: list[str] = []
    history: list[str] = []
    previous_errors = "yok"
    turns = 0
    for _ in range(max_turns + 1):
        if cap:
            cap.charge()
        turns += 1
        last_id = _max_call_id(conn)
        info: dict = {}
        salt = (f"turn={turns}|err="
                f"{_hashlib.sha256(previous_errors.encode()).hexdigest()[:16]}")

        def _note(suffix: str, _turn: int = turns, _lid: int = last_id,
                  _info: dict = info) -> None:
            calls = _call_snapshot(conn, _lid, _info)
            history.append(f"tur{_turn} {', '.join(calls)}: {suffix[:300]}")

        try:
            draft = await write_metadata(
                script, source_url=source_url, previous_errors=previous_errors,
                source_text=source_text, conn=conn, client=client,
                rate_limit_per_min=rate_limit_per_min, tier=tier,
                cache_salt=salt, cache_info=info, cache_validator=validator)
        except CallCapExceeded:
            raise
        except ValidationError as e:
            errors = [f"uzunluk/sema asildi: {_short_validation(e)}"]
            _note("; ".join(errors))
            previous_errors = retry_text(errors, budgets)
            continue
        except Exception as e:  # noqa: BLE001 - uretim hatasi da tur sayilir
            errors = [f"uretim: {type(e).__name__}: {e}"[:300]]
            _note("; ".join(errors))
            previous_errors = retry_text(errors, budgets)
            continue
        kept, dropped, basismap = filter_supported(
            draft_units(draft), script, source_text)
        by: dict[str, list[dict]] = {}
        for u in kept:
            by.setdefault(u["field"], []).append(u)
        empty = [f for f in SUPPORT_FIELDS if not by.get(f)]
        if empty:
            errors = list(dropped) + [
                f"{f}: hic destekli cumle kalmadi, yeniden uretim gerekli"
                for f in empty]
            _note("; ".join(errors[:4]))
            previous_errors = retry_text(errors, budgets)
            continue
        try:
            verdict = await _verify_units(kept, source_text, conn, client,
                                          rate_limit_per_min, cap)
        except CallCapExceeded:
            raise
        except Exception as e:  # noqa: BLE001 - dogrulama hatasi da tur sayilir
            errors = [f"dogrulama: {type(e).__name__}: {e}"[:300]]
            _note("; ".join(errors))
            previous_errors = retry_text(errors, budgets)
            continue
        survivors: list[dict] = []
        llm_dropped: list[str] = []
        for u, v in zip(kept, verdict.verdicts, strict=True):
            if v.verdict == "entailed":
                survivors.append(u)
            else:
                llm_dropped.append(
                    f"{u['field']}: cumle {v.verdict}"
                    f" ({(v.note or '')[:120]}) (cumle: {u['sentence'][:120]})")
        by2: dict[str, list[dict]] = {}
        for u in survivors:
            by2.setdefault(u["field"], []).append(u)
        empty2 = [f for f in SUPPORT_FIELDS if not by2.get(f)]
        if empty2:
            errors = list(llm_dropped) + [
                f"{f}: hic destekli cumle kalmadi, yeniden uretim gerekli"
                for f in empty2]
            _note("; ".join(errors[:4]))
            previous_errors = retry_text(errors, budgets)
            continue
        meta, gate_errors, truncated, support = _assemble_lists(
            by2["title"][0]["sentence"],
            " ".join(u["sentence"] for u in by2["description"]),
            draft.hashtags,
            [u["sentence"] for u in by2["x_post"]],
            [u["sentence"] for u in by2["linkedin_post"]],
            script, source_text, basismap)
        if meta is None:
            info.pop("hit", None)  # ret detayi onbellek etiketi tasimaz
            errors = list(llm_dropped) + list(gate_errors)
            _note("; ".join(errors[:4]))
            previous_errors = retry_text(errors, budgets)
            continue
        warns = list(truncated)
        warns += check_title_warnings(meta, script, source_text)
        warns += check_hype(meta, script, source_text)
        if info.get("hit"):
            warns.append("cache(tekrar-kullanim)")
        return meta, warns, report, support
    detail = "; ".join(errors[:6])
    trail = " | ".join(history)
    raise MetadataFailed(f"metadata ret ({turns} tur): {trail} :: {detail}",
                         last_errors=errors)


def _short_validation(exc: Exception) -> str:
    """Pydantic hatasini tek satir somut ozete indir (200 karakter)."""
    try:
        errors = exc.errors()  # type: ignore[attr-defined]
    except AttributeError:
        return str(exc)[:200]
    try:
        parts = []
        for e in errors[:4]:
            loc = ".".join(str(x) for x in e.get("loc", ()))
            parts.append(f"{loc or '(kok)'}: {e.get('msg', '')}")
        return "; ".join(parts)[:200] or str(exc)[:200]
    except Exception:  # noqa: BLE001
        return str(exc)[:200]


def save_metadata(conn: sqlite3.Connection, episode_id: int, meta: Metadata,
                  warnings: list[str] | None = None,
                  budget: dict | None = None,
                  support: list[dict] | None = None) -> None:
    """FINAL metni + uyarilar + butce hesabi + cumle dayanaklarini saklar.

    `metadata_json` icine `budget` alani (govde butcesi, link/beyan payi) ve
    `sentence_support` alani (cumle -> iddia/kaynak) yazilir; onay ekrani ve
    audit-pack aynen gosterir.
    """
    import devbrief.db as db

    payload = meta.model_dump(mode="json")
    payload["budget"] = budget or {}
    payload["sentence_support"] = support or []
    with conn:
        conn.execute("UPDATE episodes SET title = ?, description = ?, metadata_json = ?,"
                      " metadata_warnings = ?, updated_at = ? WHERE id = ?",
                      (meta.title, meta.description, _json.dumps(payload),
                       _json.dumps(warnings or [], ensure_ascii=False),
                       db.now_iso(), episode_id))


def fail_metadata(conn: sqlite3.Connection, episode_id: int, reason: str) -> None:
    import devbrief.db as db

    with conn:
        conn.execute("UPDATE episodes SET status = 'metadata_failed', reject_reason = ?,"
                     " updated_at = ? WHERE id = ?",
                     (reason[:2000], db.now_iso(), episode_id))


# --- Secimli (extractive) metadata uretimi -----------------------------------
# LLM yalnizca havuz indeksleri secer (serbest metin YOK); cumleler, link,
# beyan ve butce birlestirme tamamen kodladir. Cikti cumleleri normalize
# edilmis bicimde havuzda birebir bulunur (baslik kirpmasi haric: hook one ki).

HASHTAG_VOCAB_PATH = "config/hashtag_vocab.yaml"
EXTRACTIVE_CALL_CAP = 6  # tur (3) + secim (3); onarim yok, tek secim cagrisi/tur


class MetadataSelection(BaseModel):
    """LLM secim ciktisi (extractive): YALNIZCA indeksler, serbest metin YOK."""

    model_config = ConfigDict(extra="forbid")

    title_idx: int
    x_idx: list[int] = Field(min_length=1)
    linkedin_idx: list[int] = Field(min_length=1)
    hashtags: list[str] = Field(default_factory=list, max_length=HASHTAG_MAX)


def normalize_sentence(s: str) -> str:
    """Birebir uyelik icin normal form: kirp + bosluklari tekle (harf aynen)."""
    return " ".join(str(s or "").split())


def _scene_voice(sc: dict) -> str:
    """Sahne konusma metni (cumle dizisi varsa birlesim, yoksa voice)."""
    from devbrief.script.schemas import scene_voice as _sv

    return _sv(sc) if isinstance(sc, dict) else ""


def _scene_voice_units(sc: dict) -> list[str]:
    """Havuz icin cumleler: yazar dizisi oncelikli, yoksa voice bolunur."""
    from devbrief.script.schemas import scene_units as _su

    if isinstance(sc, dict):
        units = _su(sc)
        if units:
            return [t for t, _ in units if t.strip()]
    return split_sentences(_scene_voice(sc))


def build_pool(script: dict) -> list[dict]:
    """Aday havuzu (kodla, LLM'siz): hook, voice cumleleri, fact claim'ler,
    impact, action. Donus [{"idx","field","text"}]; kararli sira, birebir
    tekrarlar teklenir."""
    items: list[tuple[str, str]] = []
    hook = str(script.get("hook") or "").strip()
    if hook:
        items.append(("hook", hook))
    for sc in script.get("scenes") or []:
        if isinstance(sc, dict):
            for s in _scene_voice_units(sc):
                items.append(("voice", s))
    for c in script.get("claims") or []:
        if isinstance(c, dict) and str(c.get("kind") or "fact") == "fact":
            t = str(c.get("text") or "").strip()
            if t:
                items.append(("claim", t))
    for key in ("impact", "action"):
        t = str(script.get(key) or "").strip()
        if t:
            items.append((key, t))
    pool: list[dict] = []
    seen: set[str] = set()
    for field, text in items:
        norm = normalize_sentence(text)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        pool.append({"idx": len(pool), "field": field, "text": text.strip()})
    return pool


@lru_cache(maxsize=4)
def load_hashtag_vocab(path: str = HASHTAG_VOCAB_PATH) -> set[str]:
    """Sabit hashtag sozlugu (kucuk harf duyarsiz, Turkce I/i ile)."""
    import yaml

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return set()
    terms = data.get("terms") or []
    return {_tr_lower(str(t)) for t in terms if str(t).strip()}


def scenario_hashtag_words(script: dict, source_text: str) -> set[str]:
    """Senaryo/kaynakta gecen sozcukler (kucuk harf duyarsiz, koksuz)."""
    texts = [source_text or ""]
    for key in ("hook", "impact", "action"):
        texts.append(str(script.get(key) or ""))
    for sc in script.get("scenes") or []:
        if isinstance(sc, dict):
            texts.append(_scene_voice(sc))
    for c in script.get("claims") or []:
        if isinstance(c, dict):
            texts.append(str(c.get("text") or ""))
    out: set[str] = set()
    for t in texts:
        out.update(_tr_lower(w) for w in _WORD_RX.findall(t or ""))
    return out


def filter_hashtags(tags: list[str] | None, script: dict, source_text: str) -> list[str]:
    """Hashtag eleme: senaryo/kaynakta gecen ya da sabit sozlukteki kalir,
    digerleri ATILIR (ret degil). Bas # temizlenir, harf korunur."""
    vocab = load_hashtag_vocab()
    allowed = scenario_hashtag_words(script, source_text)
    out: list[str] = []
    for t in tags or []:
        name = str(t).strip().lstrip("#").strip()
        if not name:
            continue
        if _tr_lower(name) in allowed or _tr_lower(name) in vocab:
            if name not in out:
                out.append(name)
    return out


def parse_selection(raw: str) -> MetadataSelection:
    """Ham LLM metni -> secim. Uretimdeki cozumleme ile birebir ayni yol
    (extract + unwrap + validate); hata firlatir (ret)."""
    from devbrief.llm.client import extract_json, unwrap_envelope

    payload = unwrap_envelope(extract_json(raw))
    if not isinstance(payload, dict):
        raise ValueError("secim JSON obje degil")
    return MetadataSelection.model_validate(payload)


def check_selection_indices(sel: MetadataSelection, pool: list[dict]) -> list[str]:
    """Indeks aralik + bosluk denetimi (TEK choke noktasi). Bos liste = gecti.

    Gecersiz/aralik disi indeks = ret (somut liste, yeniden uretim turu).
    """
    n = len(pool)
    errs: list[str] = []
    ti = sel.title_idx
    if not isinstance(ti, int) or isinstance(ti, bool) or not (0 <= ti < n):
        errs.append(f"title_idx aralik disi: {ti!r} (0..{n - 1}, havuz {n} oge)")
    for name in ("x_idx", "linkedin_idx"):
        idxs = getattr(sel, name)
        if not idxs:
            errs.append(f"{name} bos olamaz (en az 1 indeks)")
            continue
        bad = [i for i in idxs
               if not isinstance(i, int) or isinstance(i, bool) or not (0 <= i < n)]
        if bad:
            errs.append(f"{name} aralik disi: {bad!r} (0..{n - 1}, havuz {n} oge)")
    return errs


def resolve_title(pool: list[dict], title_idx: int, script: dict
                  ) -> tuple[str, int, bool]:
    """Baslik cozumleme: (baslik, dayanak_idx, kesildi_mi).

    Secili oge >70 ise sonraki <=70 ogelerden ilki; hicbiri yoksa hook
    son kelime sinirinda kesilir (`title_cut` uyarisi).
    """
    item = pool[title_idx]["text"]
    if len(item) <= TITLE_LIMIT:
        return item, title_idx, False
    for j in range(title_idx + 1, len(pool)):
        if len(pool[j]["text"]) <= TITLE_LIMIT:
            return pool[j]["text"], j, False
    hook = str((script or {}).get("hook") or "")
    kept: list[str] = []
    for w in hook.split():
        cand = f"{' '.join(kept)} {w}".strip() if kept else w
        if len(cand) <= TITLE_LIMIT:
            kept.append(w)
        else:
            break
    title = " ".join(kept) or hook[:TITLE_LIMIT]
    hook_idx = next((p["idx"] for p in pool if p["field"] == "hook"), title_idx)
    return title, hook_idx, True


def assert_pool_membership(bodies: dict[str, str], pool: list[dict],
                           title_cut_from: str | None) -> list[str]:
    """Son iddia: her govde cumlesi normalize havuzda BIREBIR olmali.

    `title_cut_from` verildiyse baslik icin hook-oneki kuralı gecerli
    (normalize baslik, kesme kaynaginin one kidir). Link/beyan kod
    eklentisi denetim disidir (cagri oncesi govdeler verilir).
    """
    norms = {normalize_sentence(p["text"]) for p in pool}
    cut_norm = normalize_sentence(title_cut_from) if title_cut_from else ""
    errs: list[str] = []
    for field, text in bodies.items():
        for s in split_sentences(text):
            n = normalize_sentence(s)
            if not n:
                continue
            if n in norms:
                continue
            if field == "title" and cut_norm and cut_norm.startswith(n):
                continue
            errs.append(f"{field}: cumle havuzda yok: {s[:80]!r}")
    return errs


def _select_validator_for(pool: list[dict], script: dict, source_text: str):
    """Onbellek kapisi: YALNIZCA tam derlemeyi gecen secim saklanir."""

    def _check(obj: BaseModel) -> list[str]:
        if not isinstance(obj, MetadataSelection):
            return ["beklenmeyen sema"]
        _, errs, _, _ = assemble_extractive(pool, obj, script, source_text)
        return errs

    return _check


async def select_metadata(pool: list[dict], x_budget: int, linkedin_budget: int,
                          previous_errors: str = "yok",
                          conn=None, client=None,
                          rate_limit_per_min: float | None = None,
                          tier: str = "smart",
                          cache_salt: str = "",
                          cache_info: dict | None = None,
                          cache_validator=None, cap=None) -> MetadataSelection:
    """Havuzdan indeks secimi: TEK LLM cagrisi, onarim YOK (ret + tur).

    `max_repairs=0`: sema/JSON hatasi ham firlar, yeniden uretim turune girer.
    Donen secim HAMDIR (indeks araligi + derleme `assemble_extractive` yapar);
    onbellek kapisi icin `cache_validator` verilir (uretim verir).
    """
    import hashlib as _hashlib

    pool_text = "\n".join(f"[{p['idx']}] ({p['field']}) {p['text']}" for p in pool)
    salt = cache_salt or f"sel=1|err={_hashlib.sha256(b'yok').hexdigest()[:16]}"
    out = await complete_json(
        "select_metadata", "select_metadata_v1",
        {"pool": pool_text, "pool_size": str(len(pool)),
         "x_budget": str(x_budget), "linkedin_budget": str(linkedin_budget),
         "hashtag_max": str(HASHTAG_MAX), "previous_errors": previous_errors},
        MetadataSelection, tier=tier, temperature=0.2,
        conn=conn, client=client, rate_limit_per_min=rate_limit_per_min,
        cache_validator=cache_validator, cache_salt=salt, cache_info=cache_info,
        max_repairs=0, cap=cap,
    )
    return out


def _dedup_idx(idxs: list[int]) -> list[int]:
    return list(dict.fromkeys(idxs))


def assemble_extractive(pool: list[dict], sel: MetadataSelection,
                        script: dict, source_text: str
                        ) -> tuple[Metadata | None, list[str], list[str], list[dict]]:
    """Secim -> FINAL (deterministik). Donus: (final|None, hatalar, uyarilar,
    dayanaklar [`basis=pool#idx`, verdict extractive]).

    Sira: indeks denetimi -> hashtag eleme -> baslik cozumleme -> havuz
    uyelik iddiasi -> butce oneki + ek + assert + kural (`_assemble_lists`).
    Havuz ogeleri cok cumleli olabilir; dayanak her govde cumlesine
    geldigi havuz indeksini isler.
    """
    errs = check_selection_indices(sel, pool)
    if errs:
        return None, errs, [], []
    tags = filter_hashtags(sel.hashtags, script, source_text)
    title, basis_idx, cut = resolve_title(pool, sel.title_idx, script)
    x_resolved = [(s, i) for i in _dedup_idx(sel.x_idx)
                  for s in split_sentences(pool[i]["text"])]
    li_resolved = [(s, i) for i in _dedup_idx(sel.linkedin_idx)
                   for s in split_sentences(pool[i]["text"])]
    x_body = " ".join(s for s, _ in x_resolved)
    li_body = " ".join(s for s, _ in li_resolved)
    cut_from = pool[basis_idx]["text"] if cut else None
    merrs = assert_pool_membership(
        {"title": title, "description": li_body,
         "x_post": x_body, "linkedin_post": li_body},
        pool, cut_from)
    if merrs:
        return None, merrs, [], []
    basismap: dict = {}
    basismap[("title", title)] = {"basis": f"pool#{basis_idx}",
                                  "ref": pool[basis_idx]["text"][:160],
                                  "coverage": 1.0}
    for s, i in li_resolved:
        for field in ("description", "linkedin_post"):
            basismap.setdefault((field, s), {"basis": f"pool#{i}",
                                             "ref": pool[i]["text"][:160],
                                             "coverage": 1.0})
    for s, i in x_resolved:
        basismap.setdefault(("x_post", s), {"basis": f"pool#{i}",
                                            "ref": pool[i]["text"][:160],
                                            "coverage": 1.0})
    meta, errs2, warns, support = _assemble_lists(
        title, li_body, tags,
        [s for s, _ in x_resolved], [s for s, _ in li_resolved],
        script, source_text, basismap, verdict="extractive")
    if cut:
        warns = [w for w in warns if w != "title_cut"] + ["title_cut"]
    return meta, errs2, warns, support


def resolve_metadata_mode(explicit: str | None = None) -> str:
    """Uretim modu coz: bayrak > METADATA_MODE > varsayilan (extractive)."""
    raw = explicit if explicit is not None else get_settings().metadata_mode
    m = str(raw or "extractive").strip().lower()
    if m not in ("extractive", "generative"):
        raise ValueError(f"METADATA_MODE {raw!r} gecersiz (extractive|generative olmali)")
    return m


def require_generative_gate(conn) -> None:
    """Uretken mod kilidi: kayitli gecmis kapi sonucu yoksa acik hata."""
    import devbrief.db as db

    rec = db.get_eval_gate(conn, "eval-metadata")
    if rec is None or not rec.get("passed"):
        raise MetadataFailed(
            "generative secilemez: eval-metadata kapisi gecilmemis (kayit yok); "
            "once `devbrief eval-metadata` calistir",
            last_errors=["generative secilemez: eval-metadata kapisi gecilmemis"])


async def produce_extractive(script: dict, source_text: str, conn=None, client=None,
                             rate_limit_per_min: float | None = None,
                             tier: str = "smart",
                             cap=None, max_turns: int = MAX_TURNS
                             ) -> tuple[Metadata, list[str], dict, list[dict]]:
    """Secimli uretim: tur basina TEK LLM cagrisi (indeks secimi, onarimsiz).

    Sira: (1) havuz (kodla). (2) secim cagrisi. (3) indeks+uyelik
    denetimi; ret -> somut hatayla yeniden uretim. (4) Butce oneki +
    link/beyan + assert + kural denetimi. Donus produce() ile ayni
    4'lu (metadata, uyari, butce, dayanak; verdict extractive).
    """
    import hashlib as _hashlib

    from devbrief.llm.client import CallCap, CallCapExceeded

    if cap is None:
        cap = CallCap(EXTRACTIVE_CALL_CAP)
    source_url = str(script.get("source_url") or "").strip()
    budgets = body_budgets(source_url)
    report = budget_report(source_url)
    pool = build_pool(script)
    if not pool:
        raise MetadataFailed("havuz bos: senaryoda secilebilir cumle yok",
                             last_errors=["havuz bos"])
    validator = _select_validator_for(pool, script, source_text)
    errors: list[str] = []
    history: list[str] = []
    previous_errors = "yok"
    turns = 0
    for _ in range(max_turns + 1):
        if cap:
            cap.charge()
        turns += 1
        last_id = _max_call_id(conn)
        info: dict = {}
        salt = (f"sel-turn={turns}|err="
                f"{_hashlib.sha256(previous_errors.encode()).hexdigest()[:16]}")

        def _note(suffix: str, _turn: int = turns, _lid: int = last_id,
                  _info: dict = info) -> None:
            calls = _call_snapshot(conn, _lid, _info)
            history.append(f"tur{_turn} {', '.join(calls)}: {suffix[:300]}")

        try:
            sel = await select_metadata(
                pool, budgets["x_post"], budgets["linkedin_post"],
                previous_errors=previous_errors,
                conn=conn, client=client,
                rate_limit_per_min=rate_limit_per_min, tier=tier,
                cache_salt=salt, cache_info=info, cache_validator=validator,
                cap=cap)
        except CallCapExceeded:
            raise
        except ValidationError as e:
            errors = [f"secim/sema asildi: {_short_validation(e)}"]
            _note("; ".join(errors))
            previous_errors = "; ".join(errors[:6])
            continue
        except Exception as e:  # noqa: BLE001 - secim hatasi da tur sayilir
            errors = [f"secim: {type(e).__name__}: {e}"[:300]]
            _note("; ".join(errors))
            previous_errors = "; ".join(errors[:6])
            continue
        meta, gate_errors, truncated, support = assemble_extractive(
            pool, sel, script, source_text)
        if meta is None:
            info.pop("hit", None)  # ret detayi onbellek etiketi tasimaz
            errors = list(gate_errors)
            _note("; ".join(errors[:4]))
            previous_errors = "; ".join(errors[:6])
            continue
        warns = list(truncated)
        warns += check_title_warnings(meta, script, source_text)
        warns += check_hype(meta, script, source_text)
        if info.get("hit"):
            warns.append("cache(tekrar-kullanim)")
        return meta, warns, report, support
    detail = "; ".join(errors[:6])
    trail = " | ".join(history)
    raise MetadataFailed(f"metadata ret ({turns} tur): {trail} :: {detail}",
                         last_errors=errors)


async def produce_auto(script: dict, source_text: str, conn=None, client=None,
                       rate_limit_per_min: float | None = None, tier: str = "smart",
                       cap=None, max_turns: int = MAX_TURNS,
                       mode: str | None = None
                       ) -> tuple[Metadata, list[str], dict, list[dict]]:
    """Mod yonlendirmeli uretim (CLI/pipeline giris noktasi).

    Mod cozumleme: bayrak > METADATA_MODE > extractive. Generative secimde
    kayitli kapi sonucu sart (yoksa acik hata).
    """
    from devbrief.llm.client import CallCap

    m = resolve_metadata_mode(mode)
    if m == "generative":
        require_generative_gate(conn)
        if cap is None:
            cap = CallCap(8)
        return await produce(script, source_text, conn=conn, client=client,
                             rate_limit_per_min=rate_limit_per_min, tier=tier,
                             cap=cap, max_turns=max_turns)
    if cap is None:
        cap = CallCap(6)
    return await produce_extractive(
        script, source_text, conn=conn, client=client,
        rate_limit_per_min=rate_limit_per_min, tier=tier,
        cap=cap, max_turns=max_turns)
