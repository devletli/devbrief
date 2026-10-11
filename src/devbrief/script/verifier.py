"""Kural tabanli dogrulayici (LLM'siz). LLM dogrulayici Faz 2 kilidinde.

Senaryodaki somut degerler kaynak metinde birebir gecmeli (uydurma yasagi):
surum (1.2 / 1.2.3), yuzde (%40), tarih (09.10.2026, 2026-10-09, yil),
sayi (rakamla yazilan) ve kod ifadeleri. Gecmeyen her deger bir Issue'dur.
Bos liste = gecti. Yeniden yazim en fazla 2 tur (MAX_REWRITE_TURNS).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from devbrief.script.schemas import IndexVerdict, VerifyOut

MAX_REWRITE_TURNS = 2

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

_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def _doc_same_language(script: dict, source_text: str,
                        thr: float = DOC_LANG_THRESHOLD) -> bool:
    """Belge-dil karari: senaryo harf-koklerinin `thr` ve uzeri kaynakta
    geciyorsa ayni-dil sayilir (siki kok katmanlari anlamlidir).

    Capraz-dilde kok-ortusmesi ayirt edici degildir (iyi ozet bile dusuk
    skor alir); o durumda yalnizca (a) sayi/ozel-ad + (c) LLM calisir.
    """
    parts = [str(script.get("hook") or "")]
    for sc in script.get("scenes") or []:
        if isinstance(sc, dict):
            parts.append(str(sc.get("on_screen") or ""))
            parts.append(str(sc.get("voice") or ""))
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
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


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

    Sira: hook, her sahne (on_screen, voice), impact, action. Her kayit:
    {field, text}. Yazar claims listesine bagli degildir (omission acigini
    kapatir); yazar claim'leri ek/yardimci kalir.
    """
    out: list[dict] = []
    if str(script.get("hook") or "").strip():
        out.append({"field": "hook", "text": str(script["hook"]).strip()})
    for i, sc in enumerate(script.get("scenes") or []):
        if not isinstance(sc, dict):
            continue
        if str(sc.get("on_screen") or "").strip():
            out.append({"field": f"scenes[{i}].on_screen",
                        "text": str(sc["on_screen"]).strip()})
        for sent in _split_sentences(sc.get("voice") or ""):
            out.append({"field": f"scenes[{i}].voice", "text": sent})
    for key in ("impact", "action"):
        for sent in _split_sentences(script.get(key) or ""):
            out.append({"field": key, "text": sent})
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
                     "note": "", "issue": None, "window": None}
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
        # (b) kapsama (icerikli + ayni-dil).
        if same_lang and cov < EVIDENCE_COVERAGE_MIN:
            rec.update(status="desteksiz", llm=True,
                       note=f"kapsama={cov:.0%}<%70",
                       issue="voice_coverage")
            issues.append(Issue(field=d["field"],
                                value=f"{text[:100]} | {rec['note']}",
                                kind="voice_coverage"))
            derived.append(rec)
            continue
        if not same_lang:
            rec["note"] = "diger-dil:(b)-muaf"
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


def _percent_ok(value: str, source: str) -> bool:
    """Yuzde sira-bagimsiz: %40 ile 40% ayni sayilir (TR/EN yazim farki)."""
    return match_value(value, source) == "match"


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
    issues: list[Issue] = []
    scenes = script.get("scenes") or []
    for i, sc in enumerate(scenes):
        base = f"scenes[{i}]"
        voice = str(sc.get("voice") or "")
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

            if find_inference_violations(text):
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
    return issues


def format_issues(issues: list[Issue]) -> str:
    """Yazara geri verilecek hata listesi (previous_errors girdisi olur)."""
    return "\n".join(f"- [{i.kind}] {i.field}: {i.value!r} kaynakta yok" for i in issues)


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
                         max_calls: int = 8,
                         removed: list[str] | tuple = (),
                         verify_models: list[str] | None = None,
                         verify_call_cap: int | None = None,
                         ) -> tuple[str, dict | None, VerifyOut | None, str]:
    """Tam dogrulama: once kural (siki kanit dahil), sonra cift-model LLM;
    en fazla `max_turns` yeniden yazim.

    Donus: (durum, script_dict|None, verdict|None, neden).
    Bolum kotasi `max_calls` yazar cagrilarini sinirlar; verify LLM cagrilari
    ayrica VERIFY_CALL_CAP (varsayilan 6) alt kotasina islenir. Ikisi de
    asimda neden "call_cap". METADATA/script cap'leri asilmaz (gevsetilmedi).
    """
    from devbrief.llm.client import CallCap, CallCapExceeded
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
        return "failed", None, verdict, "call_cap"
    reason = (previous_errors or "dogrulama gecilemedi")[:2000]
    return "failed", None, verdict, reason
