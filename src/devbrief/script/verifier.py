"""Kural tabanli dogrulayici (LLM'siz). LLM dogrulayici Faz 2 kilidinde.

Senaryodaki somut degerler kaynak metinde birebir gecmeli (uydurma yasagi):
surum (1.2 / 1.2.3), yuzde (%40), tarih (09.10.2026, 2026-10-09, yil),
sayi (rakamla yazilan) ve kod ifadeleri. Gecmeyen her deger bir Issue'dur.
Bos liste = gecti. Yeniden yazim en fazla 2 tur (MAX_REWRITE_TURNS).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache as _lru_cache

from devbrief.script.schemas import IndexVerdict, VerifyOut

MAX_REWRITE_TURNS = 2
# Bolum kotasi varsayilani (kosu butcesiyle ayni kaynaktan: pipeline.run_call_cap).
EPISODE_CALL_CAP = 12

# --- Siki kanit katmanlari (uydurma-kanit acigi) ---------------------------
# Yazarin her fact evidence'i, kaynak bir cumleye BIREBIR uymali; uymayan
# iddia LLM'e gitmeden unsupported. Mevcut yakin-alt-dize esigi (0.9/0.85)
# SADECE verifier girdisi (evidence_match) icin birakildi, gevsetilmedi.
EVIDENCE_MIN_TOKENS = 4  # alti yargisiz (evidence_match ile ayni kural)
EVIDENCE_VERBATIM_DIFFLIB = 0.95
EVIDENCE_COVERAGE_MIN = 0.70
CLAIM_GAP_LIMIT = 2  # karsiliksiz icerik koku >=2 ise unsupported

# Islev sozcukleri (icerik koku sayilmaz; TR + kisa EN listesi).
_TR_STOPWORDS = frozenset(
    "ve bir bu su o da de ki mi ne çok daha en ile için gibi kadar ise ama fakat "
    "veya hem şey olan olarak sonra önce hep yine bile zaten sadece yalnızca karşı "
    "rağmen hangi nasıl neler the a an in on of and to or is are was were be been "
    "with from".split()
)

# Turetilmis claim etiket esigi: daha kisa cumleler etiket sayilir, (b)/(c)
# uygulanmaz (sayi/ozel-ad kurali (a) her zaman gecerlidir).
MIN_VOICE_ROOTS = 4
# Belge-dil esigi: senaryo koklerinin bu orandan azi kaynakta geciyorsa
# capraz-dil sayilir; kok-ortusme katmanlari ((b), claim boslugu) uygulanmaz.
# (Sayi/ozel-ad (a) + LLM (c) dilden bagimsizdir, her zaman calisir.)
DOC_LANG_THRESHOLD = 0.30
# Cikarim eslesme esigi: voice cumlesi yazar inference koklerinin en az
# bu oranini tasimalidir (yorum istisnasi icin).
INFERENCE_MATCH_MIN = 0.50
MAX_INFERENCE_SENTENCES = 2


def _doc_same_language(script: dict, source_text: str,
                         thr: float = DOC_LANG_THRESHOLD) -> bool:
    """Belge-dil karari: senaryo harf-koklerinin `thr` ve uzeri kaynakta
    geciyorsa ayni-dil sayilir (siki kok katmanlari anlamlidir).

    Capraz-dilde kok-ortusmesi ayirt edici degildir (iyi ozet bile dusuk
    skor alir); o durumda yalnizca (a) sayi/ozel-ad + (c) LLM calisir.
    """
    from devbrief.script.schemas import scene_units as _units

    parts = [str(script.get("hook") or "")]
    for sc in script.get("scenes") or []:
        if isinstance(sc, dict):
            parts.append(str(sc.get("on_screen") or ""))
            parts.extend(t for t, _ in _units(sc))
    for key in ("impact", "action"):
        parts.append(str(script.get(key) or ""))
    for c in script.get("claims") or []:
        if isinstance(c, dict):
            parts.append(str(c.get("text") or ""))
    s_roots = _content_roots("\n".join(parts))
    if not s_roots:
        return True
    src_roots = _content_roots(source_text)
    if not src_roots:
        return True
    return len(s_roots & src_roots) / len(s_roots) >= thr


def _split_sentences(text: str) -> list[str]:
    """Cumle bolme TEK kod yolu (metadata ile ayni regex; src indeksleri
    yazar numaralandirmasiyla birebir ortusmelidir)."""
    from devbrief.script.metadata import split_sentences as _ms

    return _ms(text)


def number_source_sentences(source_text: str) -> tuple[str, list[str]]:
    """Kaynak cumleleri numarala: ("[0] ...\\n[1] ...", [cumleler]).

    Numarali metin SADECE prompt girdisidir; dogrulama ham metinle calisir.
    Numaralar `_split_sentences` sirasidir (src denetimiyle ayni).
    """
    sents = _split_sentences(source_text)
    numbered = "\n".join(f"[{i}] {s}" for i, s in enumerate(sents))
    return numbered, sents


def _match_root(word: str) -> str:
    """Eslesme kok'u: _root_of + ı→i katlama.

    Turkce'de ASCII "I" (U+0049) kucuk harfi "ı" (U+0131), "i" (U+0069) ise
    ayri harftir; ama klavye aliskanligiyla duz metinde karisir ("Indirme"
    cumle basi vs "indirme"). Birebir katilik diger katmanlardadir
    (verbatim/sayi/ozel-ad); kok ortusmede katlama yalnizca sahte
    desteksizligi onler (yalnizca verifier, metadata'ya dokunulmaz).
    """
    from devbrief.script.metadata import _root_of

    return _root_of(word).replace("ı", "i")


def _content_roots(text: str) -> set[str]:
    """Icerik kokleri: harf jetonlari (uzunluk>=2, stopword disi) -> _match_root."""
    from devbrief.script.metadata import _WORD_RX, _tr_lower

    return {_match_root(t) for t in _WORD_RX.findall(text or "")
            if len(t) >= 2 and _tr_lower(t) not in _TR_STOPWORDS}


def _word_roots_all(text: str) -> set[str]:
    """Tum sozcuk kokleri (stopword filtresiz; kanit tarafi eslesme icin)."""
    from devbrief.script.metadata import _WORD_RX

    return {_match_root(t) for t in _WORD_RX.findall(text or "") if len(t) >= 2}


def _claim_numbers(text: str) -> list[str]:
    """Claim'deki sayilarin rakam cekirdekleri (surum + sayi)."""
    out = []
    for n in _VERSION.findall(text or "") + _NUMBER.findall(text or ""):
        d = "".join(_DIGITS.findall(n))
        if d:
            out.append(d)
    return out


def _claim_proper_roots(text: str, skip_first: bool = True) -> set[str]:
    """Ozel ad kokleri (schemas kuraliyla: cumle-ortasi buyuk harf)."""
    from devbrief.script.schemas import _MID_CAP_ALLOW, _PROPER_NOUN

    out: set[str] = set()
    for sent in _split_sentences(text):
        toks = sent.split()
        seq = toks[1:] if skip_first else toks
        for tok in seq:
            word = tok.strip(".,;:!?()\"'")
            if (word and _PROPER_NOUN.fullmatch(word) and not word.isupper()
                    and word not in _MID_CAP_ALLOW):
                from devbrief.script.metadata import _base_of_token

                out.add(_match_root(_base_of_token(word)))
    return out


def _best_evidence_sentence(evidence: str, source_text: str) -> tuple[int | None, float]:
    """Kanita en yakin kaynak cumlesi: (indeks | None, difflib orani)."""
    from difflib import SequenceMatcher

    ev = _norm(evidence)
    best: tuple[int | None, float] = (None, 0.0)
    for i, s in enumerate(_split_sentences(source_text)):
        r = SequenceMatcher(None, ev, _norm(s)).ratio()
        if r > best[1]:
            best = (i, r)
    return best


def _abbreviations(text: str) -> list[str]:
    """Kisaltma adaylari: >=2 buyuk harf tasiyan jetonlar (API, JSON, GitHub,
    DB, C++). Kaynakta birebir (harf duyarsiz) aranir; gecenler serbesttir."""
    out = []
    for tok in dict.fromkeys(_TOKEN.findall(text or "")):
        word = tok.strip(".,;:!?()\"'")
        up = sum(1 for ch in word if ch.isupper())
        if len(word) >= 2 and (up >= 2 or "++" in word or "#" in word):
            out.append(word)
    return out


#: Sayi sozcukleri -> rakam (EN 0-20+tens+100, TR 0/2-20+tens+100).
#: Turkce "bir" ASLA cevrilmez (belgisiz sifat belirsizligi); Ingilizce
#: "one" yalnizca sayi baglaminda sayilir (_one_in_numeric_context).
#: Yalnizca oge cikarimi/tanisal gosterge icin; karar esikleri aynidir.
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100",
    "sıfır": "0", "sifir": "0", "iki": "2", "üç": "3",
    "uc": "3", "dört": "4", "dort": "4", "beş": "5", "bes": "5",
    "altı": "6", "alti": "6", "yedi": "7", "sekiz": "8", "dokuz": "9",
    "on": "10", "on bir": "11", "onbir": "11", "on iki": "12",
    "oniki": "12", "on üç": "13", "onuc": "13", "on dört": "14",
    "ondort": "14", "on beş": "15", "onbes": "15", "on altı": "16",
    "onalti": "16", "on yedi": "17", "onyedi": "17", "on sekiz": "18",
    "onsekiz": "18", "on dokuz": "19", "ondokuz": "19", "yirmi": "20",
    "otuz": "30", "kırk": "40", "kirk": "40", "elli": "50",
    "altmış": "60", "altmis": "60", "yetmiş": "70", "yetmis": "70",
    "seksen": "80", "doksan": "90", "yüz": "100", "yuz": "100",
}
_NUMBER_WORD_RX = re.compile(
    r"\b(" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)

#: "one" sonrasi sayi baglami sayilan birim/sifatlar (+ cogullari).
_ONE_UNITS = frozenset({
    "critical", "high", "medium", "low",
    "month", "months", "day", "days", "year", "years",
    "bug", "bugs", "release", "releases",
})
#: "one" onu sayi sayilan onceki jetonlar (sayi sifatlari; "bir" haric).
_ONE_PRECEDING_NUMBERS = frozenset(
    w for w in _NUMBER_WORDS if w not in ("one", "bir") and " " not in w)


def _one_in_numeric_context(text: str, start: int, end: int) -> bool:
    """'one' yalnizca sayi baglaminda sayilir: sonrasinda birim/sifat
    (critical/high/medium/low, month, day, year, bug, release) ya da
    onunde sayi sifati varsa; 'one of', 'no one', 'one can' sayilmaz."""
    from devbrief.script.metadata import _tr_lower

    m_next = re.match(r"\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)", (text or "")[end:])
    nxt = _tr_lower(m_next.group(1)) if m_next else ""
    if nxt == "of":
        return False
    toks = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]+|\d+", (text or "")[:start])
    prev = _tr_lower(toks[-1]) if toks else ""
    if prev == "no":
        return False
    if nxt in _ONE_UNITS:
        return True
    if prev and (prev in _ONE_PRECEDING_NUMBERS or prev.isdigit()):
        return True
    return False


def normalize_number_words(text: str) -> str:
    """Sayi sozcuklerini rakama cevirir ("otuz saniye" -> "30 saniye").

    Turkce "bir" cevrilmez; Ingilizce "one" yalnizca sayi baglaminda
    cevrilir. Yalnizca oge cikarimi ve teshis gostergesi icin; karar
    esikleri ve regex tabanli sayi denetimleri ham metinle calismaya
    devam eder.
    """
    from devbrief.script.metadata import _tr_lower

    src = text or ""

    def _sub(m: re.Match) -> str:
        w = _tr_lower(m.group(1))
        if w == "one" and not _one_in_numeric_context(src, m.start(), m.end()):
            return m.group(1)
        return _NUMBER_WORDS.get(w, m.group(1))

    return _NUMBER_WORD_RX.sub(_sub, src)


def _leading_proper(text: str) -> bool:
    """Cumle-basi ozel ad gorunumu: ic buyuk harf tasiyan (MiniDB, GitHub)
    buyuk harfli ilk jeton. Cumle basi buyuklugu tek basina sayilmaz
    ("Kurulum ..." ozel ad degildir)."""
    from devbrief.script.metadata import _base_of_token
    from devbrief.script.schemas import _MID_CAP_ALLOW, _PROPER_NOUN

    toks = (text or "").split()
    if not toks:
        return False
    word = toks[0].strip(".,;:!?()\"'")
    if not word or not _PROPER_NOUN.fullmatch(word) or word in _MID_CAP_ALLOW:
        return False
    return any(ch.isupper() for ch in _base_of_token(word)[1:])


def _is_fact_sentence(text: str) -> bool:
    """Kullanilabilir olgu cumlesi: sayi/surum/tarih/ozel ad/kisaltma icerir.

    Somut ogesi olmayan pazarlama cumleleri olgu tasimaz (genisletme
    kaynagi olamaz). Cumle-basi buyuklugu ozel ad sayilmaz.
    """
    t = text or ""
    if _VERSION.search(t) or _NUMBER.search(t) or _DATE.search(t):
        return True
    if _claim_proper_roots(t, skip_first=True):
        return True
    if _leading_proper(t):
        return True
    return bool(_abbreviations(t))


def fact_sentence_indices(source_text: str) -> list[int]:
    """Kaynaktaki kullanilabilir olgu cumlelerinin indeksleri."""
    return [i for i, s in enumerate(_split_sentences(source_text))
            if _is_fact_sentence(s)]


def parse_numbered_source(numbered_text: str) -> list[tuple[int, str]]:
    """Numarali kaynak metni coz: [(indeks, cumle)]. Baslik satirlari atlanir."""
    out = []
    for line in (numbered_text or "").splitlines():
        m = re.match(r"^\[(\d+)\]\s?(.*)$", line.strip())
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
    return out


def unused_fact_sentences(source_text: str, used: set[int],
                          limit: int = 6) -> list[tuple[int, str]]:
    """Kullanilmayan olgu cumleleri: numarali, sirayla, en fazla `limit`.

    `source_text` numarali ("[i] ...") veya ham olabilir; `used` o ana
    kadar senaryoda dayanak yapilmis kaynak indeksleridir.
    """
    numbered = parse_numbered_source(source_text)
    if numbered:
        pairs = numbered
    else:
        pairs = list(enumerate(_split_sentences(source_text)))
    return [(i, t) for i, t in pairs
            if i not in used and _is_fact_sentence(t)][:max(0, limit)]


def short_repair_hint(numbered_source: str, payload: object) -> str | None:
    """Kisalik onarim ipucu: kullanilmayan olgu cumleleri (numarali, ilk 6).

    Donus None ise genisletme kaynagi kalmamistir (onarim turu harcanmaz,
    bolum low_source olur). Yazar yalnizca bu cumlelere dayanarak ekler.
    """
    used: set[int] = set()
    if isinstance(payload, dict):
        for sc in payload.get("scenes") or []:
            if not isinstance(sc, dict):
                continue
            if isinstance(sc.get("sentences"), list) and sc.get("sentences"):
                for s in sc["sentences"]:
                    if isinstance(s, dict):
                        for x in s.get("src") or []:
                            if isinstance(x, int) and not isinstance(x, bool):
                                used.add(x)
            else:
                for x in sc.get("src") or []:
                    if isinstance(x, int) and not isinstance(x, bool):
                        used.add(x)
    unused = unused_fact_sentences(numbered_source, used)
    if not unused:
        return None
    lines = "\n".join(f"[{i}] {t[:200]}" for i, t in unused)
    return ("KISA METIN ONARIMI: konusma metni sert alt sinirin altinda. "
            "Su kaynak cumlelerinden 1-2 TEK-OLGULU cumle ekle, baska bilgi "
            "UYDURMA (yalnizca bu cumlelere dayanarak ekle):\n" + lines)


_TECH_DEFAULTS = [
    "api", "cache", "token", "server", "client", "database", "model",
    "plugin", "build", "release", "deploy", "pipeline", "index", "query",
    "router", "framework", "library", "kernel", "container", "docker",
    "kubernetes", "node", "react", "python", "rust", "github", "git",
    "linux", "prompt", "embedding", "inference", "checkpoint", "webhook",
    "sdk", "cli", "endpoint", "backend", "frontend", "migration",
    "scheduler", "registry", "artifact", "workflow", "oauth", "tls",
    "dns", "http", "json", "yaml", "markdown", "regex", "async",
    "thread", "socket", "proxy", "gateway", "cluster", "replica",
    "snapshot", "backup", "patch", "stable", "beta", "cron", "queue",
    "stream", "batch", "orm", "graphql", "grpc", "websocket", "jwt",
    "benchmark", "linter", "compiler", "runtime", "binary", "package",
    "module", "namespace", "branch", "commit", "merge", "rebase",
    "fork", "tag",     "hook", "coverage", "refactor", "debug", "breakpoint", "sandbox",
    "vite", "typescript", "javascript", "nodejs", "postgres", "redis",
    "kafka",
]


@_lru_cache(maxsize=4)
def load_tech_term_roots(path: str = "config/tech_terms.yaml") -> frozenset:
    """Bilinen teknik terim kokleri (`_match_root` formu).

    Dosya yoksa/bossa gomulu liste kullanilir.
    """
    terms: list[str] = []
    try:
        import yaml

        data = yaml.safe_load(open(path, encoding="utf-8")) or {}
        terms = [str(v) for v in data.get("terms") or [] if str(v).strip()]
    except (OSError, ValueError):
        terms = []
    if not terms:
        terms = list(_TECH_DEFAULTS)
    return frozenset(_match_root(t) for t in terms if len(t) >= 2)


def _root_capitalized(root: str, text: str) -> bool:
    """Kok, metinde buyuk harfli jetonla geciyor mu (API, GitHub, Cache)?"""
    from devbrief.script.metadata import _WORD_RX

    for tok in _WORD_RX.findall(text or ""):
        if len(tok) >= 2 and _match_root(tok) == root and tok[0].isupper():
            return True
    return False


def _tech_items(sent: str, ref_text: str, source_roots: set[str]) -> list[str]:
    """Teknik terim ogeleri: kok uzunlugu>=3, bilinen sozlukte, kaynakta
    da gecen; cumlede ya da dayanak metinde buyuk harfli gorunum sart.

    Kisa kok artigi ("say" vb.) ve kucuk harfli siradan sozcukler elenir.
    """
    terms = load_tech_term_roots()
    out = []
    for r in _content_roots(sent or ""):
        if len(r) < 3 or r not in terms or r not in source_roots:
            continue
        if _root_capitalized(r, sent) or _root_capitalized(r, ref_text):
            out.append(r)
    return sorted(out)


def evidence_verbatim(evidence: str, source_text: str) -> tuple[bool, str]:
    """Siki kanit eslesmesi: normalize BIREBIR veya bir kaynak cumlesine
    difflib >= 0.95. Kisa evidence (<4 token) yargisiz gecer. Donus (ok, bilgi)."""
    if len(_tokens(evidence)) < EVIDENCE_MIN_TOKENS:
        return True, "kisa-yargisiz"
    ev = _norm(evidence)
    sents = _split_sentences(source_text)
    if any(ev == _norm(s) for s in sents):
        return True, "birebir"
    _, ratio = _best_evidence_sentence(evidence, source_text)
    if ratio >= EVIDENCE_VERBATIM_DIFFLIB:
        return True, f"difflib={ratio:.2f}"
    return False, f"en-yakin={ratio:.2f}"


def evidence_claim_coverage(evidence: str, claim_text: str,
                             source_text: str,
                             same_lang: bool = True) -> tuple[bool, str]:
    """Baglama sikiligi: (i) evidence icerik koklerinin >=%70'i en-yakin
    kaynak cumlesinde; (ii) claim'deki sayilarin TAMAMI evidence'da,
    ozel adlar ise yalnizca ayni-dil belgede aranir (capraz-dilde
    "Almanca"/"German" gibi ceviriler kok-eslesmez; sayi her dilde gecer).
    Kisa evidence yargisiz gecer. Donus (ok, bilgi)."""
    if len(_tokens(evidence)) < EVIDENCE_MIN_TOKENS:
        return True, "kisa-yargisiz"
    sents = _split_sentences(source_text)
    idx, _ = _best_evidence_sentence(evidence, source_text)
    ref_roots = _content_roots(sents[idx]) if idx is not None and sents else set()
    ev_roots = _content_roots(evidence)
    if ev_roots:
        hit = sum(1 for r in ev_roots if r in ref_roots)
        if hit / len(ev_roots) < EVIDENCE_COVERAGE_MIN:
            return False, f"kapsama={hit}/{len(ev_roots)}"
    ev_digits = "".join(_DIGITS.findall(evidence))
    for d in _claim_numbers(claim_text):
        if d not in ev_digits:
            return False, f"sayi-yok:{d}"
    if same_lang:
        ev_all = _word_roots_all(evidence)
        for p in sorted(_claim_proper_roots(claim_text)):
            if p not in ev_all:
                return False, f"ozel-ad-yok:{p}"
    return True, "tamam"


def claim_evidence_gap(claim_text: str, evidence: str,
                       source_text: str) -> tuple[int, list[str]]:
    """Claim icerik koklerinden evidence+-1 penceresinde karsiligi
    olmayanlar (kok + sayi digits; ozel ad tum konumlarda). Kisa evidence
    yargisiz (0, []). Donus (sayi, [karsiliksiz kokler])."""
    if len(_tokens(evidence)) < EVIDENCE_MIN_TOKENS:
        return 0, []
    sents = _split_sentences(source_text)
    idx, _ = _best_evidence_sentence(evidence, source_text)
    if idx is None or not sents:
        window = [evidence]
    else:
        window = [evidence] + [sents[i] for i in (idx - 1, idx + 1)
                               if 0 <= i < len(sents)]
    wtext = "\n".join(window)
    wroots = _word_roots_all(wtext)
    wdigits = "".join(_DIGITS.findall(wtext))
    missing = [r for r in sorted(_content_roots(claim_text)) if r not in wroots]
    missing += [f"#{d}" for d in _claim_numbers(claim_text) if d not in wdigits]
    missing += [p for p in sorted(_claim_proper_roots(claim_text))
                if p not in wroots]
    return len(missing), missing


def derive_voice_claims(script: dict) -> list[dict]:
    """Voice cumlelerinden turetilmis claim'ler (sabit sira).

    Sira: hook, her sahne (on_screen, voice), impact, action. Yazarin
    cumle dizisine guvenilir (kod bolucuye degil); legacy (voice+src)
    sahnelerde voice bolunup src paralel eslenir. Her kayit:
    {field, text, src (ilk indeks), srcs (tam liste)}.
    Yazar claims listesine bagli degildir (omission acigini kapatir);
    yazar claim'leri ek/yardimci kalir.
    """
    from devbrief.script.schemas import scene_units as _units

    out: list[dict] = []
    if str(script.get("hook") or "").strip():
        out.append({"field": "hook", "text": str(script["hook"]).strip(),
                    "src": None, "srcs": []})
    for i, sc in enumerate(script.get("scenes") or []):
        if not isinstance(sc, dict):
            continue
        if str(sc.get("on_screen") or "").strip():
            out.append({"field": f"scenes[{i}].on_screen",
                        "text": str(sc["on_screen"]).strip(),
                        "src": None, "srcs": []})
        for sent, srcs in _units(sc):
            refs = [s for s in srcs if isinstance(s, int)
                    and not isinstance(s, bool)]
            out.append({"field": f"scenes[{i}].voice", "text": sent,
                        "src": refs[0] if refs else None, "srcs": refs})
    for key in ("impact", "action"):
        for sent in _split_sentences(script.get(key) or ""):
            out.append({"field": key, "text": sent, "src": None})
    return out


def _best_coverage_sentence(text: str, source_text: str) -> tuple[str | None, float]:
    """En yuksek kapsamali kaynak cumlesi (kanit atama). Donus (cumle, kapsama)."""
    from devbrief.script.metadata import _ref_roots, sentence_coverage

    best: tuple[str | None, float] = (None, 0.0)
    for s in _split_sentences(source_text):
        cov = sentence_coverage(text, _ref_roots(s), s)
        if cov > best[1]:
            best = (s, cov)
    return best


def cross_item_coverage(text: str, window: str,
                        source_roots: set[str]) -> tuple[float | None, str]:
    """Dil-bagimsiz oge kapsama (capraz-dil belgede (b) yerine gecer).

    Payda: sayilar + ozel adlar + teknik terimler (sozluk + buyuk harf
    sartli, madde 2; dayanak metin pencere icerisidir). Esik ayni (%70).
    Hic oge yoksa (None, 'ogesiz') -> deterministik hukumsuz (LLM karar verir).
    """
    nums = _claim_numbers(text)
    proper = sorted(_claim_proper_roots(text))
    tech = _tech_items(text, window, source_roots)
    total = len(nums) + len(proper) + len(tech)
    if total == 0:
        return None, "ogesiz"
    wdigits = "".join(_DIGITS.findall(window))
    wall = _word_roots_all(window)
    hit = sum(1 for d in nums if d in wdigits)
    hit += sum(1 for p in proper if p in wall)
    hit += sum(1 for t in tech if t in wall)
    return hit / total, f"{hit}/{total}"


def _impact_action_facts(text: str, source_text: str,
                         same_lang: bool) -> list[str]:
    """Impact/action olgu denetimi (tum kaynakta, kanitsiz alanlar icin).

    Sayi/kisaltma her dilde aranir (kaynakta gecen serbest); ozel ad
    yalnizca ayni-dilde. Donus eksik listesi (bos = temiz).
    """
    missing = [f"sayi:{x}" for x in _claim_numbers(text)
               if x not in "".join(_DIGITS.findall(source_text or ""))]
    src_norm = _norm(source_text)
    abbrs = _abbreviations(text)
    missing += [f"kis:{a}" for a in abbrs if a.lower() not in src_norm]
    if same_lang:
        roots = _word_roots_all(source_text)
        text_wo = text
        for a in abbrs:
            text_wo = text_wo.replace(a, " ")
        missing += [f"ad:{p}" for p in sorted(_claim_proper_roots(text_wo))
                    if p not in roots]
    return missing


def check_voice_grounding(script: dict, source_text: str
                          ) -> tuple[list[Issue], list[dict]]:
    """Voice-seviyesi deterministik denetim (yazar claims'inden bagimsiz).

    Her turetilmis cumle icin kaynakta en yuksek kapsamali cumle kanit
    atanir. Kurallar: (a) sayi/surum/tarih (digits) + ozel adlar kanit
    cumlesinde veya +-1 komsuda gecmeli; (b) icerik kok kapsama >=%70
    (yalnizca icerikli (>=MIN_VOICE_ROOTS kok) + ayni-dil belgede; capraz
    dilde kok-ortusme ayirt edici degildir); (d) yazar inference koklerinin
    >=yarisini tasiyan, sayisiz en fazla 2 cumle kanitsiz gecebilir (yorum).
    Donus: (issues[kind voice_numbers|voice_coverage], derived listesi).
    Derived kayit: {field, text, evidence|None, cov, status, llm}.
    status: ok | etiket | yorum | desteksiz. llm=True olanlar (c) icin
    tek istekte toplanir.
    """
    derived: list[dict] = []
    issues: list[Issue] = []
    same_lang = _doc_same_language(script, source_text)
    src_roots = _content_roots(source_text)
    inf_claims = [str(c.get("text") or "") for c in script.get("claims") or []
                  if isinstance(c, dict) and str(c.get("kind") or "fact") == "inference"]
    inf_roots: set[str] = set()
    for t in inf_claims:
        inf_roots |= _content_roots(t)
    yorum_used = 0
    for d in derive_voice_claims(script):
        text = d["text"]
        roots = _content_roots(text)
        ev, cov = _best_coverage_sentence(text, source_text)
        rec: dict = {"field": d["field"], "text": text, "evidence": ev,
                     "cov": round(cov, 3), "status": "ok", "llm": True,
                     "note": "", "issue": None, "window": None,
                     "metric": "muaf", "src": d.get("src"),
                     "srcs": list(d.get("srcs") or []),
                     "cov_info": "-"}
        # Impact/action olgu iddiasi degildir: (a) olgu denetimi gecer, kapsama
        # aranmaz, LLM'e gitmez, onayda yorum gorunur.
        if d["field"] in ("impact", "action"):
            missing_ia = _impact_action_facts(text, source_text, same_lang)
            if missing_ia:
                rec.update(status="desteksiz", llm=True,
                           note=f"kanitta-yok:{','.join(missing_ia[:4])}",
                           issue="voice_numbers")
                issues.append(Issue(field=d["field"],
                                    value=f"{text[:100]} | {rec['note']}",
                                    kind="voice_numbers"))
            else:
                rec.update(status="yorum", llm=False, note="alan-yorum")
            derived.append(rec)
            continue
        # (a) sayi/ozel-ad: kanit +-1 penceresinde (dilden bagimsiz).
        sents = _split_sentences(source_text)
        if ev is not None and ev in sents:
            i = sents.index(ev)
            window = "\n".join([ev] + [sents[k] for k in (i - 1, i + 1)
                                       if 0 <= k < len(sents)])
        elif ev is not None:
            window = ev
        else:
            window = source_text or ""
        rec["window"] = window
        wdigits = "".join(_DIGITS.findall(window))
        wall = _word_roots_all(window)
        missing_facts = [f"sayi:{x}" for x in _claim_numbers(text) if x not in wdigits]
        # Ozel adlar yalnizca ayni-dil belgede aranir: capraz-dilde ceviriler
        # ("Almanca"/"German") kok-eslesmez; sayi her dilde gecerlidir.
        if same_lang:
            missing_facts += [f"ad:{p}" for p in
                              sorted(_claim_proper_roots(text))
                              if p not in wall]
        if missing_facts:
            rec.update(status="desteksiz", llm=True,
                       note=f"kanitta-yok:{','.join(missing_facts[:4])}",
                       issue="voice_numbers")
            issues.append(Issue(field=d["field"],
                                value=f"{text[:100]} | {rec['note']}",
                                kind="voice_numbers"))
            derived.append(rec)
            continue
        # Etiket: kisa cumleler (b)/(c)'den muaf.
        if len(roots) < MIN_VOICE_ROOTS:
            rec.update(status="etiket", llm=False, note="etiket-kisa")
            derived.append(rec)
            continue
        # (d) yorum istisnasi: yazar inference'iyla ortusen, sayisiz, en fazla 2.
        if (inf_roots and not _claim_numbers(text)
                and not _claim_proper_roots(text)
                and yorum_used < MAX_INFERENCE_SENTENCES
                and len(roots & inf_roots) / len(roots) >= INFERENCE_MATCH_MIN):
            yorum_used += 1
            rec.update(status="yorum", llm=False, note="yazar-inference")
            derived.append(rec)
            continue
        # (b) kapsama: ayni-dilde kok kapsama, capraz-dilde oge kapsama
        # (sayi/ozel-ad/teknik-terim). Esik ayni (%70); metrik kayda islenir.
        cross = None
        if same_lang:
            rec["metric"] = "kok-kapsama"
            rec["cov_info"] = f"{cov:.0%}"
            if cov < EVIDENCE_COVERAGE_MIN:
                rec.update(status="desteksiz", llm=True,
                           note=f"kapsama={cov:.0%}<%70",
                           issue="voice_coverage")
                issues.append(Issue(field=d["field"],
                                    value=f"{text[:100]} | {rec['note']}",
                                    kind="voice_coverage"))
                derived.append(rec)
                continue
        else:
            rec["metric"] = "capraz-ogeler"
            cross, info = cross_item_coverage(text, window, src_roots)
            rec["cov_info"] = info
            if cross is not None and cross < EVIDENCE_COVERAGE_MIN:
                rec.update(status="desteksiz", llm=True,
                           note=f"capraz-kapsama={info}<%70",
                           issue="voice_coverage")
                issues.append(Issue(field=d["field"],
                                    value=f"{text[:100]} | {rec['note']}",
                                    kind="voice_coverage"))
                derived.append(rec)
                continue
            if cross is None:
                rec["note"] = "capraz-dil:ogesiz"
        derived.append(rec)
    return issues, derived


def build_llm_claims(script: dict, source_text: str
                     ) -> tuple[list[dict], list[int], list[dict]]:
    """LLM dogrulama TEK listesi: yazar fact'leri + (c)'ye giren turetilmisler.

    Turetilmislerin kaniti pencere metnidir (kanit cumlesi +-1 komsu):
    verify istemi zaten "evidence +-1 baglam" soyler, kod da onu gonderir.
    Donus: (combined[{text,evidence,kind}], writer_idx[], derived[]).
    writer_idx: combined'deki yazar konumu -> orijinal claim indeksi;
    derived: llm=True turetilmis kayitlar (combined sirasiyla).
    """
    w_idx = [j for j, c in enumerate(script.get("claims") or [])
             if isinstance(c, dict) and str(c.get("kind") or "fact") != "inference"]
    combined = [{"text": str(script["claims"][j].get("text") or ""),
                 "evidence": str(script["claims"][j].get("evidence") or ""),
                 "kind": "fact"} for j in w_idx]
    _issues, derived = check_voice_grounding(script, source_text)
    dll = [d for d in derived if d.get("llm")]
    for d in dll:
        combined.append({"text": d["text"],
                         "evidence": d.get("window") or d["evidence"] or "",
                         "kind": "fact"})
    return combined, w_idx, dll

_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?")
_PERCENT = re.compile(r"%\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*%")
_DATE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b(?:19|20)\d{2}\b")
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_TOKEN = re.compile(r"[A-Za-z0-9_@.+-]+")
_DIGITS = re.compile(r"\d+")
_MAGNITUDE = (("m", 1_000_000.0, ("m", "milyon", "million")),
              ("k", 1_000.0, ("k", "bin", "thousand")))
_CURRENCY = ("$", "€", "£", "dollar", "dollars", "dolar", "tl", "eur", "usd")
_PERCENT_WORDS = ("%", "yuzde", "yüzde", "percent")


def _strip_accents(s: str) -> str:
    return (s.replace("ü", "u").replace("ğ", "g").replace("ş", "s")
             .replace("ö", "o").replace("ç", "c").replace("ı", "i"))


def parse_magnitude(token: str) -> tuple[float | None, str]:
    """'2.4M'/'2,4 milyon' -> (2400000.0, digits); olmadiysa (None, digits)."""
    low = _strip_accents(token.lower()).strip()
    mult, rest = 1.0, low
    for _sym, m, words in _MAGNITUDE:
        for w in words:
            if rest.endswith(w) and len(rest) > len(w):
                mult, rest = m, rest[: -len(w)].strip()
                break
    rest = rest.replace(",", ".")
    try:
        return float(rest) * mult, "".join(_DIGITS.findall(token))
    except ValueError:
        return None, "".join(_DIGITS.findall(token))


def _plain_digits(token: str) -> str:
    low = _strip_accents(token.lower())
    for w in _CURRENCY + _PERCENT_WORDS:
        low = low.replace(w, " ")
    num = low.strip().lstrip("v")
    num = num.replace(",", ".")
    return "".join(_DIGITS.findall(num))


def match_value(value: str, source: str) -> str:
    """Sayi/surum karsilastirma (eval + kural icin TEK kod yolu).

    Donus: "match" | "ambiguous" | "miss".
    - birebir / v-siz surum / % sirasi / K-M-para birimi / ayrac birlesimi -> match
    - rakam cekirdegi daha uzun bir sayinin parcasiysa -> ambiguous
    - aksi -> miss
    Sayisal adaylarda ("40" vs "140") kelime siniri aranir; duz alt-dize yetmez.
    """
    src = _norm(source)
    norm_v = _norm(value)
    if re.fullmatch(r"[\d][\d.,%\-/ ]*", norm_v):
        if re.search(r"(?<!\d)" + re.escape(norm_v) + r"(?!\d)", src):
            return "match"
    elif norm_v in src:
        return "match"
    cand_digits = "".join(_DIGITS.findall(value))
    if not cand_digits:
        return "miss"
    src_numbers = _NUMBER.findall(source) + _VERSION.findall(source)
    # v-siz surum: "v8.3.4" ~ "8.3.4".
    stripped = value.lstrip("vV")
    if stripped != value and _norm(stripped) in src:
        return "match"
    # Sayisal esitlik: ayrac + birim + para birimi normalize.
    cand_num, _ = parse_magnitude(_plain_digits(value) or value)
    for tok in dict.fromkeys(src_numbers):
        if _plain_digits(tok) == _plain_digits(value):
            return "match"
        if cand_num is not None:
            src_num, _ = parse_magnitude(tok)
            if src_num is not None and abs(src_num - cand_num) < 1e-9 * max(1.0, abs(src_num)):
                return "match"
    # Belirsiz: rakamlar daha uzun bir sayinin icinde geciyor.
    for tok in dict.fromkeys(src_numbers):
        d = "".join(_DIGITS.findall(tok))
        if d and d != cand_digits and (cand_digits in d or d in cand_digits):
            return "ambiguous"
    return "miss"


def nearest_source_number(value: str, source_text: str, window: int = 40) -> str:
    """Kaynakta en yakin sayi + baglami (denetim raporu icin)."""
    cand = "".join(_DIGITS.findall(value)) or value
    best, best_len = "", -1
    for m in _NUMBER.finditer(source_text):
        tok = m.group(0)
        d = "".join(_DIGITS.findall(tok))
        common = _overlap(d, cand)
        if common > best_len:
            best, best_len = tok, common
    if not best:
        return "(sayisal eslesme yok)"
    i = source_text.find(best)
    lo, hi = max(0, i - window), min(len(source_text), i + len(best) + window)
    return f"{best} -- ..." + " ".join(source_text[lo:hi].split()) + "..."


def _overlap(a: str, b: str) -> int:
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


@dataclass
class Issue:
    field: str  # örn. "scenes[1].voice" / "code" / "claim"
    value: str  # kaynakta bulunamayan deger
    kind: str  # version | percent | date | number | code | ambiguous


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zçğıöşü0-9]+", _norm(text))


def evidence_match(evidence: str, source_text: str,
                   tok_thr: float = 0.85, dif_thr: float = 0.9) -> bool:
    """Deterministik kanit kontrolu: fact evidence'i kaynakta gecmeli.

    Token kapsama >= tok_thr VEYA difflib >= dif_thr. Kisa evidence (<4 token)
    degerlendirilemez -> True (baska kurallar yakalar).
    """
    from difflib import SequenceMatcher

    ev_toks = _tokens(evidence)
    if len(ev_toks) < 4:
        return True
    src = _norm(source_text)
    ev = _norm(evidence)
    if ev in src:
        return True
    src_set = set(_tokens(source_text))
    cover = sum(1 for t in ev_toks if t in src_set) / len(ev_toks)
    if cover >= tok_thr:
        return True
    return SequenceMatcher(None, ev, src).ratio() >= dif_thr


def _sentence_items(sent: str, ref_text: str, source_roots: set[str]) -> dict:
    """Cumleden cikarilan ogeler: sayi/surum jetonlari (sayi sozcukleri
    rakama normalle edilir: "otuz" -> "30"; "bir" cevrilmez), ozel ad
    kokleri, teknik terim kokleri (sozluk + buyuk harf sartli, madde 2)."""
    norm = normalize_number_words(sent or "")
    return {"numbers": list(dict.fromkeys(_VERSION.findall(norm)
                                          + _NUMBER.findall(norm))),
            "proper": sorted(_claim_proper_roots(sent or "")),
            "tech": _tech_items(sent or "", ref_text or "", source_roots)}


#: Turkce zorunluluk sozcukleri (acik liste; kip kaymasi denetimi icin).
_TR_OBLIGATION_WORDS = frozenset({
    "gerekir", "gerekli", "olmalı", "olmali", "zorunlu", "zorunluluk",
    "zorunludur", "şart", "sart", "şarttır", "sarttir", "mecburi", "mecbur",
})
#: -mAlI zorunluluk eki + kisi ekleri (olumlu/olumsuz kip degil, gereklilik).
_TR_OBL_SUFFIX_RX = re.compile(
    r"[a-zçğıöşü]+m[ae]l[ıiuü](?:y[ıiuü]m|s[ıiuü]n|y[ıi]z|s[ıiuü]n[ıiuü]z|l[ae]r)?\b")
#: Ek-yalanci kokler (-mAlI gorunumlu ama kip degil).
_TR_OBL_STOP = ("normal", "minimal", "optimal", "formal", "ihtimal")
#: Kaynakta zorunluluk sayilan EN ifadeler (kip korunumu icin).
#: explain-reject bu listeyi aynen yazdirir.
_EN_OBLIGATION_LIST = ["must", "required", "require", "requires",
                        "need", "needs", "should", "mandatory"]
_EN_OBLIGATION_RX = re.compile(
    r"\b(" + "|".join(_EN_OBLIGATION_LIST) + r")\b", re.IGNORECASE)


def mood_markers(sent: str) -> list[str]:
    """Cumledeki Turkce zorunluluk isaretleri (gerekir/olmalı/-meli...)."""
    from devbrief.script.metadata import _tr_lower

    out = []
    for tok in re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]+", sent or ""):
        low = _tr_lower(tok)
        if low in _TR_OBLIGATION_WORDS:
            out.append(tok)
            continue
        if low.startswith(_TR_OBL_STOP):
            continue
        if _TR_OBL_SUFFIX_RX.fullmatch(low):
            out.append(tok)
    return out


def mood_search_window(sents: list[str], valid: list[int]
                       ) -> list[tuple[int, str]]:
    """Src cumlesi +-1 pencere: [(indeks, cumle)]. Dayanak baglami genisletir
    (LLM dogrulayicidaki evidence+-1 kuraliyle ayni); esik degismez."""
    if not sents or not valid:
        return []
    idx = sorted({i for s in valid if isinstance(s, int)
                  and not isinstance(s, bool)
                  for i in (s - 1, s, s + 1)} & set(range(len(sents))))
    return [(i, sents[i]) for i in idx]


def mood_window_hits(sents: list[str], valid: list[int]) -> list[dict]:
    """Pencerede bulunan zorunluluk ifadeleri: [{expr, from}] (from: '#i')."""
    hits: list[dict] = []
    for i, t in mood_search_window(sents, valid):
        for m in _EN_OBLIGATION_RX.finditer(t or ""):
            h = {"expr": m.group(0).lower(), "from": f"#{i}"}
            if h not in hits:
                hits.append(h)
    return hits


def check_mood_shift(sent: str, union: str,
                     sents: list[str] | None = None,
                     valid: list[int] | None = None) -> list[str]:
    """Kip kaymasi: cumlede zorunluluk var, dayanak src (+-1 komsu) yoksa
    isaretler. Bos donus = temiz (ya kip yok ya pencerede karsiligi var)."""
    markers = mood_markers(sent)
    if not markers:
        return []
    if sents is not None and valid:
        return [] if mood_window_hits(sents, valid) else markers
    if _EN_OBLIGATION_RX.search(union or ""):
        return []
    return markers


_FRAMING_DEFAULTS = [
    {"id": "onemli-degisiklik",
     "tr": ["önemli değişiklik", "önemli degisiklik"],
     "en": ["major change", "significant change"]},
    {"id": "buyuk-adim",
     "tr": ["büyük adım", "buyuk adim"],
     "en": ["major step", "big step"]},
    {"id": "yeni-donem",
     "tr": ["yeni dönem", "yeni donem"],
     "en": ["new era", "new chapter"]},
    {"id": "devrim",
     "tr": ["devrim"],
     "en": ["revolution", "revolutionary"]},
]


@_lru_cache(maxsize=4)
def load_framing_terms(path: str = "config/framing_terms.yaml") -> list[dict]:
    """Cerceveleme gruplari: [{id, variants:[...]}]. Dosya yoksa gomulu liste."""
    try:
        import yaml

        data = yaml.safe_load(open(path, encoding="utf-8")) or {}
        groups = []
        for g in data.get("groups") or []:
            variants = [str(v) for v in (g.get("tr") or []) + (g.get("en") or [])
                        if str(v).strip()]
            if variants:
                groups.append({"id": str(g.get("id") or "?"), "variants": variants})
        if groups:
            return groups
    except (OSError, ValueError):
        pass
    return [{"id": g["id"], "variants": list(g["tr"]) + list(g["en"])}
            for g in _FRAMING_DEFAULTS]


def _fold(s: str) -> str:
    """Karsilastirma normal formu: Turkce-kucuk + aksan siyirma.

    Aksansiz yazim ("onemli") ile dogru yazimi ("önemli") eslestirir;
    yalnizca cerceveleme eslesmesinde kullanilir (karar esikleri ayni).
    """
    from devbrief.script.metadata import _tr_lower

    return _strip_accents(_tr_lower(s or ""))


def _phrase_norm(s: str) -> str:
    """Cerceve kalibi normal formu: katlama + "bir" ara sozcugunu at.

    "önemli bir değişiklik" ile "önemli değişiklik" ayni kalip sayilir.
    """
    return re.sub(r"\s+", " ", re.sub(r"\bbir\b", " ", _fold(s))).strip()


def framing_hits(sent: str, groups: list[dict] | None = None) -> list[tuple[str, str]]:
    """Cumledeki cerceveleme terimleri: [(terim, grup)].

    Olumsuzlama ("... degil/yok") cerceveleme sayilmaz.
    """
    low = _phrase_norm(sent)
    out = []
    for g in groups if groups is not None else load_framing_terms():
        for v in g["variants"]:
            nv = _phrase_norm(v)
            if nv and nv in low:
                tail = low.split(nv, 1)[1][:20]
                if re.search(r"\bdegil\b|\byok\b", tail):
                    break
                out.append((v, g["id"]))
                break
    return out


def check_framing(sent: str, union: str,
                 groups: list[dict] | None = None) -> list[tuple[str, str]]:
    """Cerceveleme: terim cumlede var, dayanak src'de hicbir varyanti yoksa ret.

    Donus [(terim, grup)] (bos = temiz). Grup ici capraz-dil karsilik
    (or. "devrim" <-> "revolution") dayanak sayilir.
    """
    union_low = _phrase_norm(union)
    groups = groups if groups is not None else load_framing_terms()
    bad = []
    for term, gid in framing_hits(sent, groups):
        variants = next((g["variants"] for g in groups if g["id"] == gid), [term])
        if not any(v and _phrase_norm(v) in union_low for v in variants):
            bad.append((term, gid))
    return bad


def framing_row(sent: str, union: str,
               groups: list[dict] | None = None) -> list[dict]:
    """Teshis kaydi: cumledeki her terim + dayanak durumu."""
    union_low = _phrase_norm(union)
    groups = groups if groups is not None else load_framing_terms()
    out = []
    for term, gid in framing_hits(sent, groups):
        variants = next((g["variants"] for g in groups if g["id"] == gid), [term])
        out.append({"term": term, "group": gid,
                    "allowed": any(v and _phrase_norm(v) in union_low
                                   for v in variants)})
    return out


def _check_sentence_units(script: dict, source_text: str
                           ) -> tuple[list[Issue], list[dict]]:
    """Cumle-bazli sahne-kaynak denetimi motoru: (issues, satirlar).

    Yeni sema (`sentences` dizisi): her cumle KENDI src cumlelerine karsi
    denetlenir (sayi/surum/ozel-ad +-0, kapsama %70); src bos olamaz,
    en fazla 2 indeks. Legacy (voice+paralel src): eski davranis aynen
    korunur (eski mesaj bicimleri dahil); src'siz eski cikti denetlenmez.
    Esikler degismez (EVIDENCE_COVERAGE_MIN).

    Satir (cumle bazinda kayit): {field, scene_idx, sent_idx, scene, sent,
    text, srcs, src_texts, metric, coverage, coverage_info, items, missing,
    decision}. `decision`: "ok" ya da "red:<tur-ipi>".
    """
    from devbrief.script.schemas import scene_units as _units

    sents = _split_sentences(source_text)
    same_lang = _doc_same_language(script, source_text)
    src_roots = _content_roots(source_text)
    issues: list[Issue] = []
    rows: list[dict] = []
    n = 0  # global voice cumle numarasi (legacy mesajlar 1'den baslar)

    def _refs_of(valid: list[int]) -> str:
        union = "\n".join(sents[s] for s in valid)
        return union

    for i, sc in enumerate(script.get("scenes") or []):
        if not isinstance(sc, dict):
            continue
        sid = sc.get("id", i)
        new_shape = (isinstance(sc.get("sentences"), list) and sc.get("sentences")
                     and not sc.get("src_legacy"))
        if isinstance(sc.get("sentences"), list) and sc.get("sentences") \
                and sc.get("src_legacy"):
            continue  # src'siz legacy donusum: denetlenmez (eski davranis)
        units = _units(sc)
        if not new_shape:
            # --- Legacy: paralel voice/src, eski davranis ve mesajlar aynen.
            voices = [t for t, _ in units]
            srcs = sc.get("src") or []
            if not srcs:
                n += len(voices)
                continue  # eski cikti: denetlenmez
            if len(srcs) != len(voices):
                issues.append(Issue(
                    field=f"scenes[{i}].src",
                    value=f"{len(voices)} cumleye {len(srcs)} src verildi",
                    kind="src_arity"))
                n += len(voices)
                continue
            distinct = {s for s in srcs if isinstance(s, int)
                        and not isinstance(s, bool)}
            if len(distinct) > 3:
                issues.append(Issue(
                    field=f"scenes[{i}].src",
                    value=f"sahne {sid}: {len(distinct)} farkli kaynak "
                           "(fuzyon: tek sahne en fazla 3 kaynak cumlesine "
                           "yaslanir)",
                    kind="src_fusion"))
            for k, sent in enumerate(voices):
                n += 1
                s = srcs[k]
                if isinstance(s, bool) or not isinstance(s, int) or not (
                        0 <= s < len(sents)):
                    issues.append(Issue(
                        field=f"scenes[{i}].src[{k}]",
                        value=f"cumle {n}: kaynak #{s} aralik disi "
                               f"(0..{len(sents) - 1})",
                        kind="src_range"))
                    rows.append(_legacy_row(i, sid, k, n, sent, [], [],
                                            "red:src_range"))
                    continue
                mark = len(issues)
                ref = sents[s]
                rows.append(_legacy_row(i, sid, k, n, sent, [s], [ref], None))
                ref_digits = "".join(_DIGITS.findall(ref))
                for tok in dict.fromkeys(_VERSION.findall(sent)
                                         + _NUMBER.findall(sent)):
                    if "".join(_DIGITS.findall(tok)) not in ref_digits:
                        issues.append(Issue(
                            field=f"scenes[{i}].voice",
                            value=f"cumle {n}: {tok!r} kaynak #{s}'te yok",
                            kind="src"))
                _cover_one(issues, rows[-1], sent, ref, sents, same_lang,
                           src_roots, f"cumle {n}: ", f" (kaynak #{s})",
                           f"scenes[{i}].voice")
                if same_lang:
                    ref_roots = _word_roots_all(ref)
                    for p in sorted(_claim_proper_roots(sent)):
                        if p not in ref_roots:
                            issues.append(Issue(
                                field=f"scenes[{i}].voice",
                                value=f"cumle {n}: {p!r} kaynak #{s}'te yok",
                                kind="src"))
                _mood_framing_issues(issues, sent, ref, f"scenes[{i}].voice",
                                     f"cumle {n}: ", f" (kaynak #{s})",
                                     sents, [s])
                _stamp_decision(issues, rows[-1], mark)
            continue
        # --- Yeni sema: cumle dizisine guven (kod bolucu kullanilmaz).
        ref_lists = [list(srcs) for _, srcs in units]
        distinct = {s for srcs in ref_lists for s in srcs
                    if isinstance(s, int) and not isinstance(s, bool)}
        if len(distinct) > 3:
            issues.append(Issue(
                field=f"scenes[{i}].sentences",
                value=f"sahne {sid}: {len(distinct)} farkli kaynak "
                       "(fuzyon: tek sahne en fazla 3 kaynak cumlesine "
                       "yaslanir)",
                kind="src_fusion"))
        for j, (sent, raw_srcs) in enumerate(units):
            k = j + 1
            field = f"scenes[{i}].sentences[{j}]"
            if not sent.strip():
                issues.append(Issue(
                    field=field + ".text",
                    value=f"sahne {sid} cumle {k}: bos cumle",
                    kind="empty_sentence"))
                rows.append(_new_row(i, sid, j, k, sent, [], [],
                                     "red:empty_sentence"))
                continue
            if not raw_srcs:
                issues.append(Issue(
                    field=field + ".src",
                    value=f"sahne {sid} cumle {k}: src eksik "
                           "(1-2 kaynak cumle indeksi gerekli)",
                    kind="src_missing"))
                rows.append(_new_row(i, sid, j, k, sent, [], [],
                                     "red:src_missing"))
                continue
            if len(raw_srcs) > 2:
                issues.append(Issue(
                    field=field + ".src",
                    value=f"sahne {sid} cumle {k}: {len(raw_srcs)} src "
                           "verildi (en fazla 2)",
                    kind="src_arity"))
            valid: list[int] = []
            for s in raw_srcs:
                if isinstance(s, bool) or not isinstance(s, int) or not (
                        0 <= s < len(sents)):
                    issues.append(Issue(
                        field=field + ".src",
                        value=f"sahne {sid} cumle {k}: kaynak #{s} "
                               f"aralik disi (0..{len(sents) - 1})",
                        kind="src_range"))
                elif s not in valid:
                    valid.append(s)
            if not valid:
                rows.append(_new_row(i, sid, j, k, sent, [], [],
                                     "red:src_range"))
                continue
            mark = len(issues)
            refs = ",".join(f"#{s}" for s in valid)
            union = _refs_of(valid)
            row = _new_row(i, sid, j, k, sent, valid,
                           [sents[s] for s in valid], None)
            rows.append(row)
            union_digits = "".join(_DIGITS.findall(union))
            for tok in dict.fromkeys(_VERSION.findall(sent)
                                     + _NUMBER.findall(sent)):
                if "".join(_DIGITS.findall(tok)) not in union_digits:
                    issues.append(Issue(
                        field=field,
                        value=f"sahne {sid} cumle {k}: {tok!r} "
                               f"kaynak {refs}'te yok",
                        kind="src"))
            _cover_one(issues, row, sent, union, sents, same_lang, src_roots,
                       f"sahne {sid} cumle {k}: ", f" (kaynak {refs})", field)
            if same_lang:
                union_roots = _word_roots_all(union)
                for p in sorted(_claim_proper_roots(sent)):
                    if p not in union_roots:
                        issues.append(Issue(
                            field=field,
                            value=f"sahne {sid} cumle {k}: {p!r} "
                                   f"kaynak {refs}'te yok",
                            kind="src"))
            _mood_framing_issues(issues, sent, union, field,
                                 f"sahne {sid} cumle {k}: ",
                                 f" (kaynak {refs})", sents, valid)
            _stamp_decision(issues, row, mark)
    return issues, rows


def check_scene_sources(script: dict, source_text: str) -> list[Issue]:
    """Sahne-kaynak gonderimi (cumle bazinda).

    Yeni semada her cumle KENDI src cumlelerine karsi denetlenir
    (sayi/surum/ozel-ad +-0, kapsama %70, esik ayni); kod cumle bolucuye
    degil yazarin dizisine guvenir. Legacy sahnelerde eski davranis aynen
    korunur. Donus Issue listesi (kind: src_arity | src_range | src |
    src_coverage | src_fusion | src_missing | empty_sentence).
    """
    issues, _ = _check_sentence_units(script, source_text)
    return issues


def sentence_coverage_rows(script: dict, source_text: str) -> list[dict]:
    """Cumle-bazli kapsama kayitlari (metrik, kapsama, ogeler, karar).

    Her satir: {field, scene_idx, sent_idx, scene, sent, text, srcs,
    src_texts, metric, coverage, coverage_info, items{numbers,proper,tech},
    missing{numbers,proper,roots}, decision}. Teshis (audit-pack,
    explain-reject) ve failed kaydi bunu kullanir.
    """
    _, rows = _check_sentence_units(script, source_text)
    return rows


def _blank_item_maps() -> dict:
    return {"items": {"numbers": [], "proper": [], "tech": []},
            "matched": {"numbers": [], "proper": [], "tech": [], "roots": []},
            "missing": {"numbers": [], "proper": [], "roots": []},
            "mood": {"markers": [], "src_obligation": False},
            "framing": []}


def _legacy_row(i: int, sid, k: int, n: int, sent: str,
                valid: list[int], texts: list[str],
                decision: str | None) -> dict:
    return {"field": f"scenes[{i}].voice", "scene_idx": i, "sent_idx": k,
            "scene": sid, "sent": n, "text": sent, "srcs": valid,
            "src_texts": texts, "metric": "-", "coverage": None,
            "coverage_info": "-", "decision": decision or "ok",
            **_blank_item_maps()}


def _new_row(i: int, sid, j: int, k: int, sent: str,
             valid: list[int], texts: list[str],
             decision: str | None) -> dict:
    return {"field": f"scenes[{i}].sentences[{j}]", "scene_idx": i,
            "sent_idx": j, "scene": sid, "sent": k, "text": sent,
            "srcs": valid, "src_texts": texts, "metric": "-",
            "coverage": None, "coverage_info": "-",
            "decision": decision or "ok", **_blank_item_maps()}


def _mood_framing_issues(issues: list[Issue], sent: str, union: str,
                          field: str, prefix: str, suffix: str,
                          sents: list[str] | None = None,
                          valid: list[int] | None = None) -> None:
    """Kip + cerceveleme denetimi (kural hatasi, somut onarim mesaji).

    Kip kaymasi: cumlede Turkce zorunluluk (gerekir/olmali/-meli) var,
    dayanak src (+-1 komsu) penceresinde must/required/need/should/
    mandatory yoksa ret. Cerceveleme: degerlendirici terim
    (config/framing_terms.yaml) cumlede var, dayanak src'de hicbir
    varyanti yoksa ret. Esikler aynidir (ikili ret/izin; yuzde esigi yok).
    """
    for mk in check_mood_shift(sent, union, sents, valid):
        issues.append(Issue(
            field=field,
            value=f"{prefix}kip kaymasi ('{mk}'): "
                   f"src'de zorunluluk ifadesi yok{suffix}",
            kind="kip"))
    for ft, _gid in check_framing(sent, union):
        issues.append(Issue(
            field=field,
            value=f"{prefix}cerceveleme ('{ft}'): "
                   f"src cumlede gecmiyor{suffix}",
            kind="cerceveleme"))


def _cover_one(issues: list[Issue], row: dict, sent: str, ref: str,
               sents: list[str], same_lang: bool, src_roots: set[str],
               prefix: str, suffix: str, field: str) -> None:
    """Kapsama katmani (esik ayni %70): metrik/kapsama/oge kaydi satura islenir.

    Ayni-dilde kok-kapsama, capraz-dilde oge kapsama (sayi/ozel-ad/teknik).
    Ogesiz capraz cumle hukumusuzdur (LLM karar verir).
    """
    items = _sentence_items(sent, ref, src_roots)
    row["items"] = {k: list(v) for k, v in items.items()}
    union_roots = _word_roots_all(ref)
    ref_digits = "".join(_DIGITS.findall(ref))
    row["missing"]["numbers"] = [
        t for t in items["numbers"] if "".join(_DIGITS.findall(t)) not in ref_digits]
    row["matched"]["numbers"] = [
        t for t in items["numbers"] if t not in row["missing"]["numbers"]]
    row["matched"]["proper"] = [p for p in items["proper"] if p in union_roots]
    row["matched"]["tech"] = [t for t in items["tech"] if t in union_roots]
    _win_hits = mood_window_hits(sents, row.get("srcs") or [])
    row["mood"] = {"markers": mood_markers(sent),
                   "src_obligation": bool(_win_hits),
                   "searched": list(_EN_OBLIGATION_LIST),
                   "hits": _win_hits}
    row["framing"] = framing_row(sent, ref)
    if same_lang:
        row["metric"] = "kok-kapsama"
        cover = _coverage_of(sent, ref)
        row["coverage"] = round(cover, 3)
        row["coverage_info"] = f"{cover:.0%}"
        ref_content = _content_roots(ref)
        row["missing"]["roots"] = sorted(
            r for r in _content_roots(sent) if r not in ref_content)[:8]
        row["missing"]["proper"] = [
            p for p in items["proper"] if p not in union_roots]
        row["matched"]["roots"] = sorted(
            r for r in _content_roots(sent) if r in ref_content)[:8]
        if cover < EVIDENCE_COVERAGE_MIN:
            issues.append(Issue(
                field=field,
                value=f"{prefix}kapsama {cover:.0%}<%70{suffix}",
                kind="src_coverage"))
    else:
        row["metric"] = "capraz-ogeler"
        cross, info = cross_item_coverage(sent, ref, src_roots)
        row["coverage"] = round(cross, 3) if cross is not None else None
        row["coverage_info"] = info
        if cross is not None and cross < EVIDENCE_COVERAGE_MIN:
            issues.append(Issue(
                field=field,
                value=f"{prefix}capraz-kapsama <%70{suffix}",
                kind="src_coverage"))


def _stamp_decision(issues: list[Issue], row: dict, mark: int) -> None:
    """Cumle reddi: `mark` sonrasi eklenen ilk-ihlal turleri satir karari olur."""
    kinds = [iss.kind for iss in issues[mark:]]
    if kinds and row.get("decision") in (None, "ok"):
        row["decision"] = "red:" + "+".join(dict.fromkeys(kinds))


def _coverage_of(text: str, ref: str) -> float:
    """Icerik-koku kapsama orani (kok kumesinde)."""
    from devbrief.script.metadata import _ref_roots, sentence_coverage

    return sentence_coverage(text, _ref_roots(ref), ref)


def _find_missing(values: list[str], source: str, kind: str, field: str,
                  fuzzy: bool = False) -> list[Issue]:
    src = _norm(source)
    out = []
    for v in dict.fromkeys(values):  # sira korunarak tekillestir
        if not fuzzy:
            if _norm(v) not in src:
                out.append(Issue(field=field, value=v, kind=kind))
            continue
        verdict = match_value(v, source)
        if verdict == "miss":
            out.append(Issue(field=field, value=v, kind=kind))
        elif verdict == "ambiguous":
            out.append(Issue(field=field, value=v, kind="ambiguous"))
    return out


def check_grounding(script: dict, source_text: str,
                    removed: list[str] | tuple = ()) -> list[Issue]:
    """Senaryodaki somut degerleri kaynakla karsilastir. Bos = gecti.

    `removed`: taramada cikarilmis supheli cumleler; bir claim evidence'i
    bunlara isaret ediyorsa iddia unsupported (`evidence_removed`).
    """
    from devbrief.script.schemas import scene_voice as _scene_voice

    issues: list[Issue] = []
    scenes = script.get("scenes") or []
    for i, sc in enumerate(scenes):
        base = f"scenes[{i}]"
        voice = _scene_voice(sc) if isinstance(sc, dict) else ""
        issues += _find_missing(_VERSION.findall(voice), source_text, "version",
                                f"{base}.voice", fuzzy=True)
        for p in dict.fromkeys(_PERCENT.findall(voice)):
            if match_value(p, source_text) == "miss":
                issues.append(Issue(field=f"{base}.voice", value=p, kind="percent"))
            elif match_value(p, source_text) == "ambiguous":
                issues.append(Issue(field=f"{base}.voice", value=p, kind="ambiguous"))
        issues += _find_missing(_DATE.findall(voice), source_text, "date", f"{base}.voice")
        issues += _find_missing(
            [n for n in _NUMBER.findall(voice) if not _VERSION.fullmatch(n or "")],
            source_text, "number", f"{base}.voice", fuzzy=True,
        )
        code = str(sc.get("code") or "")
        if code.strip():
            toks = [t for t in dict.fromkeys(_TOKEN.findall(code)) if len(t) >= 4]
            issues += _find_missing(toks, source_text, "code", f"{base}.code")
    for j, claim in enumerate(script.get("claims") or []):
        text = str(claim.get("text") or "")
        kind = str(claim.get("kind") or "fact")
        if kind == "inference":
            from devbrief.script.schemas import find_inference_violations

            if find_inference_violations(text, source_text):
                issues.append(Issue(field=f"claims[{j}]", value=text[:120],
                                    kind="inference"))
            continue
        issues += _find_missing(_VERSION.findall(text), source_text, "version",
                                f"claims[{j}]", fuzzy=True)
        issues += _find_missing(_NUMBER.findall(text), source_text, "number",
                                f"claims[{j}]", fuzzy=True)
    hook = str(script.get("hook") or "")
    issues += _find_missing(_VERSION.findall(hook), source_text, "version", "hook", fuzzy=True)
    try:
        from devbrief.config import get_settings as _gs

        _s = _gs()
        tok_thr, dif_thr = _s.evidence_token_coverage, _s.evidence_difflib
    except Exception:  # noqa: BLE001 - testsiz ortamda varsayilan
        tok_thr, dif_thr = 0.85, 0.9
    for j, claim in enumerate(script.get("claims") or []):
        if str(claim.get("kind") or "fact") == "inference":
            continue  # yorum: kanit eslesmesi aranmaz
        ev = str(claim.get("evidence") or "")
        if not evidence_match(ev, source_text, tok_thr, dif_thr):
            issues.append(Issue(field=f"claims[{j}].evidence", value=ev[:120],
                                kind="evidence_mismatch"))
    # Siki kanit katmanlari (uydurma-kanit): kisa evidence yargisiz gecer.
    # Kok-ortusme kontrolleri yalnizca ayni-dil belgede anlamlidir.
    same_lang = _doc_same_language(script, source_text)
    for j, claim in enumerate(script.get("claims") or []):
        if str(claim.get("kind") or "fact") == "inference":
            continue
        ev = str(claim.get("evidence") or "")
        text = str(claim.get("text") or "")
        if len(_tokens(ev)) < EVIDENCE_MIN_TOKENS:
            continue
        ok, info = evidence_verbatim(ev, source_text)
        if not ok:
            issues.append(Issue(field=f"claims[{j}].evidence", value=ev[:120],
                                kind="evidence_not_verbatim"))
        ok, info = evidence_claim_coverage(ev, text, source_text, same_lang)
        if not ok:
            issues.append(Issue(field=f"claims[{j}].evidence",
                                value=f"{ev[:80]} | {info}",
                                kind="evidence_coverage"))
        n, roots = claim_evidence_gap(text, ev, source_text)
        if n >= CLAIM_GAP_LIMIT and same_lang:
            issues.append(Issue(field=f"claims[{j}]",
                                value=f"{text[:80]} | karsiliksiz: {','.join(roots[:6])}",
                                kind="claim_evidence_gap"))
    removed_norm = [_norm(r) for r in removed if len(_norm(r)) >= 20]
    for j, claim in enumerate(script.get("claims") or []):
        ev = _norm(str(claim.get("evidence") or ""))
        if len(ev) >= 20 and any(ev in r or r in ev for r in removed_norm):
            issues.append(Issue(field=f"claims[{j}].evidence", value=ev[:120],
                                kind="evidence_removed"))
    issues += check_scene_sources(script, source_text)
    issues += check_language(script)
    return issues


def format_issues(issues: list[Issue]) -> str:
    """Yazara geri verilecek hata listesi (previous_errors girdisi olur).

    `src`/yapi turu degerler somut onarim cumlesidir (hangi sahne/cumle,
    hangi alan eksik/hatali; ornek: "sahne 1 cumle 2: src eksik"); son ek
    almazlar.
    """
    lines = []
    for i in issues:
        if i.kind.startswith("src") or i.kind in (
                "dil", "empty_sentence", "kip", "cerceveleme"):
            lines.append(f"- [{i.kind}] {i.field}: {i.value}")
        else:
            lines.append(f"- [{i.kind}] {i.field}: {i.value!r} kaynakta yok")
    return "\n".join(lines)


async def verify_llm(source_text: str, claims: list[dict],
                     conn=None, client=None,
                     rate_limit_per_min: float | None = None,
                     cap=None, model: str | None = None,
                     cache_salt: str = "") -> VerifyOut:
    """LLM dogrulayici (tier smart, temp 0). Kati olmasi promptta yazar.

    Bicim hatasi (eksik/tekrarli/aralik-disi indeks) icerik karari degildir:
    AYNI model zincirinde bir kez, somut hata listesiyle yeniden istenir
    (gorev `verify_script:format_retry`, cagri butceye ve cap'e dahil,
    log `format_retry`). Ikinci deneme de bozuksa eksik indeksler mevcut
    kuralla unsupported sayilir (guvenlik varsayilani degismez).

    `model` verilirse yalnizca o model denenir (cift-model icin pin);
    `cache_salt` onbellek anahtarini ayirir (model basi / tekrar cagrisi).
    """
    import logging as _logging

    from devbrief.llm.client import complete_json

    _log = _logging.getLogger("devbrief.verifier")
    first = await complete_json(
        "verify_script", "verify_script_v1",
        {"source_text": source_text, "claims": _claims_text(claims),
         "format_errors": "yok"},
        VerifyOut, tier="smart", temperature=0.0,
        conn=conn, client=client, rate_limit_per_min=rate_limit_per_min, cap=cap,
        model=model, cache_salt=cache_salt,
    )
    errs = format_error_list(first, claims)
    if not errs:
        return first
    _log.info("llm format_retry (%s)", ", ".join(errs))
    retry_salt = (f"{cache_salt}|format_retry:{','.join(errs)}" if cache_salt
                  else "format_retry:" + ", ".join(errs))
    second = await complete_json(
        "verify_script:format_retry", "verify_script_v1",
        {"source_text": source_text, "claims": _claims_text(claims),
         "format_errors": ", ".join(errs)},
        VerifyOut, tier="smart", temperature=0.0,
        conn=conn, client=client, rate_limit_per_min=rate_limit_per_min, cap=cap,
        model=model, cache_salt=retry_salt,
    )
    return second


def _claims_text(claims: list[dict]) -> str:
    return "\n".join(
        f"[{i}] {c.get('text', '')} || kanit: {c.get('evidence', '')}"
        for i, c in enumerate(claims))


#: Ingilizce durak sozcukler (kucuk harf; teknik terim/ozel ad icermez).
EN_STOPWORDS = frozenset(
    "a an the and or but if so no not for with to of in on at by from as "
    "is are was were be been have has will would can your our their you we "
    "it he she they that this these those".split()
)
#: Dil denetimi alt siniri (daha kisa birimler etiket sayilir, yargisiz).
MIN_LANG_WORDS = 4


def _is_code_like(tok: str) -> bool:
    """Kod/kisaltma/surum gorunumu: rakam/altcizgi, 2+ buyuk harf, ic buyuk harf."""
    if any(ch.isdigit() or ch == "_" for ch in tok):
        return True
    up = sum(1 for ch in tok if ch.isupper())
    if up >= 2:
        return True
    if len(tok) > 1 and tok.isupper():
        return True
    return up == 1 and not tok[0].isupper()


def english_stopword_ratio(text: str) -> tuple[float, int]:
    """(Ingilizce durak orani, sayilan sozcuk). Kod/ozel-ad/sayi disinda."""
    from devbrief.script.schemas import _MID_CAP_ALLOW, _PROPER_NOUN

    toks = re.findall(r"[A-Za-z]+", text or "")
    kept = []
    for pos, tok in enumerate(toks):
        if len(tok) < 2 or _is_code_like(tok):
            continue
        if pos > 0 and _PROPER_NOUN.fullmatch(tok) and not tok.isupper() \
                and tok not in _MID_CAP_ALLOW:
            continue
        kept.append(tok.lower())
    if not kept:
        return 0.0, 0
    return sum(1 for t in kept if t in EN_STOPWORDS) / len(kept), len(kept)


def _has_turkish_trace(text: str) -> bool:
    """Turkce iz: ozel harf (cgiou) veya ilk-sozcuk disi ek siyirmasi.

    Sinirlar bilinir: kisa etiket-parcalari ("Vite Plugin") yanlis iz
    verebilir; sayi/ozel-ad katmanlari bundan etkilenmez.
    """
    from devbrief.script.metadata import _root_of, _tr_lower

    if re.search(r"[çğıöşüİı]", text or ""):
        return True
    toks = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]+", text or "")
    for tok in toks[1:]:
        if len(tok) >= 4 and _root_of(tok) != _tr_lower(tok):
            return True
    return False


def check_language(script: dict, ratio_thr: float | None = None,
                   min_words: int | None = None) -> list[Issue]:
    """Alanlar Turkce olmali: hook, on_screen, voice (cumle bazinda),
    impact, action. Metnin >=esik orani Ingilizce durak sozcukse veya hic
    Turkce iz yoksa Issue (kind dil, ileti "<alan> Turkce olmali").
    Teknik terim/ozel ad/sayi sayima girmez; kisa birimler yargisiz.
    Esikler config'te (LANG_EN_STOPWORD_RATIO, LANG_MIN_WORDS).
    """
    if ratio_thr is None or min_words is None:
        try:
            from devbrief.config import get_settings as _gs

            _s = _gs()
            if ratio_thr is None:
                ratio_thr = float(_s.lang_en_stopword_ratio)
            if min_words is None:
                min_words = int(_s.lang_min_words)
        except Exception:  # noqa: BLE001 - testsiz ortamda varsayilan
            if ratio_thr is None:
                ratio_thr = 0.40
            if min_words is None:
                min_words = MIN_LANG_WORDS
    issues: list[Issue] = []

    def _check(field: str, text: str) -> None:
        ratio, n = english_stopword_ratio(text)
        if n < (min_words if min_words is not None else MIN_LANG_WORDS):
            return
        if ratio >= ratio_thr:
            issues.append(Issue(
                field=field,
                value=f"{field} Turkce olmali (durak-oran %{ratio:.0%})",
                kind="dil"))
        elif not _has_turkish_trace(text):
            issues.append(Issue(
                field=field,
                value=f"{field} Turkce olmali (Turkce iz yok)",
                kind="dil"))

    from devbrief.script.schemas import scene_units as _units

    if str(script.get("hook") or "").strip():
        _check("hook", str(script["hook"]))
    for i, sc in enumerate(script.get("scenes") or []):
        if not isinstance(sc, dict):
            continue
        if str(sc.get("on_screen") or "").strip():
            _check(f"scenes[{i}].on_screen", str(sc["on_screen"]))
        for sent, _ in _units(sc):
            if sent.strip():
                _check(f"scenes[{i}].voice", sent)
    for key in ("impact", "action"):
        if str(script.get(key) or "").strip():
            _check(key, str(script[key]))
    return issues


def format_error_list(raw: VerifyOut | None, claims: list[dict]) -> list[str]:
    """Bicim hatasi listesi (icerik karari degil).

    Eksik fact indeksi, tekrarli indeks, aralik-disi indeks (fact kumesi
    disinda: negatif, >=len veya inference'a isaret eden) bicim hatasidir.
    Donus somut mesajlar: "indeks 3 eksik", "indeks 5 iki kez",
    "indeks 7 aralik-disi". Bos = bicim temiz. `raw=None` (JSON bozuk)
    bicim degil cozumleme hatasidir -> bos doner (onarim yolu ayri).
    """
    from collections import Counter

    if raw is None:
        return []
    fact_idx = {i for i, c in enumerate(claims)
                if str((c or {}).get("kind") or "fact") != "inference"}
    n = len(claims)
    got = [v.i for v in (raw.verdicts or [])]
    errs: list[str] = []
    seen = set(got)
    for i in sorted(fact_idx - seen):
        errs.append(f"indeks {i} eksik")
    counts = Counter(got)
    for i in sorted(i for i, c in counts.items() if c > 1):
        errs.append(f"indeks {i} iki kez" if counts[i] == 2 else f"indeks {i} {counts[i]} kez")
    for i in sorted({g for g in seen if g not in fact_idx}):
        errs.append(f"indeks {i} aralik-disi (0..{n - 1})")
    return errs


#: Kural turunden red sinifi: yapi/dil/gonderim hatalari yazar-kalitesi,
#: olgu/kanit eslesmeleri kural-hatasi sayilir (cumle bazinda tur).
_WRITER_QUALITY_KINDS = frozenset({
    "dil", "src_arity", "src_range", "src_fusion", "src_missing",
    "empty_sentence", "inference",
})


def violation_kind(kind: str) -> str:
    """Red turu: yazar-kalitesi | kural-hatasi | dogrulayici.

    Deterministik kural ihlalleri: yapi/dil/gonderim -> yazar-kalitesi,
    olgu/kanit eslesmesi -> kural-hatasi. LLM unsupported -> dogrulayici.
    """
    if (kind or "") in _WRITER_QUALITY_KINDS or str(kind or "").startswith("src"):
        return "yazar-kalitesi"
    return "kural-hatasi"


_SENT_FIELD_RX = re.compile(r"scenes\[(\d+)\]\.sentences\[(\d+)\]")


def build_violations(script: dict, source_text: str, rule_issues: list,
                     derived: list, verdict, w_idx: list,
                     dll: list, coverage_rows: list | None = None) -> list[dict]:
    """Red satirlari (failed kayda + audit-pack 'Reddedilen cumleler' icin).

    Her satir: {alan, cümle, src, metrik, kapsama, karar} + teshis
    alanlari {srcs, src_metni, ogeler, eslesen, eslesmeyen, kip,
    cerceveleme}. Kural + turetilmis
    deterministik + LLM-unsupported birlesir; ayni (alan, cumle) teklenir.
    Cumle-dizisi alanlarindaki kural ihlalleri cumle bazinda birlesir
    (tek satirda tum turler + coverage kaydi). Karar cümle bazinda tur
    tasir: red:<tur>:<ayrinti> (tur = yazar-kalitesi | kural-hatasi |
    dogrulayici).
    """
    _ = source_text  # imza simetrisi (gelecek kullanim)
    _ = derived  # turetilmis liste dll uzerinden islenir (imza korunur)
    rows: list[dict] = []
    seen: set[tuple] = set()

    def _add(alan, cumle, src, metrik, kapsama, karar, not_="",
             extra: dict | None = None):
        key = (alan, str(cumle or "")[:80])
        if key in seen:
            return
        seen.add(key)
        rec = {"alan": alan, "cümle": str(cumle or "")[:160], "src": src,
               "metrik": metrik, "kapsama": kapsama, "karar": karar,
               "not": str(not_ or "")[:160]}
        if extra:
            rec.update(extra)
        rows.append(rec)

    def _cov_extra(i: int, j: int) -> dict:
        for r in coverage_rows or []:
            if r.get("scene_idx") == i and r.get("sent_idx") == j:
                return {
                    "srcs": list(r.get("srcs") or []),
                    "src_metni": [str(t)[:160] for t in r.get("src_texts", [])],
                    "ogeler": {k: list(v) for k, v in
                               (r.get("items") or {}).items()},
                    "eslesen": {k: list(v) for k, v in
                                (r.get("matched") or {}).items()},
                    "eslesmeyen": {k: list(v) for k, v in
                                   (r.get("missing") or {}).items()},
                    "kip": dict(r.get("mood") or {}),
                    "cerceveleme": list(r.get("framing") or []),
                    "kapsama": r.get("coverage_info", "-"),
                    "metrik": r.get("metric", "-"),
                }
        return {}

    merged: dict[tuple[int, int], list] = {}
    for i in rule_issues or []:
        # Alan `scenes[i].sentences[j](.alt)` ise cumle satirinda birlesir.
        mm = _SENT_FIELD_RX.match(str(getattr(i, "field", "")))
        if mm and coverage_rows is not None:
            merged.setdefault((int(mm.group(1)), int(mm.group(2))), []).append(i)
            continue
        kind = str(getattr(i, "kind", "?"))
        _add(getattr(i, "field", "?"), getattr(i, "value", ""),
             "-", "-", "-", f"red:{violation_kind(kind)}:{kind}")
    for (i, j), iss in merged.items():
        kinds = list(dict.fromkeys(str(x.kind) for x in iss))
        turs = {violation_kind(k) for k in kinds}
        tur = ("kural-hatasi" if "kural-hatasi" in turs
               else "yazar-kalitesi" if "yazar-kalitesi" in turs
               else sorted(turs)[0])
        extra = _cov_extra(i, j)
        text = next((r.get("text", "") for r in coverage_rows or []
                     if r.get("scene_idx") == i and r.get("sent_idx") == j), "")
        refs = ",".join(f"#{s}" for s in (extra.get("srcs") or [])) or "-"
        _add(f"scenes[{i}].sentences[{j}]", text, refs,
             extra.get("metrik", "-"), extra.get("kapsama", "-"),
             f"red:{tur}:" + "+".join(kinds),
             "; ".join(str(x.value) for x in iss)[:300], extra)
    if verdict is not None:
        unsup = {v.i: v for v in (verdict.verdicts or [])
                 if v.verdict in ("contradicted", "not_stated")}
        claims = (script.get("claims") or []) if isinstance(script, dict) else []
        for pos, v in unsup.items():
            if pos < len(w_idx):
                j = w_idx[pos]
                cl = claims[j] if 0 <= j < len(claims) else {}
                _add(f"claims[{j}]", cl.get("text", ""), "-",
                     "llm", "-", f"red:dogrulayici:{v.verdict}", v.note)
            else:
                k = pos - len(w_idx)
                if 0 <= k < len(dll or []):
                    d = dll[k]
                    _add(d.get("field", "?"), d.get("text", ""),
                         ("#" + str(d["src"]) if d.get("src") is not None else "-"),
                         d.get("metric", "?"), d.get("cov_info", "-"),
                         f"red:dogrulayici:{v.verdict}", v.note)
    return rows


def failed_violations(script: dict, source_text: str, verdict=None,
                      removed: list[str] | tuple = ()) -> list[dict]:
    """Failed bolum icin red satirlari (kural + turetilmis + LLM).

    `verify_episode` donus degerlerine bagli kalmadan, saklanan SON senaryo
    ve kaynak metinden deterministik olarak uretilir; LLM verdict'i varsa
    unsupported satirlari da eklenir. Bos = kural temiz (red baska nedenden).
    Cumle-dizisi satirlari coverage kaydini tasir (src cumleleri, ogeler,
    eslesmeyenler, metrik, kapsama).
    """
    try:
        rule_issues = list(check_grounding(script, source_text, removed))
    except Exception:  # noqa: BLE001 - denetim disi birakilir
        rule_issues = []
    try:
        v_issues, _ = check_voice_grounding(script, source_text)
        rule_issues = list(rule_issues) + list(v_issues)
    except Exception:  # noqa: BLE001
        pass
    try:
        _, w_idx, dll = build_llm_claims(script, source_text)
    except Exception:  # noqa: BLE001
        w_idx, dll = [], []
    try:
        cov_rows = sentence_coverage_rows(script, source_text)
    except Exception:  # noqa: BLE001
        cov_rows = []
    return build_violations(script, source_text, rule_issues, [], verdict,
                            w_idx, dll, cov_rows)


def _combine_dual(norms: dict[str, VerifyOut], claims: list[dict]
                  ) -> tuple[VerifyOut, dict]:
    """Iki model birlesimi: claim yalnizca IKISI de entailed ise gecer;
    biri bile not_stated/contradicted ise unsupported (katman cift-model).
    Donus: (birlesik normalize VerifyOut, bilgi)."""
    models = list(norms)
    by_model = {m: {v.i: v for v in norms[m].verdicts} for m in models}
    fact_idx = [i for i, c in enumerate(claims)
                if str((c or {}).get("kind") or "fact") != "inference"]
    full, per_index = [], {}
    for i in fact_idx:
        vs = {m: (by_model[m].get(i).verdict if by_model[m].get(i) else "not_stated")
              for m in models}
        notes = {m: ((by_model[m].get(i).note if by_model[m].get(i) else "") or "")
                 for m in models}
        if all(v == "entailed" for v in vs.values()):
            full.append(IndexVerdict(i=i, verdict="entailed",
                                     note="; ".join(f"{m}:{(notes[m] or '')[:100]}"
                                                    for m in models)[:300]))
        else:
            worst = "contradicted" if "contradicted" in vs.values() else "not_stated"
            detail = " ".join(f"{m}={vs[m]}" for m in models)
            note_bits = [f"{m}:{(notes[m] or '')[:120]}" for m in models
                         if vs[m] != "entailed"]
            full.append(IndexVerdict(
                i=i, verdict=worst,
                note=f"cift-model ({detail})" + (f" | {'; '.join(note_bits)}"
                                                 if note_bits else "")[:300]))
        per_index[i] = dict(vs)
    overall = "pass" if all(v.verdict == "entailed" for v in full) else "fail"
    return VerifyOut(verdicts=full, overall=overall), {"models": models,
                                                      "per_index": per_index}


def _combine_flaky(n1: VerifyOut, n2: VerifyOut, model: str,
                   claims: list[dict]) -> tuple[VerifyOut, dict]:
    """Tek-model kararsizlik: iki ayri cagri AYNI karari vermeli; farkliysa
    unsupported (not: flaky). Donus: (birlesik VerifyOut, bilgi)."""
    m1 = {v.i: v.verdict for v in n1.verdicts}
    m2 = {v.i: v.verdict for v in n2.verdicts}
    n1n = {v.i: v.note for v in n1.verdicts}
    fact_idx = [i for i, c in enumerate(claims)
                if str((c or {}).get("kind") or "fact") != "inference"]
    full, flaky = [], []
    for i in fact_idx:
        a, b = m1.get(i, "not_stated"), m2.get(i, "not_stated")
        ent_a, ent_b = (a == "entailed"), (b == "entailed")
        if ent_a and ent_b:
            full.append(IndexVerdict(i=i, verdict="entailed",
                                     note=(n1n.get(i, ""))[:200]))
        elif ent_a != ent_b:
            flaky.append(i)
            full.append(IndexVerdict(
                i=i, verdict="not_stated",
                note=f"flaky: {model} iki cagri farkli ({a} vs {b})"[:200]))
        else:
            worst = "contradicted" if "contradicted" in (a, b) else "not_stated"
            full.append(IndexVerdict(i=i, verdict=worst,
                                     note=(n1n.get(i, "") or b)[:200]))
    overall = "pass" if all(v.verdict == "entailed" for v in full) else "fail"
    return VerifyOut(verdicts=full, overall=overall), {"models": [model],
                                                      "flaky": flaky}


async def verify_with_models(source_text: str, claims: list[dict],
                             models: list[str], conn=None, client=None,
                             rate_limit_per_min: float | None = None,
                             cap=None, verify_cap=None,
                             flaky_double: bool = False) -> tuple[VerifyOut, dict]:
    """Coklu dogrulama: VERIFY listesinden iki farkli model (tekse tek).

    - Iki model: her biri ayri cagri (format retry dahil, model basi ayri
      onbellek salt); claim yalnizca ikisi de entailed ise gecer.
    - Tek model + flaky_double: ayni modele iki ayri cagri (farkli salt);
      karar farki unsupported ("flaky").
    `cap` bolum kotasi (tum cagrilara islenir, retry dahil);
    `verify_cap` verify alt kotasi (VERIFY_CALL_CAP; her model turu harcar).
    Donus: (birlesik normalize VerifyOut, bilgi).
    """
    if len(models) >= 2:
        pair = list(models[:2])
        norms = {}
        for m in pair:
            if verify_cap is not None:
                verify_cap.charge()
            raw = await verify_llm(source_text, claims, conn=conn, client=client,
                                   rate_limit_per_min=rate_limit_per_min,
                                   cap=cap, model=m, cache_salt=f"verify:{m}")
            norms[m] = normalize_verdicts(raw, claims)
        return _combine_dual(norms, claims)
    m = models[0]
    if verify_cap is not None:
        verify_cap.charge()
    first = normalize_verdicts(await verify_llm(
        source_text, claims, conn=conn, client=client,
        rate_limit_per_min=rate_limit_per_min, cap=cap,
        model=m, cache_salt=f"verify:{m}:1"), claims)
    if not flaky_double:
        return first, {"models": [m], "flaky": []}
    if verify_cap is not None:
        verify_cap.charge()
    second = normalize_verdicts(await verify_llm(
        source_text, claims, conn=conn, client=client,
        rate_limit_per_min=rate_limit_per_min, cap=cap,
        model=m, cache_salt=f"verify:{m}:2"), claims)
    return _combine_flaky(first, second, m, claims)


def normalize_verdicts(verdict: VerifyOut, claims: list[dict]) -> VerifyOut:
    """Indeks sozlesmesi: cikti iddiayi indeksle refere eder.

    Yalnizca fact iddialar zorunlu; inference degerlendirilmez (yorum).
    Eksik fact indeksi = unsupported (degerlendirilmemis iddia gecemez).
    overall, model "pass" dese bile unsupported varsa fail olur.
    """
    by_index = {}
    for v in verdict.verdicts:
        if v.i not in by_index:
            by_index[v.i] = v
    full = []
    for i, c in enumerate(claims):
        if str(c.get("kind") or "fact") == "inference":
            continue
        v = by_index.get(i)
        if v is None:
            full.append(IndexVerdict(i=i, verdict="not_stated", note="degerlendirilmedi"))
        else:
            full.append(v)
    overall = "pass" if verdict.overall == "pass" and not any(
        v.verdict in ("contradicted", "not_stated") for v in full) else "fail"
    return VerifyOut(verdicts=full, overall=overall)


async def verify_episode(script: dict, source_text: str, conn=None, client=None,
                         rate_limit_per_min: float | None = None,
                         max_turns: int = MAX_REWRITE_TURNS,
                         max_calls: int = EPISODE_CALL_CAP,
                         removed: list[str] | tuple = (),
                         verify_models: list[str] | None = None,
                         verify_call_cap: int | None = None,
                         ) -> tuple[str, dict | None, VerifyOut | None, str]:
    """Tam dogrulama: once kural (siki kanit dahil), sonra cift-model LLM;
    en fazla `max_turns` yeniden yazim.

    Donus: (durum, script_dict|None, verdict|None, neden).
    `final` basarida gecerli senaryo, basarisizlikta SON DENENEN senaryodur
    (denetim/audit icin saklanir; yayinlanamaz).
    Bolum kotasi `max_calls` yazar cagrilarini sinirlar; verify LLM cagrilari
    ayrica VERIFY_CALL_CAP (varsayilan 6) alt kotasina islenir. Ikisi de
    asimda neden "call_cap". METADATA/script cap'leri asilmaz (gevsetilmedi).
    """
    from devbrief.llm.client import AllModelsFailed, CallCap, CallCapExceeded
    from devbrief.script import writer as _writer

    def _resolve_verify() -> tuple[list[str], int]:
        try:
            from devbrief.config import get_settings as _gs

            _s = _gs()
            ms = [m for m in _s.verify_models if _s.model_allowed(m)][:2]
            return ms, int(_s.verify_call_cap)
        except Exception:  # noqa: BLE001 - testsiz ortamda guvenli varsayilan
            return [], 6

    resolved_models, resolved_cap = _resolve_verify()
    vm = verify_models if verify_models is not None else resolved_models
    vcap = CallCap(verify_call_cap if verify_call_cap is not None
                   else resolved_cap)
    cap = CallCap(max_calls)
    previous_errors = ""
    verdict: VerifyOut | None = None
    current = script

    def _llm_notes(combined_v: VerifyOut, w_idx: list[int],
                   dll: list[dict]) -> list[str]:
        """Birlesik karardan yazara geri bildirim: yazar + turetilmis."""
        notes = []
        for pos, v in enumerate(combined_v.verdicts):
            if v.verdict == "entailed":
                continue
            if pos < len(w_idx):
                notes.append(f"- [{v.verdict}] #{w_idx[pos]}: {v.note[:200]}")
            else:
                d = dll[pos - len(w_idx)] if pos - len(w_idx) < len(dll) else {}
                notes.append(f"- [{v.verdict}] {d.get('field', '?')}: "
                             f"{d.get('text', '')[:120]} | {v.note[:150]}")
        return notes

    try:
        for turn in range(max_turns + 1):
            rule_issues = check_grounding(current, source_text, removed)
            v_issues, _derived = check_voice_grounding(current, source_text)
            rule_issues = list(rule_issues) + list(v_issues)
            if rule_issues:
                previous_errors = "Kural ihlalleri:\n" + format_issues(rule_issues)
            else:
                llm_claims, w_idx, dll = build_llm_claims(current, source_text)
                try:
                    if not vm:
                        raw = await verify_llm(
                            source_text, llm_claims,
                            conn=conn, client=client, rate_limit_per_min=rate_limit_per_min,
                            cap=cap)
                        verdict = normalize_verdicts(raw, llm_claims)
                    else:
                        combined, _info = await verify_with_models(
                            source_text, llm_claims, vm,
                            conn=conn, client=client, rate_limit_per_min=rate_limit_per_min,
                            cap=cap, verify_cap=vcap, flaky_double=(len(vm) == 1))
                        verdict = combined
                except AllModelsFailed as e:
                    # Altyapi arızasi (bos yanit/zaman asimi/kesinti): icerik
                    # hukmu degil, bolumu oldurmez; tur icinde yeniden denenir.
                    previous_errors = (
                        f"Dogrulama altyapi hatasi: {e}"[:300]
                        + " Icerik ayni kalabilir, dogrulama tekrar denenecek.")
                    if turn == max_turns:
                        break
                    current = (await _writer.write_script(
                        source_text, current.get("source_url", ""), previous_errors,
                        conn=conn, client=client,
                        rate_limit_per_min=rate_limit_per_min, cap=cap,
                        removed=removed)).model_dump()
                    continue
                if verdict.overall == "pass":
                    verdict = verdict.model_copy(
                        update={"voice_derived": [
                            {"field": d["field"], "text": d["text"],
                             "status": d["status"],
                             "note": d.get("note", "")} for d in _derived]})
                    return "verified", current, verdict, ""
                notes = _llm_notes(verdict, w_idx, dll)
                previous_errors = ("LLM dogrulama (cift-model):\n" if vm
                                   else "LLM dogrulama:\n") + "\n".join(notes)
            if turn == max_turns:
                break
            current = (await _writer.write_script(
                source_text, current.get("source_url", ""), previous_errors,
                conn=conn, client=client,
                rate_limit_per_min=rate_limit_per_min, cap=cap,
                removed=removed)).model_dump()
    except CallCapExceeded:
        return "failed", current, verdict, "call_cap"
    reason = (previous_errors or "dogrulama gecilemedi")[:2000]
    return "failed", current, verdict, reason
