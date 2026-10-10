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
                     cap=None) -> VerifyOut:
    """LLM dogrulayici (tier smart, temp 0). Kati olmasi promptta yazar."""
    from devbrief.llm.client import complete_json

    return await complete_json(
        "verify_script", "verify_script_v1",
        {"source_text": source_text, "claims": _claims_text(claims)},
        VerifyOut, tier="smart", temperature=0.0,
        conn=conn, client=client, rate_limit_per_min=rate_limit_per_min, cap=cap,
    )


def _claims_text(claims: list[dict]) -> str:
    return "\n".join(
        f"[{i}] {c.get('text', '')} || kanit: {c.get('evidence', '')}"
        for i, c in enumerate(claims))


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
                         removed: list[str] | tuple = ()
                         ) -> tuple[str, dict | None, VerifyOut | None, str]:
    """Tam dogrulama: once kural, sonra LLM; en fazla `max_turns` yeniden yazim.

    Donus: (durum, script_dict|None, verdict|None, neden).
    Bolum basina toplam LLM cagrisi `max_calls` ile sinirli; asimda neden "call_cap".
    """
    from devbrief.llm.client import CallCap, CallCapExceeded
    from devbrief.script import writer as _writer

    cap = CallCap(max_calls)
    previous_errors = ""
    verdict: VerifyOut | None = None
    current = script
    try:
        for turn in range(max_turns + 1):
            rule_issues = check_grounding(current, source_text, removed)
            if rule_issues:
                previous_errors = "Kural ihlalleri:\n" + format_issues(rule_issues)
            else:
                raw = await verify_llm(
                    source_text, current.get("claims") or [],
                    conn=conn, client=client, rate_limit_per_min=rate_limit_per_min,
                    cap=cap)
                verdict = normalize_verdicts(raw, current.get("claims") or [])
                if verdict.overall == "pass":
                    return "verified", current, verdict, ""
                notes = [f"- [{v.verdict}] #{v.i}: {v.note[:200]}"
                         for v in verdict.verdicts if v.verdict != "entailed"]
                previous_errors = "LLM dogrulama:\n" + "\n".join(notes)
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
