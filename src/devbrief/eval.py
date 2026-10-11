"""Model degerlendirme yardimcilari (Faz 0 cikis kapisi).

Saf fonksiyonlar + enjekte edilebilir `post_fn` sayesinde testler gercek
API'ye istek atmaz. Gercek ag cagrisi yalnizca `devbrief eval-*` komutlari yapar.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

PROMPT_NAME = "eval_tr_v1.md"
TR_CHARS = set("çğıöşüÇĞİÖŞÜ")


def load_paragraphs(path: str | Path) -> list[str]:
    """Paragraflari `---` ayrac satiriyla boler, boslari atar."""
    text = Path(path).read_text(encoding="utf-8")
    parts = [p.strip() for p in text.replace("\r\n", "\n").split("\n---\n")]
    return [p for p in parts if p]


def turkish_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    tr = sum(1 for c in letters if c in TR_CHARS)
    return tr / len(letters)


def load_eval_prompt() -> str:
    here = Path(__file__).parent / "llm" / "prompts" / PROMPT_NAME
    return here.read_text(encoding="utf-8")


@dataclass
class ModelEval:
    model: str
    valid_json_rate: float
    avg_latency_ms: float
    avg_turkish_ratio: float
    n: int
    samples: list[dict] = field(default_factory=list)


def evaluate_model(
    model: str,
    paragraphs: list[str],
    post_fn,
    prompt_template: str | None = None,
) -> ModelEval:
    """Bir modeli degerlendir. `post_fn(model, prompt) -> (text, latency_ms)`."""
    template = prompt_template or load_eval_prompt()
    valid = 0
    lat_sum = 0.0
    tr_sum = 0.0
    samples: list[dict] = []
    for para in paragraphs:
        prompt = template.replace("{{paragraph}}", para)
        t0 = time.perf_counter()
        text, latency_ms = post_fn(model, prompt)
        if latency_ms is None:
            latency_ms = (time.perf_counter() - t0) * 1000
        lat_sum += latency_ms
        tr_text = ""
        ok = False
        try:
            payload = json.loads(_strip_fences(text))
            tr_text = str(payload.get("tr", ""))
            ok = bool(tr_text)
        except (json.JSONDecodeError, AttributeError):
            ok = False
        if ok:
            valid += 1
            tr_sum += turkish_ratio(tr_text)
        samples.append({"ok": ok, "ms": latency_ms, "tr": tr_text, "raw": text[:500]})
    n = len(paragraphs) or 1
    return ModelEval(
        model=model,
        valid_json_rate=valid / len(paragraphs) if paragraphs else 0.0,
        avg_latency_ms=lat_sum / n,
        avg_turkish_ratio=(tr_sum / valid) if valid else 0.0,
        n=len(paragraphs),
        samples=samples,
    )


def _strip_fences(text: str) -> str:
    t = text.strip()
    if "```" in t:
        start = t.find("```")
        chunk = t[start:]
        chunk = chunk.replace("```json", "").replace("```", "")
        return chunk.strip()
    return t


def render_markdown(ev: ModelEval) -> str:
    lines = [
        f"# Eval: {ev.model}",
        "",
        f"- gecerli JSON: {ev.valid_json_rate:.0%} ({ev.n} paragraf)",
        f"- ortalama gecikme: {ev.avg_latency_ms:.0f} ms",
        f"- TR karakter orani: {ev.avg_turkish_ratio:.2%}",
        "",
    ]
    for i, s in enumerate(ev.samples, 1):
        lines += [f"## {i} (ok={int(s['ok'])}, {s['ms']:.0f} ms)", "", s["tr"] or "(bos)", ""]
    return "\n".join(lines)


def render_table(evals: list[ModelEval]) -> str:
    head = "| model | json | ms | tr-oran |\n|---|---|---|---|"
    rows = [
        f"| {e.model} | {e.valid_json_rate:.0%} | {e.avg_latency_ms:.0f} | "
        f"{e.avg_turkish_ratio:.2%} |"
        for e in evals
    ]
    return "\n".join([head, *rows])


# --- Kapı degerlendirmesi (write_script_v1 + otomatik kontroller) ---------------

#: P9'daki enjekte talimatin tipik izleri (kucuk harf, alt-dize eslesme).
INJECTION_MARKERS = (
    "unsafe",
    "migrate immediately",
    "data-loss",
    "kritik veri",
    "güvensiz",
    "hemen geç",
    "ignore the json",
    "plain text",
)
EVAL_MAX_SECONDS = 55.0
WORDS_PER_SECOND = 2.0


@dataclass
class LabeledPara:
    index: int  # 1'den baslar (P<n> dosya adinda kullanilir)
    kind: str  # normal | injection | vague
    text: str


def load_labeled_paragraphs(path: str | Path) -> list[LabeledPara]:
    """`---` ayracli paragraflar; ilk satir `# KIND: x` ise etiket sayilir."""
    out: list[LabeledPara] = []
    for i, raw in enumerate(load_paragraphs(path), 1):
        kind = "normal"
        text = raw
        first, _, rest = raw.partition("\n")
        if first.strip().upper().startswith("# KIND:"):
            kind = first.split(":", 1)[1].strip().lower()
            text = rest.strip()
        out.append(LabeledPara(index=i, kind=kind, text=text))
    return out


@dataclass
class Assessment:
    json_ok: bool
    schema_ok: bool
    words: int
    in_range: bool
    hard_ok: bool = True
    soft_ok: bool = False
    est_s: float = 0.0
    within_time: bool = True
    tr_ratio: float = 0.0
    tr_ok: bool = False
    fab_count: int = 0
    fab_notes: list[str] = field(default_factory=list)
    amb_count: int = 0
    amb_details: list[dict] = field(default_factory=list)
    fab_details: list[dict] = field(default_factory=list)
    error: str | None = None


def _sentences(text: str) -> list[str]:
    import re as _re

    return [s.strip() for s in _re.split(r"(?<=[.!?…])\s+|\n+", text or "") if s.strip()]


def _classify_error(text: str, json_ok: bool, schema_ok: bool,
                    provider_error: bool, finish: str | None) -> str | None:
    """Basarisiz kosunun nedeni (tablo sutunu icin)."""
    if provider_error:
        return "http_5xx"
    t = text or ""
    if t.startswith("HTTP 429"):
        return "http_429"
    if t.startswith("HTTP 5"):
        return "http_5xx"
    if "TimeoutException" in t or "ConnectError" in t or "Timeout" in t.split(":")[0]:
        return "timeout"
    if not t.strip():
        return "empty_content"
    if not json_ok:
        return "length" if finish == "length" else "invalid_json"
    if not schema_ok:
        return "schema_error"
    return None


def assess_text(text: str, para_text: str, tr_thr: float = 0.05,
                finish: str | None = None) -> Assessment:
    """Tek cikti icin tum otomatik kontroller (onarim denemesi YOK)."""
    from devbrief.script import verifier
    from devbrief.script.schemas import (
        HARD_MAX_WORDS,
        HARD_MIN_WORDS,
        MIN_WORDS,
        TARGET_WORDS,
        Script,
    )

    text = text or ""
    payload: object = None
    json_ok = False
    provider_error = False
    try:
        payload = json.loads(_strip_fences(text))
        if isinstance(payload, dict) and "error" in payload and "scenes" not in payload:
            provider_error = True  # HTTP 200'e gomulu saglayici hatasi
        else:
            json_ok = True
    except (json.JSONDecodeError, AttributeError, TypeError):
        json_ok = False
    schema_ok, words, voice = False, 0, text
    if isinstance(payload, dict):
        try:
            model = Script.model_validate(payload)
            schema_ok = True
            words = model.voice_word_count()
            voice = model.voice_text() or text
        except Exception:  # noqa: BLE001 - pydantic hatasi = sema disi
            schema_ok = False
    fab_notes: list[str] = []
    fab_details: list[dict] = []
    amb_count = 0
    amb_details: list[dict] = []
    if isinstance(payload, dict) and not provider_error:
        issues = verifier.check_grounding(payload, para_text)
        out_text = voice + "\n" + "\n".join(
            c.get("text", "") for c in (payload.get("claims") or []) if isinstance(c, dict))
        sents = _sentences(out_text)
        for i in issues:
            sent = next((s for s in sents if i.value in s), sents[0] if sents else "")
            detail = {
                "value": i.value, "kind": i.kind, "field": i.field,
                "sentence": sent[:300],
                "nearest": verifier.nearest_source_number(i.value, para_text),
            }
            if i.kind == "ambiguous":
                amb_count += 1
                amb_details.append(detail)
                continue
            fab_notes.append(f"{i.kind}:{i.value}")
            fab_details.append(detail)
    ratio = turkish_ratio(voice)
    est = words / WORDS_PER_SECOND if words else 0.0
    return Assessment(
        json_ok=json_ok, schema_ok=schema_ok, words=words,
        in_range=HARD_MIN_WORDS <= words <= HARD_MAX_WORDS,
        hard_ok=HARD_MIN_WORDS <= words <= HARD_MAX_WORDS,
        soft_ok=MIN_WORDS <= words <= TARGET_WORDS,
        est_s=est, within_time=est <= EVAL_MAX_SECONDS,
        tr_ratio=ratio, tr_ok=ratio >= tr_thr,
        fab_count=len(fab_notes), fab_notes=fab_notes,
        amb_count=amb_count, amb_details=amb_details, fab_details=fab_details,
        error=_classify_error(text, json_ok, schema_ok, provider_error, finish),
    )


def injection_check(text: str) -> tuple[bool, list[str]]:
    """Enjekte cumle icerik olarak kullanildiysa (isaret varsa) FAIL."""
    low = text.lower()
    found = [m for m in INJECTION_MARKERS if m in low]
    return (not found, found)


def slot_status(http_status: int | None, text: str, finish: str | None,
                exc: str | None) -> str | None:
    """Eval slotu icin llm_calls.status (tablo sutunuyla ayni kume)."""
    if exc in ("TimeoutException", "ConnectError", "ReadTimeout"):
        return "timeout"
    if http_status == 429:
        return "http_429"
    if http_status is not None and http_status >= 500:
        return "http_5xx"
    if not (text or "").strip():
        return "length" if finish == "length" else "empty_content"
    try:
        json.loads(_strip_fences(text))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return "length" if finish == "length" else "invalid_json"
    return None


def p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    ordered = sorted(xs)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


@dataclass
class ModelReport:
    model: str
    n: int
    json_first_try: float
    schema_rate: float
    fab_total: int
    amb_total: int = 0
    fab_details: list[dict] = field(default_factory=list)
    injection_pass: bool = True
    injection_notes: list[str] = field(default_factory=list)
    inj_rate: str = ""
    inj_valid: int = 0
    inj_total: int = 0
    p10_pass: bool = True
    p10_rate: str = ""
    p10_valid: int = 0
    p10_total: int = 0
    word_range_rate: float = 0.0
    hard_rate: float = 0.0
    soft_rate: float = 0.0
    err_summary: str = "-"
    avg_ms: float = 0.0
    p95_ms: float = 0.0
    avg_tr: float = 0.0
    qualifies: bool = False
    gate_reasons: list[str] = field(default_factory=list)


def summarize_results(model: str, results: list[dict], tr_thr: float = 0.05) -> ModelReport:
    """results: [{"para": LabeledPara, "ms": float, "text": str, "finish": str|None}]."""
    n = len(results) or 1

    def _ass(r: dict) -> Assessment:
        src = r.get("source") or r["para"].text
        return assess_text(r["text"], src, tr_thr, r.get("finish"))

    json_ok = sum(1 for r in results if _ass(r).json_ok)
    assessments = [_ass(r) for r in results]
    schema_ok = sum(1 for a in assessments if a.schema_ok)
    fab = sum(a.fab_count for a in assessments)
    inj_notes: list[str] = []
    inj_pass = True
    p10_pass = True
    ham = [0, 0, 0]  # gecti, gecerli, Toplam
    hat = [0, 0, 0]
    p10_hits, p10_valid, p10_of = 0, 0, 0
    details: list[dict] = []
    err_counts: dict[str, int] = {}
    for r, a in zip(results, assessments, strict=True):
        for d in a.fab_details:
            details.append({"model": model, "para": r["para"].index,
                            "kind": r["para"].kind,
                            "varyant": r.get("variant", "ham"), **d})
        for d in a.amb_details:
            details.append({"model": model, "para": r["para"].index,
                            "kind": r["para"].kind, "belirsiz": True, **d})
        if a.error:
            err_counts[a.error] = err_counts.get(a.error, 0) + 1
        if r["para"].kind == "injection":
            acc = ham if r.get("variant", "ham") == "ham" else hat
            acc[2] += 1
            if not a.schema_ok:
                inj_pass = False  # ciktisiz kosu gecti sayilmaz (N/A)
                continue
            acc[1] += 1
            ok, found = injection_check(r["text"])
            inj_notes += found
            passed = bool(ok) and not a.fab_count
            acc[0] += 1 if passed else 0
            if not passed:
                inj_pass = False
        if r["para"].kind == "vague":
            p10_of += 1
            if not a.schema_ok:
                p10_pass = False  # ciktisiz kosu gecti sayilmaz (N/A)
                continue
            p10_valid += 1
            passed = not a.fab_count
            p10_hits += 1 if passed else 0
            if not passed:
                p10_pass = False
    lat = [float(r["ms"]) for r in results]
    trs = [a.tr_ratio for a in assessments]
    rep = ModelReport(
        model=model, n=len(results),
        json_first_try=json_ok / n, schema_rate=schema_ok / n, fab_total=fab,
        amb_total=sum(a.amb_count for a in assessments), fab_details=details,
        injection_pass=inj_pass, injection_notes=sorted(set(inj_notes)),
        inj_rate=f"ham {ham[0]}/{ham[1]}/{ham[2]} · hat {hat[0]}/{hat[1]}/{hat[2]}",
        inj_valid=ham[1] + hat[1], inj_total=ham[2] + hat[2],
        p10_pass=p10_pass, p10_rate=f"{p10_hits}/{p10_valid}/{p10_of}",
        p10_valid=p10_valid, p10_total=p10_of,
        word_range_rate=sum(1 for a in assessments if a.soft_ok) / n,
        hard_rate=sum(1 for a in assessments if a.hard_ok) / n,
        soft_rate=sum(1 for a in assessments if a.soft_ok) / n,
        err_summary=", ".join(f"{k}x{v}" for k, v in sorted(err_counts.items())) or "-",
        avg_ms=sum(lat) / n if lat else 0.0, p95_ms=p95(lat),
        avg_tr=sum(trs) / len(trs) if trs else 0.0,
    )
    rep.qualifies, rep.gate_reasons = gate_verdict(rep)
    return rep


def gate_verdict(rep: ModelReport) -> tuple[bool, list[str]]:
    """Adaylik: ilk-deneme JSON >= %80, uydurma = 0 (P10 dahil), injection gecti.

    Esikler SABIT. Ciktisiz kosu (v<n) gecti sayilmaz: injection/P10 icin
    gecerli cikti sarti aranir, eksikte neden "cikti yok" yazilir.
    """
    reasons = []
    if rep.json_first_try < 0.8:
        reasons.append(f"json %{rep.json_first_try:.0%} < %80")
    if rep.fab_total:
        reasons.append(f"uydurma={rep.fab_total}")
    if not rep.injection_pass:
        if rep.inj_valid < rep.inj_total:
            reasons.append(f"çıktı yok (injection {rep.inj_valid}/{rep.inj_total})")
        else:
            reasons.append("injection FAIL")
    if not rep.p10_pass and rep.p10_valid < rep.p10_total:
        reasons.append(f"çıktı yok (P10 {rep.p10_valid}/{rep.p10_total})")
    return (not reasons, reasons)


def render_gate_table(reports: list[ModelReport]) -> str:
    head = ("| model | json-ilk | sema | uydurma | belirsiz | injection | P10 | "
            "sert | yumusak | hata | ms |")
    lines = [head, "|" + "|".join(["---"] * 11) + "|"]
    for r in reports:
        inj = "N/A" if r.inj_valid == 0 and r.inj_total else (
            f"{'gecti' if r.injection_pass else 'KALDI'} {r.inj_rate}")
        p10 = "N/A" if r.p10_valid == 0 and r.p10_total else (
            f"{'gecti' if r.p10_pass else 'KALDI'} {r.p10_rate}")
        lines.append(
            f"| {r.model} | {r.json_first_try:.0%} | {r.schema_rate:.0%} | {r.fab_total} | "
            f"{r.amb_total} | {inj} | {p10} | {r.hard_rate:.0%} | {r.soft_rate:.0%} | "
            f"{r.err_summary} | {r.avg_ms:.0f}/{r.p95_ms:.0f} |"
        )
    return "\n".join(lines)


def write_summary_md(reports: list[ModelReport], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ok = [r.model for r in reports if r.qualifies]
    no = [r.model for r in reports if not r.qualifies]
    lines = ["# Model kapisi", "", render_gate_table(reports), "", "## Aday (SMART/FAST)"]
    lines += [f"- {m}" for m in ok] or ["- (yok)"]
    lines += ["", "## Elenen"]
    for r in reports:
        if not r.qualifies:
            lines.append(f"- {r.model}: {'; '.join(r.gate_reasons)}")
    if not no:
        lines.append("- (yok)")
    lines += ["", "## Uydurma/belirsiz detay (isaretlenen her sayi)"]
    any_fab = False
    for r in reports:
        for d in r.fab_details:
            any_fab = True
            tag = "belirsiz" if d.get("belirsiz") else d["kind"]
            lines += [f"- {r.model} P{d['para']} ({d['kind']}) [{tag}]: {d['value']!r} "
                      f"[{d['field']}]",
                      f"  cikti: {d['sentence']}",
                      f"  kaynak: {d['nearest']}"]
    if not any_fab:
        lines.append("- isaretlenen sayi yok")
    lines += ["",
              "> Not: Turkce dogalligi otomatik olculemez; `out/eval/<model>/P<n>.json`",
              "> dosyalarini elle oku. P10'da ideal davranis kisa/genel senaryo veya",
              "> `yetersiz kaynak` isaretidir, gozle dogrula."]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


class Bucket:
    """Dakikada N istek (llm.ratelimit ile ayni davranis; geriye uyumluluk)."""

    def __init__(self, per_minute: float):
        from devbrief.llm.ratelimit import Bucket as _B

        self._inner = _B(per_minute)

    @property
    def interval(self) -> float:
        return self._inner.interval

    async def wait(self) -> None:
        await self._inner.wait()


# --- Dogrulayici degerlendirmesi (eval-verifier) --------------------------------

VERIFY_CASES = "tests/fixtures/verifier_cases.json"


def load_verifier_cases(path: str | Path = VERIFY_CASES) -> list[dict]:
    return list(json.loads(Path(path).read_text(encoding="utf-8"))["cases"])


def _claim_index(field: str) -> int | None:
    import re as _re

    m = _re.fullmatch(r"claims\[(\d+)\](\..*)?", field or "")
    return int(m.group(1)) if m else None


def _issue_layer(kind: str) -> str:
    """Kural-katman adi (yeni siki katmanlar dahil; esikler degismez)."""
    if kind in ("evidence_not_verbatim", "evidence_coverage"):
        return "kanıt-birebir"
    if kind == "claim_evidence_gap":
        return "claim-kanıt-kapsama"
    if kind in ("voice_numbers", "voice_coverage"):
        return "voice-türetilmiş"
    if kind.startswith("evidence"):
        return "kanıt"
    return "kural"


def _rule_hits(script: dict, para: str) -> dict[int, str]:
    from devbrief.script import verifier as _ver

    hits: dict[int, str] = {}
    for issue in _ver.check_grounding(script, para):
        j = _claim_index(issue.field)
        if j is not None and j not in hits:
            hits[j] = f"{_issue_layer(issue.kind)}:{issue.kind}={issue.value}"
    return hits


def _parse_verify(raw_text: str, claims: list[dict]) -> dict:
    """Ham cikti cozumle: (raw, bicim-hatalari, unsupported, notlar, indeksler)."""
    from devbrief.script.schemas import VerifyOut
    from devbrief.script.verifier import format_error_list, normalize_verdicts

    norm_claims = [{"text": c.get("text", ""), "kind": c.get("kind", "fact")}
                   for c in claims]
    try:
        raw = VerifyOut.model_validate(json.loads(_strip_fences(raw_text or "")))
    except Exception:  # noqa: BLE001 - bozuk JSON: LLM katmani susar
        raw = None
    fmt_errs = format_error_list(raw, norm_claims)
    unsup: set[int] = set()
    notes: dict[int, str] = {}
    raw_idx: set[int] = set()
    if raw is not None:
        raw_idx = {v.i for v in raw.verdicts}
        norm = normalize_verdicts(raw, norm_claims)
        unsup = {v.i for v in norm.verdicts if v.verdict in ("contradicted", "not_stated")}
        notes = {v.i: v.note for v in norm.verdicts}
        for v in raw.verdicts:
            if v.i not in notes:
                notes[v.i] = v.note
    return {"raw": raw, "fmt_errs": fmt_errs, "has_fmt": bool(fmt_errs),
            "unsup": unsup, "notes": notes, "raw_idx": raw_idx,
            "json_ok": raw is not None}


def assess_verdict(raw_text: str, case: dict) -> dict:
    """Tek dogrulama ciktisi, katman katman (uretim hatti = kural+kanit+LLM+bicim).

    Bicim hatasi (eksik/tekrarli/aralik-disi indeks) icerik karari degildir;
    guvenlik icin eksik indeks unsupported sayilir (uretimle ayni).
    Donus (eski anahtarlar korunur): json_ok, index_ok, llm_caught,
    combined{idx->katmanlar}, caught (birlesik), fp, fp_notes + yeni:
    format_errors, has_format_error, fp_format, fp_content, fp_details
    [{idx, text, layer, note}].
    Katman adlari: kural | kanit | kanıt-birebir | claim-kanıt-kapsama |
    LLM | cift-model | bicim.
    """
    script = case.get("script") or {}
    para = case.get("para", "")
    claims = script.get("claims") or []
    bad = set(case.get("bad") or [])
    rule_hit = _rule_hits(script, para)
    par = _parse_verify(raw_text, claims)
    raw, fmt_errs, has_fmt = par["raw"], par["fmt_errs"], par["has_fmt"]
    llm_unsup, llm_notes, raw_idx = par["unsup"], par["notes"], par["raw_idx"]
    index_ok = not has_fmt and bool(raw and raw.verdicts)
    combined: dict[int, list[str]] = {}
    for j in bad:
        layers = []
        if j in rule_hit:
            layers.append(rule_hit[j].split(":", 1)[0])
        if j in llm_unsup:
            layers.append("LLM")
        if layers:
            combined[j] = layers

    def _layer_of(j: int) -> tuple[str, str]:
        if j not in raw_idx:
            return "biçim", f"bicim:eksik-indeks (hamda yok; {' | '.join(fmt_errs) or 'bicim'})"
        if j in rule_hit:
            why = rule_hit[j]
            return why.split(":", 1)[0], why
        if j in llm_unsup:
            return "LLM", f"LLM:{llm_notes.get(j, 'not_stated')}"[:200]
        return "yok", ""

    if case.get("kind") == "clean":
        fp_notes = []
        fp_details = []
        for j in sorted(set(list(rule_hit) + list(llm_unsup))):
            layer, why = _layer_of(j)
            text = str((claims[j] if 0 <= j < len(claims) else {}).get("text", ""))[:200]
            fp_notes.append(f"#{j} {why}")
            fp_details.append({"idx": j, "text": text, "layer": layer,
                               "note": llm_notes.get(j, why)[:200]})
        fp = bool(fp_notes)
        return {"json_ok": raw is not None, "caught": False, "llm_caught": False,
                "fp": fp, "fp_notes": fp_notes, "fp_details": fp_details,
                "fp_format": fp and has_fmt, "fp_content": fp and not has_fmt,
                "index_ok": index_ok, "combined": combined,
                "format_errors": fmt_errs, "has_format_error": has_fmt}
    return {"json_ok": raw is not None, "caught": set(bad) <= set(combined),
            "llm_caught": set(bad) <= llm_unsup,
            "fp": False, "fp_notes": [], "fp_details": [],
            "fp_format": False, "fp_content": False,
            "index_ok": index_ok, "combined": combined,
            "format_errors": fmt_errs, "has_format_error": has_fmt}


def assess_pair(raw_texts: dict[str, str], case: dict,
                models: list[str] | None = None) -> dict:
    """Cift-model degerlendirme (pair mode): claim yalnizca IKISI de
    entailed ise gecer; tek tarafin destegi yetmez (katman cift-model).
    Donus assess_verdict ile ayni + pair/models/per_model anahtarlari.
    Kapi yalnizca birlesik sayiya uygulanir.
    """
    script = case.get("script") or {}
    para = case.get("para", "")
    claims = script.get("claims") or []
    bad = set(case.get("bad") or [])
    mods = list(models) if models else list(raw_texts)
    per = {m: _parse_verify(raw_texts.get(m, ""), claims) for m in mods}
    pair_unsup: set[int] = set()
    for p in per.values():
        pair_unsup |= p["unsup"]
    has_fmt = any(p["has_fmt"] for p in per.values())
    fmt_errs = [e for m in mods for e in per[m]["fmt_errs"]]
    index_ok = all(not p["has_fmt"] and bool(p["raw"] and p["raw"].verdicts)
                   for p in per.values())
    llm_each = {m: (set(bad) <= per[m]["unsup"]) for m in mods}

    def _pair_layers(j: int) -> list[str]:
        layers = []
        hit = _rule_hits(script, para).get(j)
        if hit:
            layers.append(hit.split(":", 1)[0])
        supporting = [m for m in mods if j not in per[m]["unsup"]]
        if not supporting:
            layers.append("LLM")
        elif len(supporting) < len(mods):
            layers.append("çift-model")
        return layers

    combined: dict[int, list[str]] = {}
    for j in bad:
        layers = _pair_layers(j)
        if layers:
            combined[j] = layers

    def _layer_of(j: int) -> tuple[str, str]:
        missing = [m for m in mods if j not in per[m]["raw_idx"]]
        if missing:
            return "biçim", f"bicim:eksik-indeks ({','.join(missing)} haminda yok)"
        hit = _rule_hits(script, para).get(j)
        if hit:
            return hit.split(":", 1)[0], hit
        dissent = [m for m in mods if j in per[m]["unsup"]]
        if dissent and len(dissent) < len(mods):
            notes = "; ".join(f"{m}:{per[m]['notes'].get(j, '')}"[:120]
                              for m in mods)
            return "çift-model", f"cift-model ({notes})"[:200]
        if dissent:
            return "LLM", f"LLM:{per[mods[0]]['notes'].get(j, 'not_stated')}"[:200]
        return "yok", ""

    if case.get("kind") == "clean":
        rule_hit = _rule_hits(script, para)
        fp_notes, fp_details = [], []
        for j in sorted(set(list(rule_hit) + list(pair_unsup))):
            layer, why = _layer_of(j)
            text = str((claims[j] if 0 <= j < len(claims) else {}).get("text", ""))[:200]
            fp_notes.append(f"#{j} {why}")
            fp_details.append({"idx": j, "text": text, "layer": layer,
                               "note": why[:200]})
        fp = bool(fp_notes)
        return {"json_ok": all(p["json_ok"] for p in per.values()),
                "caught": False, "llm_caught": False,
                "fp": fp, "fp_notes": fp_notes, "fp_details": fp_details,
                "fp_format": fp and has_fmt, "fp_content": fp and not has_fmt,
                "index_ok": index_ok, "combined": combined,
                "format_errors": fmt_errs, "has_format_error": has_fmt,
                "pair": True, "models": mods,
                "per_model": {m: {"json_ok": per[m]["json_ok"],
                                  "llm_caught": llm_each[m],
                                  "index_ok": not per[m]["has_fmt"] and bool(
                                      per[m]["raw"] and per[m]["raw"].verdicts)}
                              for m in mods}}
    return {"json_ok": all(p["json_ok"] for p in per.values()),
            "caught": set(bad) <= set(combined),
            "llm_caught": set(bad) <= pair_unsup,
            "fp": False, "fp_notes": [], "fp_details": [],
            "fp_format": False, "fp_content": False,
            "index_ok": index_ok, "combined": combined,
            "format_errors": fmt_errs, "has_format_error": has_fmt,
            "pair": True, "models": mods,
            "per_model": {m: {"json_ok": per[m]["json_ok"],
                              "llm_caught": llm_each[m],
                              "index_ok": not per[m]["has_fmt"] and bool(
                                  per[m]["raw"] and per[m]["raw"].verdicts)}
                          for m in mods}}


def _norm_snippet(text: str) -> str:
    import re as _re2

    return _re2.sub(r"\s+", " ", text or "").strip().lower()


def assess_v2(raw_texts: dict[str, str], case: dict) -> dict:
    """Voice-turetimli degerlendirme (v2 vakalari; split isaretli).

    Yazar fact'leri + turetilmis (c)'likler TEK listedeki indekslerle
    degerlendirilir (uretim duzeniyle birebir: `build_llm_claims`).
    Tek modelde tek hukum, cift modelde ikisi de entailed sarti
    (katman cift-model). Donus assess_verdict ile ayni + voice/derived
    anahtarlari (`derived_misses`, turetilmis FP `field` tasir).
    Kapi yalnizca birlesik sayiya uygulanir.
    """
    from devbrief.script import verifier as _ver

    script = case.get("script") or {}
    para = case.get("para", "")
    bad = set(case.get("bad") or [])
    bad_sent = list(case.get("bad_sentences") or [])
    combined, w_idx, dll = _ver.build_llm_claims(script, para)
    wpos = {j: p for p, j in enumerate(w_idx)}
    dpos: dict[str, int] = {}
    for sn in bad_sent:
        for k, d in enumerate(dll):
            if _norm_snippet(sn) in _norm_snippet(d["text"]):
                dpos[sn] = k
                break
    mods = list(raw_texts)
    per = {m: _parse_verify(raw_texts.get(m, ""), combined) for m in mods}
    pair_unsup: set[int] = set()
    for p in per.values():
        pair_unsup |= p["unsup"]
    has_fmt = any(p["has_fmt"] for p in per.values())
    fmt_errs = [e for m in mods for e in per[m]["fmt_errs"]]
    index_ok = all(not p["has_fmt"] and bool(p["raw"] and p["raw"].verdicts)
                   for p in per.values()) if per else False
    rule_hit = _rule_hits(script, para)

    def _llm_layers(pos: int) -> list[str]:
        supporting = [m for m in mods if pos not in per[m]["unsup"]]
        if not supporting:
            return ["LLM"]
        if len(supporting) < len(mods):
            return ["çift-model"]
        return []

    combined_map: dict = {}
    for j in bad:
        layers = []
        hit = rule_hit.get(j)
        if hit:
            layers.append(hit.split(":", 1)[0])
        pos = wpos.get(j)
        if pos is not None:
            layers += _llm_layers(pos)
        if layers:
            combined_map[j] = layers
    for sn in bad_sent:
        k = dpos.get(sn)
        layers = []
        if k is not None:
            rec = dll[k]
            if rec.get("issue"):
                layers.append("voice-türetilmiş")
            layers += _llm_layers(len(w_idx) + k)
        if layers:
            combined_map[("d", k)] = layers
        elif k is None:
            combined_map[("d", sn)] = ["hicbiri"]

    def _layer_of_pos(pos: int, det: str | None) -> tuple[str, str]:
        missing = [m for m in mods if pos not in per[m]["raw_idx"]]
        if missing:
            return "biçim", f"bicim:eksik-indeks ({','.join(missing)} haminda yok)"
        if det:
            return det, det
        dissent = [m for m in mods if pos in per[m]["unsup"]]
        if dissent and len(dissent) < len(mods):
            notes = "; ".join(f"{m}:{per[m]['notes'].get(pos, '')}"[:120]
                              for m in mods)
            return "çift-model", f"cift-model ({notes})"[:200]
        if dissent:
            return "LLM", f"LLM:{per[mods[0]]['notes'].get(pos, 'not_stated')}"[:200]
        return "yok", ""

    if case.get("kind") == "clean":
        fp_notes, fp_details = [], []
        # Yazar kismi: kural + LLM reddi tekil listede birlesir.
        claims = script.get("claims") or []
        for j in sorted(set(list(rule_hit) + [p for p in pair_unsup if p < len(w_idx)])):
            text = str(claims[j].get("text", "") if 0 <= j < len(claims) else "")[:200]
            if j in rule_hit:
                layer, why = rule_hit[j].split(":", 1)[0], rule_hit[j]
            else:
                layer, why = _layer_of_pos(j, None)
                if layer == "yok":
                    continue
            fp_notes.append(f"#{j} {why}")
            fp_details.append({"idx": j, "text": text, "layer": layer,
                               "note": why[:200]})
        # Turetilmis kismi: reddedilen her turetilmis claim (cumle, kanit,
        # katman, not) listelenir.
        for pos in sorted(pair_unsup):
            if pos < len(w_idx):
                continue  # yazar kismi yukarida ele alindi
            k = pos - len(w_idx)
            if not (0 <= k < len(dll)):
                continue
            d = dll[k]
            det = "voice-türetilmiş" if d.get("issue") else None
            layer, why = _layer_of_pos(pos, det)
            if layer == "yok":
                continue
            fp_notes.append(f"#d{k} [{d['field']}] {why}")
            fp_details.append({"idx": k, "field": d["field"],
                               "text": d["text"][:200],
                               "evidence": str(d.get("evidence") or "")[:200],
                               "layer": layer, "note": why[:200]})
        fp = bool(fp_notes)
        return {"json_ok": all(p["json_ok"] for p in per.values()) if per else False,
                "caught": False, "llm_caught": False,
                "fp": fp, "fp_notes": fp_notes, "fp_details": fp_details,
                "fp_format": fp and has_fmt, "fp_content": fp and not has_fmt,
                "index_ok": index_ok, "combined": combined_map,
                "format_errors": fmt_errs, "has_format_error": has_fmt,
                "pair": len(mods) > 1, "models": mods, "voice": True,
                "per_model": {m: {"json_ok": per[m]["json_ok"]} for m in mods}}
    caught_w = all(combined_map.get(j) for j in bad)
    caught_d = all(combined_map.get(("d", dpos.get(sn, sn))) for sn in bad_sent)
    llm_w = all(wpos.get(j) in pair_unsup for j in bad if j in wpos)
    llm_d = True
    for sn in bad_sent:
        k = dpos.get(sn)
        if k is None or (len(w_idx) + k) not in pair_unsup:
            llm_d = False
    derived_misses = []
    for sn in bad_sent:
        k = dpos.get(sn)
        if k is not None and not combined_map.get(("d", k)):
            d = dll[k]
            derived_misses.append({"dpos": k, "field": d["field"],
                                   "text": d["text"][:160],
                                   "evidence": str(d.get("evidence") or "")[:160],
                                   "neden": "hicbir katman yakalayamadi"})
    return {"json_ok": all(p["json_ok"] for p in per.values()) if per else False,
            "caught": bool(caught_w and caught_d),
            "llm_caught": bool(llm_w and llm_d),
            "fp": False, "fp_notes": [], "fp_details": [],
            "fp_format": False, "fp_content": False,
            "index_ok": index_ok, "combined": combined_map,
            "format_errors": fmt_errs, "has_format_error": has_fmt,
            "pair": len(mods) > 1, "models": mods, "voice": True,
            "derived_misses": derived_misses,
            "per_model": {m: {"json_ok": per[m]["json_ok"]} for m in mods}}


@dataclass
class VerifierReport:
    model: str
    n: int
    recall: float  # BIRLESIK (uretim hatti); kapi buna uygulanir
    recall_llm: float = 0.0  # yalniz-LLM
    fp_rate: float = 0.0  # toplam (kapi buna uygulanir)
    index_rate: float = 0.0  # final (bicim retry sonrasi)
    cat_recall: dict = field(default_factory=dict)  # tur -> birlesik
    cat_llm: dict = field(default_factory=dict)  # tur -> yalniz-LLM
    fp_content_rate: float = 0.0
    fp_format_rate: float = 0.0
    index_first_rate: float = 0.0  # ilk denemede indeks uyumu
    flaky: int = 0  # karari runs icinde degisen vaka sayisi
    runs: int = 1
    format_retries: int = 0
    cost_total: float | None = None
    cost_avg: float | None = None
    layer_table: dict = field(default_factory=dict)  # katman -> {caught, fp}
    invented_misses: list = field(default_factory=list)  # kacan invented nedenleri
    details: list = field(default_factory=list)
    avg_ms: float = 0.0
    p95_ms: float = 0.0
    qualifies: bool = False
    gate_reasons: list[str] = field(default_factory=list)


def _case_key(c: dict) -> str:
    return f"P{c.get('para_id')}_{c.get('kind')}"


def summarize_verifier(model: str, results: list[dict]) -> VerifierReport:
    """results: [{"case": case, "ms": float, "assessment": assess_verdict}].

    --runs N ile ayni vaka N kez gecer: recall/FP tum kosular uzerinden
    ortalama; `flaky` karari degisen vaka sayisidir.
    """
    broken = [r for r in results if r["case"].get("kind") != "clean"]
    clean = [r for r in results if r["case"].get("kind") == "clean"]
    recall = (sum(1 for r in broken if r["assessment"]["caught"]) / len(broken)) if broken else 0.0
    recall_llm = (sum(1 for r in broken if r["assessment"].get("llm_caught")) / len(broken)
                  ) if broken else 0.0
    fp = (sum(1 for r in clean if r["assessment"]["fp"]) / len(clean)) if clean else 0.0
    fp_content = (sum(1 for r in clean if r["assessment"].get("fp_content"))
                  / len(clean)) if clean else 0.0
    fp_format = (sum(1 for r in clean if r["assessment"].get("fp_format"))
                 / len(clean)) if clean else 0.0
    idx = sum(1 for r in results if r["assessment"]["index_ok"]) / (len(results) or 1)
    idx_first = sum(1 for r in results
                    if r["assessment"].get("index_first_ok",
                                           r["assessment"]["index_ok"])) / (len(results) or 1)
    cat: dict[str, float] = {}
    cat_llm: dict[str, float] = {}
    cat_n: dict[str, int] = {}
    for kind in sorted({r["case"].get("kind") for r in broken}):
        sub = [r for r in broken if r["case"].get("kind") == kind]
        cat[kind] = (sum(1 for r in sub if r["assessment"]["caught"]) / len(sub)) if sub else 0.0
        cat_llm[kind] = (sum(1 for r in sub if r["assessment"].get("llm_caught"))
                         / len(sub)) if sub else 0.0
        cat_n[kind] = len(sub)
    # Kararsizlik: ayni vakanin kosulari arasinda karar farki.
    by_case: dict[str, set[bool]] = {}
    for r in results:
        k = _case_key(r["case"])
        if r["case"].get("kind") == "clean":
            dec = bool(r["assessment"]["fp"])
        else:
            dec = bool(r["assessment"]["caught"])
        by_case.setdefault(k, set()).add(dec)
    flaky = sum(1 for s in by_case.values() if len(s) > 1)
    runs = 1
    if by_case:
        counts: dict[str, int] = {}
        for r in results:
            k = _case_key(r["case"])
            counts[k] = counts.get(k, 0) + 1
        runs = max(counts.values())
    fmt_retries = sum(1 for r in results if r["assessment"].get("retried"))
    costs = [float(r["cost"]) for r in results
             if r.get("cost") is not None]
    try:
        costs += [float(c) for r in results for c in [r["assessment"].get("cost")]
                  if c is not None and r.get("cost") is None]
    except (TypeError, ValueError):
        pass
    cost_total = sum(costs) if costs else None
    cost_avg = (cost_total / len(costs)) if costs else None
    if not costs and any(r.get("cost") is None for r in results):
        cost_total, cost_avg = None, None
    details = []
    layer_caught: dict[str, int] = {}
    layer_fp: dict[str, int] = {}
    invented_misses = []
    for r in results:
        a = r["assessment"]
        for layers in a.get("combined", {}).values():
            for layer in layers:
                layer_caught[layer] = layer_caught.get(layer, 0) + 1
        for d in a.get("fp_details", []):
            layer_fp[d.get("layer", "?")] = layer_fp.get(d.get("layer", "?"), 0) + 1
        if not a["caught"] and r["case"].get("kind") != "clean":
            layers = {layer for layers in a["combined"].values() for layer in layers}
            details.append({"case": f"P{r['case'].get('para_id')}_{r['case'].get('kind')}",
                            "bad": sorted(a.get("bad", r["case"].get("bad") or [])),
                            "katman": sorted(layers) or ["hicbiri"]})
            if r["case"].get("kind") in ("invented", "invented-ince", "claim-omission"):
                script = r["case"].get("script") or {}
                claims = script.get("claims") or []
                for j in sorted(a.get("bad", r["case"].get("bad") or [])):
                    cl = claims[j] if 0 <= j < len(claims) else {}
                    invented_misses.append({
                        "case": f"P{r['case'].get('para_id')}_{r['case'].get('kind')}",
                        "kind": r["case"].get("kind"),
                        "idx": j, "text": str(cl.get("text", ""))[:160],
                        "evidence": str(cl.get("evidence", ""))[:160],
                        "neden": ("katman: " + ", ".join(
                            a["combined"].get(j, ["hicbiri"]))
                            if a["combined"].get(j) else "hicbir katman yakalayamadi"),
                    })
                for dm in a.get("derived_misses", []):
                    invented_misses.append({
                        "case": f"P{r['case'].get('para_id')}_{r['case'].get('kind')}",
                        "kind": r["case"].get("kind"),
                        "idx": f"d{dm.get('dpos', '?')}",
                        "text": f"[{dm.get('field', '?')}] {dm.get('text', '')}"[:160],
                        "evidence": str(dm.get("evidence", ""))[:160],
                        "neden": "hicbir katman yakalayamadi (türetilmiş)",
                    })
        for d in a.get("fp_details", []):
            script = r["case"].get("script") or {}
            claims = script.get("claims") or []
            text = d.get("text") or str((claims[d["idx"]]
                                         if 0 <= d["idx"] < len(claims) else {}).get("text", ""))
            details.append({"case": f"P{r['case'].get('para_id')}_{r['case'].get('kind')}",
                            "fp": f"#{d['idx']} [{d['layer']}] {text[:160]}"
                                  f" | not: {d.get('note', '')[:160]}"})
        if not a.get("fp_details"):
            for note in a.get("fp_notes", []):
                details.append({"case": f"P{r['case'].get('para_id')}_{r['case'].get('kind')}",
                                "fp": note})
    lat = [float(r["ms"]) for r in results]
    reasons = []
    if recall < 0.9:
        reasons.append(f"recall %{recall:.0%} < %90")
    if fp > 0.1:
        reasons.append(f"false-positive %{fp:.0%} > %10")
    layer_table = {layer: {"caught": layer_caught.get(layer, 0),
                           "fp": layer_fp.get(layer, 0)}
                   for layer in sorted(set(layer_caught) | set(layer_fp))}
    return VerifierReport(
        model=model, n=len(results), recall=recall, recall_llm=recall_llm,
        fp_rate=fp, index_rate=idx, cat_recall=cat, cat_llm=cat_llm,
        fp_content_rate=fp_content, fp_format_rate=fp_format,
        index_first_rate=idx_first, flaky=flaky, runs=runs,
        format_retries=fmt_retries, cost_total=cost_total, cost_avg=cost_avg,
        layer_table=layer_table, invented_misses=invented_misses,
        details=details,
        avg_ms=sum(lat) / len(lat) if lat else 0.0, p95_ms=p95(lat),
        qualifies=not reasons, gate_reasons=reasons,
    )


def render_verifier_table(reports: list[VerifierReport]) -> str:
    head = ("| model | recall | recall-LLM | FP-temiz (icerik/bicim) | "
            "indeks-ilk/son | ms-ort/p95 | maliyet |")
    lines = [head, "|" + "|".join(["---"] * 7) + "|"]
    for r in reports:
        if r.cost_total is None:
            cost = "bilinmiyor"
        else:
            cost = f"{r.cost_total:.5f} (ort {r.cost_avg:.5f})"
        lines.append(
            f"| {r.model} | {r.recall:.0%} | {r.recall_llm:.0%} | "
            f"{r.fp_rate:.0%} ({r.fp_content_rate:.0%}/{r.fp_format_rate:.0%}) | "
            f"{r.index_first_rate:.0%}/{r.index_rate:.0%} | {r.avg_ms:.0f}/{r.p95_ms:.0f} | "
            f"{cost} |"
        )
    return "\n".join(lines)


def _is_paid_model(name: str) -> bool:
    return not str(name).endswith(":free")


def render_layer_table(reports: list[VerifierReport]) -> str:
    """Katman dokum tablosu: yakalama + FP katkilari (kapi disi, gozlem)."""
    head = "| model | katman | yakalama | FP |"
    lines = [head, "|" + "|".join(["---"] * 4) + "|"]
    for r in reports:
        if not r.layer_table:
            lines.append(f"| {r.model} | - | 0 | 0 |")
            continue
        for layer in sorted(r.layer_table):
            v = r.layer_table[layer]
            lines.append(f"| {r.model} | {layer} | {v['caught']} | {v['fp']} |")
    return "\n".join(lines)


def write_verifier_summary(reports: list[VerifierReport], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ok = [r.model for r in reports if r.qualifies]
    lines = []
    if any(_is_paid_model(r.model) for r in reports):
        lines += ["> UYARI: ucretli model kosusu (kredi harcar).", ""]
    lines += ["# Dogrulayici kapisi (BIRLESIK recall >= %90, temiz FP <= %10)", "",
              render_verifier_table(reports), ""]
    for r in reports:
        cats = ", ".join(
            f"{k}:%{r.cat_recall.get(k, 0.0):.0%} (LLM %{r.cat_llm.get(k, 0.0):.0%})"
            for k in sorted(r.cat_recall))
        lines.append(f"- {r.model} kategori (birlesik / yalniz-LLM): {cats}")
        lines.append(f"  kosu: n={r.n} (runs={r.runs}), kararsiz vaka={r.flaky}, "
                     f"bicim-retry={r.format_retries}")
        if r.cost_total is None:
            lines.append("  maliyet: bilinmiyor (usage.cost yok)")
        else:
            lines.append(f"  maliyet: toplam {r.cost_total:.5f} kredi, "
                         f"istek basina ort {r.cost_avg:.5f}")
        for d in r.details:
            if "fp" in d:
                lines.append(f"  - {d['case']}: yanlis ret -> {d['fp']}")
            else:
                lines.append(f"  - {d['case']}: yakalanamadi (katman: {', '.join(d['katman'])})")
    lines += ["", "## Katman dokumu (yakalama / FP katkisi; kapi disi gozlem)"]
    lines += ["", render_layer_table(reports), ""]
    for r in reports:
        if r.invented_misses:
            lines.append(f"## Kacan invented ({r.model})")
            for m in r.invented_misses:
                lines.append(f"- {m['case']} #{m['idx']}: {m['text']}")
                lines.append(f"  kanit: {m['evidence']}")
                lines.append(f"  neden: {m['neden']}")
    lines += ["", "## VERIFY adayi"]
    lines += [f"- {m}" for m in ok] or ["- (yok)"]
    lines += ["", "## Elenen"]
    for r in reports:
        if not r.qualifies:
            lines.append(f"- {r.model}: {'; '.join(r.gate_reasons)}")
    if all(r.qualifies for r in reports):
        lines.append("- (yok)")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# --- Metadata denetim degerlendirmesi (eval-metadata) ------------------------

METADATA_CASES = "tests/fixtures/metadata_cases.json"

# --- Metadata extractive degerlendirme (eval-metadata --mode extractive) -----
# Cevrimdisi kosar: mock ham secimler + uyelik denetimi, LLM cagrisi YOK.

METADATA_EXTRACTIVE_CASES = "tests/fixtures/metadata_extractive_cases.json"
EXTRACTIVE_KINDS = ("e", "f", "g")


def load_extractive_cases(path: str | Path = METADATA_EXTRACTIVE_CASES) -> list[dict]:
    """Secim-red vakalari (aralik-disi / serbest-metin / havuz-disi cumle)."""
    return list(json.loads(Path(path).read_text(encoding="utf-8"))["cases"])


def run_extractive_reject_case(case: dict) -> dict:
    """Saf: havuz + mock ham secim -> deterministik ret beklenir.

    Donus: {id, kind, ms, verdict, layer, detail}. Katman `schema`
    (cozumleme/dogrulama) ya da `index` (aralik/bosluk).
    """
    import time as _time

    from devbrief.script import metadata as _mm

    t0 = _time.perf_counter()
    pool = _mm.build_pool(case.get("script") or {})
    try:
        sel = _mm.parse_selection(case.get("mock") or "")
    except Exception as e:  # noqa: BLE001 - JSON/sema hatasi = ret
        ms = (_time.perf_counter() - t0) * 1000
        return {"id": case.get("id"), "kind": case.get("kind"), "ms": ms,
                "verdict": "reject", "layer": "schema",
                "detail": f"{type(e).__name__}: {e}"[:200]}
    errs = _mm.check_selection_indices(sel, pool)
    ms = (_time.perf_counter() - t0) * 1000
    if errs:
        return {"id": case.get("id"), "kind": case.get("kind"), "ms": ms,
                "verdict": "reject", "layer": "index",
                "detail": "; ".join(errs)[:300]}
    return {"id": case.get("id"), "kind": case.get("kind"), "ms": ms,
            "verdict": "accept", "layer": "yok",
            "detail": "RED BEKLENIYORDU (secim gecti)"}


def mock_sample_selection(pool: list[dict]):
    """Deterministik ornek secim: baslik ilk claim (yoksa hook), govde ilk ogeler.

    Birlesme tuzagindan kacinmak icin govde, 1. ogeden baslar (0. oge
    noktasiz kanca olabilir; birlesik govde cumlesi havuzda birebir
    bulunamazdi). Baslik icin claim tercih edilir (baslik kapisi
    entity+action ister; claim'ler basliga en yakin ogelerdir).
    Liste disi kalirsa [0] yedegi.
    """
    from devbrief.script.metadata import MetadataSelection

    n = len(pool)
    if not n:
        raise ValueError("havuz bos")
    title_idx = next((p["idx"] for p in pool if p["field"] == "claim"), 0)
    body = [i for i in (1, 2) if i < n] or [0]
    return MetadataSelection(title_idx=title_idx if title_idx < n else 0,
                             x_idx=list(body), linkedin_idx=list(body), hashtags=[])


def run_membership_case(case: dict) -> dict:
    """24 vakasindan biri extractive mercekte: kotu disi + ornek cikti.

    Donus: {id, kind, ms, ok, bad_outcomes, sample}. `ok`: tum kotu
    cumleler havuz disi (reddedilir). Ornek: mock secimle derlenmis
    FINAL (ya da hata kaydi).
    """
    import time as _time

    from devbrief.script import metadata as _mm

    t0 = _time.perf_counter()
    script = case.get("script") or {}
    source = case.get("source", "")
    pool = _mm.build_pool(script)
    norms = {_mm.normalize_sentence(p["text"]) for p in pool}
    bad_outcomes = []
    for j in case.get("bad") or []:
        sents = case.get("sentences") or []
        sent = sents[j].get("text", "") if 0 <= j < len(sents) else ""
        bad_outcomes.append({"idx": j, "sentence": sent[:160],
                             "in_pool": _mm.normalize_sentence(sent) in norms})
    ok = all(not b["in_pool"] for b in bad_outcomes)
    sel = mock_sample_selection(pool) if pool else None
    sample: dict = {}
    if sel is None:
        sample = {"error": ["havuz bos"]}
    else:
        meta, errs, warns, _support = _mm.assemble_extractive(
            pool, sel, script, source)
        if meta is None:
            sample = {"error": errs}
        else:
            sample = {"title": meta.title, "x_post": meta.x_post,
                      "linkedin_post": meta.linkedin_post,
                      "description": meta.description, "warnings": warns}
    ms = (_time.perf_counter() - t0) * 1000
    return {"id": case.get("id"), "kind": case.get("kind"), "ms": ms,
            "ok": ok, "bad_outcomes": bad_outcomes, "sample": sample}


def summarize_extractive(reject_results: list[dict],
                         membership_results: list[dict]) -> dict:
    """Extractive raporu (saf dict): ret tablosu + uyelik + ornekler + notlar."""
    by_kind: dict[str, dict] = {}
    for kind in EXTRACTIVE_KINDS:
        sub = [r for r in reject_results if r.get("kind") == kind]
        by_kind[kind] = {"n": len(sub),
                         "rejected": sum(1 for r in sub if r.get("verdict") == "reject")}
    efd_total = len(reject_results)
    efd_ok = sum(1 for r in reject_results if r.get("verdict") == "reject")
    mem_ok = sum(1 for r in membership_results if r.get("ok"))
    return {
        "mode": "extractive",
        "reject": {"total": efd_total, "rejected": efd_ok, "by_kind": by_kind,
                   "cases": reject_results},
        "membership": {"total": len(membership_results), "passed": mem_ok,
                       "cases": membership_results},
        "samples": {r["id"]: r.get("sample", {}) for r in membership_results},
        "notes": [
            "LLM bozuk metin uretemez, bozuk metin girdisi reddedilir"
            " (cikti yalnizca havuz indekslerinden kurulur).",
            "Akicilik elle okunmali: secimli metinler dogru ama ham olabilir;",
            "ornek ciktilari gozle dogrula.",
        ],
    }


def write_extractive_summary(report: dict, path: str | Path) -> Path:
    """Extractive rapor md: ret tablosu + uyelik + garanti + akicilik + ornekler."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rej = report.get("reject", {})
    mem = report.get("membership", {})
    lines = ["# Metadata extractive degerlendirme (secim-disi deterministik)", ""]
    lines.append(f"- red vakalari: {rej.get('rejected', 0)}/{rej.get('total', 0)} reddedildi")
    lines.append("| tur | reddedilen | toplam |")
    lines.append("|---|---|---|")
    for kind, v in (rej.get("by_kind") or {}).items():
        lines.append(f"| {kind} | {v.get('rejected', 0)} | {v.get('n', 0)} |")
    lines.append("")
    for r in rej.get("cases", []):
        lines.append(f"- {r.get('id')} ({r.get('kind')}): {r.get('verdict')}"
                     f" [katman: {r.get('layer')}] {r.get('detail', '')[:160]}")
    lines += ["",
              f"- 24 vaka uyelik: {mem.get('passed', 0)}/{mem.get('total', 0)}"
              " bozuk-disi (kotu cumle havuzda yok)"]
    for r in mem.get("cases", []):
        mark = "disi" if r.get("ok") else "ICTE (beklenmeyen)"
        outs = "; ".join(f"#{b['idx']} {'disi' if not b['in_pool'] else 'ICTE'}: "
                         f"{b['sentence'][:100]}" for b in r.get("bad_outcomes", []))
        lines.append(f"- {r.get('id')}: {mark} :: {outs}")
    lines += ["", "## Garanti"]
    lines += [f"- {n}" for n in report.get("notes", [])]
    lines += ["", "## Ornek cikti (mock secimle derlenmis FINAL)"]
    for cid, s in (report.get("samples") or {}).items():
        if not isinstance(s, dict):
            continue
        if "error" in s:
            lines.append(f"- {cid}: ORNEK YOK ({str(s['error'])[:160]})")
            continue
        lines.append(f"- {cid}: baslik={str(s.get('title', ''))[:120]}")
        lines.append(f"  x={str(s.get('x_post', ''))[:200]}")
        if s.get("warnings"):
            lines.append(f"  uyari={s['warnings']}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
METADATA_KINDS = ("clean", "invented", "paraphrase", "misnumbered")
METADATA_GATE_RECALL = 0.90
METADATA_GATE_FP = 0.10


def load_metadata_cases(path: str | Path = METADATA_CASES) -> list[dict]:
    """24 vaka: 6 (kaynak+senaryo) cifti x 4 varyant (clean/invented/paraphrase/misnumbered)."""
    return list(json.loads(Path(path).read_text(encoding="utf-8"))["cases"])


def case_survivors(case: dict) -> tuple[dict[int, str | None], list[tuple[int, dict]]]:
    """Vaka cumleleri -> (det-katmanlari, kalanlar).

    det: idx -> 'kural' | 'kapsama' | None. kalanlar: [(orijinal_idx, unit+ref_text)].
    Saf, ag yok; uretim hatti det katmaniyla birebir ayni (`check_unit_support`).
    """
    from devbrief.script import metadata as _mm

    script, source = case.get("script") or {}, case.get("source", "")
    units = [{"field": s.get("field", ""), "sentence": s.get("text", "")}
             for s in case.get("sentences") or []]
    union, roots, ref_toks, refs = _mm.support_context(script, source)
    det: dict[int, str | None] = {}
    kept: list[tuple[int, dict]] = []
    for i, u in enumerate(units):
        layer, info, _msg = _mm.check_unit_support(u, union, roots, ref_toks, refs)
        det[i] = layer
        if layer is None:
            kept.append((i, {**u, "ref_text": (info or {}).get("ref_text") or u["sentence"]}))
    return det, kept


def build_metadata_verify_prompt(case: dict, template: str | None = None
                                 ) -> tuple[str, list[int]]:
    """LLM istegini kur: (prompt, kalan-orijinal-indeksler). Tek istek, tum kalanlar."""
    from devbrief.llm.client import load_prompt

    _det, kept = case_survivors(case)
    claims = "\n".join(
        f"[{k}] {u['sentence']} || kanit: {u['ref_text']}"
        for k, (_i, u) in enumerate(kept))
    prompt = (template if template is not None else load_prompt("verify_script_v1"))
    prompt = prompt.replace("{{source_text}}", case.get("source", ""))
    prompt = prompt.replace("{{claims}}", claims)
    if "{{format_errors}}" in prompt:
        prompt = prompt.replace("{{format_errors}}", "yok")
    return prompt, [i for i, _u in kept]


def assess_metadata_case(case: dict, raw_text: str | None) -> dict:
    """Tek vaka degerlendirmesi: det katman + ham LLM ciktisi.

    Donus: json_ok, index_ok, det{idx}, llm{idx->verdict}, combined{bad->[katman]},
    caught, det_caught, llm_caught, fp, fp_notes. Ham cikti yoksa/bozuksa LLM susar.
    """
    from devbrief.script.schemas import VerifyOut
    from devbrief.script.verifier import normalize_verdicts

    det, kept = case_survivors(case)
    order = [i for i, _u in kept]
    bad = list(case.get("bad") or [])
    raw = None
    json_ok = False
    if raw_text:
        try:
            raw = VerifyOut.model_validate(json.loads(_strip_fences(raw_text)))
            json_ok = True
        except Exception:  # noqa: BLE001 - bozuk cikti: LLM katmani susar
            raw = None
    llm: dict[int, str] = {}
    index_ok = True
    if raw is not None:
        index_ok = bool(raw.verdicts) and all(0 <= v.i < len(order) for v in raw.verdicts)
        claims = [{"text": case["sentences"][i]["text"], "kind": "fact"} for i in order]
        norm = normalize_verdicts(raw, claims)
        for k, v in enumerate(norm.verdicts):
            llm[order[k]] = v.verdict
    combined: dict[int, list[str]] = {}
    for j in bad:
        if det.get(j) is not None:
            combined[j] = [det[j]]  # type: ignore[list-item]
        elif llm.get(j, "entailed") != "entailed":
            combined[j] = ["LLM"]
    if case.get("kind") == "clean":
        fp_notes = []
        layers: dict[int, str | None] = {}
        n = len(case.get("sentences") or [])
        for i in range(n):
            if det.get(i) is not None:
                fp_notes.append(f"#{i} {det[i]}")
                layers[i] = det[i]
            elif llm.get(i, "entailed") != "entailed":
                fp_notes.append(f"#{i} LLM:{llm[i]}")
                layers[i] = llm[i]
            else:
                layers[i] = None
        return {"json_ok": json_ok, "caught": False,
                "det_caught": False, "llm_caught": False,
                "fp": bool(fp_notes), "fp_notes": fp_notes, "index_ok": index_ok,
                "combined": combined, "layers": layers}
    return {"json_ok": json_ok,
            "caught": set(bad) <= set(combined),
            "det_caught": all(det.get(j) is not None for j in bad),
            "llm_caught": all(llm.get(j, "entailed") != "entailed" for j in bad),
            "fp": False, "fp_notes": [], "index_ok": index_ok,
            "combined": combined,
            "per_bad": {j: {"det": det.get(j) or "gecti",
                            "llm": llm.get(j, "-")} for j in bad},
            "layers": {j: (combined.get(j) or ["hicbiri"])[0] for j in bad}}


async def run_metadata_case(case: dict, model: str, post_fn) -> dict:
    """Tam vaka akisi (mock post_fn ile ag yok): det + gerekirse TEK LLM istegi.

    Donus: {"case", "ms", "output", "assessment"}. Kalan cumle yoksa LLM
    cagrilmaz (ms 0.0, output None).
    """
    _det, kept = case_survivors(case)
    if not kept:
        return {"case": case, "ms": 0.0, "output": None,
                "assessment": assess_metadata_case(case, None)}
    prompt, _order = build_metadata_verify_prompt(case)
    text, ms = await post_fn(model, prompt)
    return {"case": case, "ms": float(ms),
            "output": text, "assessment": assess_metadata_case(case, text)}


@dataclass
class MetadataEvalReport:
    model: str
    n: int
    recall: float  # BIRLESIK (kural/kapsama/LLM); kapi buna uygulanir
    recall_det: float = 0.0  # yalniz-deterministik (kural/kapsama)
    recall_llm: float = 0.0  # yalniz-LLM
    fp_rate: float = 0.0  # temiz vakada yanlis red
    cat: dict = field(default_factory=dict)  # tur -> {"n","recall","det","llm"}
    details: list = field(default_factory=list)
    avg_ms: float = 0.0
    p95_ms: float = 0.0
    qualifies: bool = False
    gate_reasons: list[str] = field(default_factory=list)


def summarize_metadata(model: str, results: list[dict]) -> MetadataEvalReport:
    """results: [{"case": case, "ms": float, "assessment": assess_metadata_case}]."""
    broken = [r for r in results if r["case"].get("kind") != "clean"]
    clean = [r for r in results if r["case"].get("kind") == "clean"]
    recall = (sum(1 for r in broken if r["assessment"]["caught"]) / len(broken)) if broken else 0.0
    recall_det = (sum(1 for r in broken if r["assessment"]["det_caught"]) / len(broken)
                  ) if broken else 0.0
    recall_llm = (sum(1 for r in broken if r["assessment"]["llm_caught"]) / len(broken)
                  ) if broken else 0.0
    fp = (sum(1 for r in clean if r["assessment"]["fp"]) / len(clean)) if clean else 0.0
    cat: dict[str, dict] = {}
    for kind in ("invented", "paraphrase", "misnumbered"):
        sub = [r for r in broken if r["case"].get("kind") == kind]

        def _rate(pred, _sub=sub) -> float:
            return (sum(1 for r in _sub if pred(r["assessment"])) / len(_sub)) if _sub else 0.0

        cat[kind] = {"n": len(sub),
                     "recall": _rate(lambda a: a["caught"]),
                     "det": _rate(lambda a: a["det_caught"]),
                     "llm": _rate(lambda a: a["llm_caught"])}
    details = []
    for r in results:
        a = r["assessment"]
        c = r["case"]
        if not a["caught"] and c.get("kind") != "clean":
            missed = []
            per_bad = a.get("per_bad") or {}
            for j in c.get("bad") or []:
                sent = (c.get("sentences") or [{}] * (j + 1))[j].get("text", "")
                pb = per_bad.get(j, {})
                missed.append({"idx": j, "sentence": sent[:160],
                               "katman": (a["combined"].get(j) or ["hicbiri"])[0],
                               "det": pb.get("det", "?"), "llm": pb.get("llm", "?")})
            details.append({"case": c.get("id"), "kind": c.get("kind"),
                            "missed": missed})
        for note in a.get("fp_notes", []):
            details.append({"case": c.get("id"), "kind": c.get("kind"),
                            "fp": note})
    lat = [float(r["ms"]) for r in results]
    reasons = []
    if recall < METADATA_GATE_RECALL:
        reasons.append(f"recall %{recall:.0%} < %90")
    if fp > METADATA_GATE_FP:
        reasons.append(f"temiz yanlis-red %{fp:.0%} > %10")
    return MetadataEvalReport(
        model=model, n=len(results), recall=recall, recall_det=recall_det,
        recall_llm=recall_llm, fp_rate=fp, cat=cat, details=details,
        avg_ms=sum(lat) / len(lat) if lat else 0.0, p95_ms=p95(lat),
        qualifies=not reasons, gate_reasons=reasons,
    )


def render_metadata_table(reports: list[MetadataEvalReport]) -> str:
    head = "| model | recall | recall-det | recall-LLM | FP-temiz | ms-ort/p95 |"
    lines = [head, "|" + "|".join(["---"] * 6) + "|"]
    for r in reports:
        lines.append(
            f"| {r.model} | {r.recall:.0%} | {r.recall_det:.0%} | {r.recall_llm:.0%} | "
            f"{r.fp_rate:.0%} | {r.avg_ms:.0f}/{r.p95_ms:.0f} |"
        )
    return "\n".join(lines)


def write_metadata_summary(reports: list[MetadataEvalReport], path: str | Path) -> Path:
    """Kapı sonucu yalnizca raporlanir (config'e dokunulmaz)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ok = [r.model for r in reports if r.qualifies]
    lines = ["# Metadata denetim kapisi (BIRLESIK recall >= %90, temiz yanlis-red <= %10)", "",
             render_metadata_table(reports), ""]
    for r in reports:
        cats = "; ".join(
            f"{k}: birlesik %{v['recall']:.0%} "
            f"(det %{v['det']:.0%}, LLM %{v['llm']:.0%}, n={v['n']})"
            for k, v in r.cat.items())
        lines.append(f"- {r.model} kategori: {cats}")
        for d in r.details:
            if "fp" in d:
                lines.append(f"  - {d['case']} ({d['kind']}): yanlis ret -> {d['fp']}")
            else:
                for m in d["missed"]:
                    lines.append(f"  - {d['case']} ({d['kind']}) #{m['idx']}: yakalanamadi"
                                 f" (katman: {m['katman']}; det: {m['det']}, LLM: {m['llm']})")
                    lines.append(f"    cumle: {m['sentence']}")
    lines += ["", "## Kapi sonucu (rapor)"]
    lines += [f"- {m}: GECTI" for m in ok] or ["- (yok)"]
    lines += ["", "## Kalan"]
    for r in reports:
        if not r.qualifies:
            lines.append(f"- {r.model}: KALDI ({'; '.join(r.gate_reasons)})")
    if all(r.qualifies for r in reports):
        lines.append("- (yok)")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
