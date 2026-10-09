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


def _percent_ok(value: str, source: str) -> bool:
    """Yuzde sira-bagimsiz: %40 ile 40% ayni sayilir (TR/EN yazim farki)."""
    if _norm(value) in _norm(source):
        return True
    digits = "".join(_DIGITS.findall(value))
    return bool(digits) and digits in set(_NUMBER.findall(source))


@dataclass
class Issue:
    field: str  # örn. "scenes[1].voice" / "code" / "claim"
    value: str  # kaynakta bulunamayan deger
    kind: str  # version | percent | date | number | code


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _find_missing(values: list[str], source: str, kind: str, field: str) -> list[Issue]:
    src = _norm(source)
    out = []
    for v in dict.fromkeys(values):  # sira korunarak tekillestir
        if _norm(v) not in src:
            out.append(Issue(field=field, value=v, kind=kind))
    return out


def check_grounding(script: dict, source_text: str) -> list[Issue]:
    """Senaryodaki somut degerleri kaynakla karsilastir. Bos = gecti."""
    issues: list[Issue] = []
    scenes = script.get("scenes") or []
    for i, sc in enumerate(scenes):
        base = f"scenes[{i}]"
        voice = str(sc.get("voice") or "")
        issues += _find_missing(_VERSION.findall(voice), source_text, "version", f"{base}.voice")
        for p in dict.fromkeys(_PERCENT.findall(voice)):
            if not _percent_ok(p, source_text):
                issues.append(Issue(field=f"{base}.voice", value=p, kind="percent"))
        issues += _find_missing(_DATE.findall(voice), source_text, "date", f"{base}.voice")
        issues += _find_missing(
            [n for n in _NUMBER.findall(voice) if not _VERSION.fullmatch(n or "")],
            source_text, "number", f"{base}.voice",
        )
        code = str(sc.get("code") or "")
        if code.strip():
            toks = [t for t in dict.fromkeys(_TOKEN.findall(code)) if len(t) >= 4]
            issues += _find_missing(toks, source_text, "code", f"{base}.code")
    for j, claim in enumerate(script.get("claims") or []):
        text = str(claim.get("text") or "")
        issues += _find_missing(_VERSION.findall(text), source_text, "version", f"claims[{j}]")
        issues += _find_missing(_NUMBER.findall(text), source_text, "number", f"claims[{j}]")
    hook = str(script.get("hook") or "")
    issues += _find_missing(_VERSION.findall(hook), source_text, "version", "hook")
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

    Eksik indeks = unsupported (degerlendirilmemis iddia gecemez).
    overall, model "pass" dese bile unsupported varsa fail olur.
    """
    by_index = {}
    for v in verdict.verdicts:
        if v.i not in by_index:
            by_index[v.i] = v
    full = []
    for i, _c in enumerate(claims):
        v = by_index.get(i)
        if v is None:
            full.append(IndexVerdict(i=i, verdict="unsupported", note="degerlendirilmedi"))
        else:
            full.append(v)
    overall = "pass" if verdict.overall == "pass" and not any(
        v.verdict == "unsupported" for v in full) else "fail"
    return VerifyOut(verdicts=full, overall=overall)


async def verify_episode(script: dict, source_text: str, conn=None, client=None,
                         rate_limit_per_min: float | None = None,
                         max_turns: int = MAX_REWRITE_TURNS,
                         max_calls: int = 8
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
            rule_issues = check_grounding(current, source_text)
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
                notes = [f"- [{v.verdict}] #{v.i}: {v.note[:200]}" for v in verdict.verdicts
                         if v.verdict != "supported"]
                previous_errors = "LLM dogrulama:\n" + "\n".join(notes)
            if turn == max_turns:
                break
            current = (await _writer.write_script(
                source_text, current.get("source_url", ""), previous_errors,
                conn=conn, client=client,
                rate_limit_per_min=rate_limit_per_min, cap=cap)).model_dump()
    except CallCapExceeded:
        return "failed", None, verdict, "call_cap"
    reason = (previous_errors or "dogrulama gecilemedi")[:2000]
    return "failed", None, verdict, reason
