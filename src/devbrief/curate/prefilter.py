"""LLM'siz on puanlama (docs/02-ARCHITECTURE.md §6). Faz 2 kilidi DISINDA.

prescore = w_source*kaynak + w_pop*log(1+puan) + w_fresh*tazelik + w_kw*anahtar
Anahtar listeler kodda degil `config/prefilter_terms.yaml` icindedir.
Ham skor kaynaga gore siser (HN puani); bu yuzden secim iki asamalidir:
1) kaynak-ici normalizasyon (min-max, 0-1),
2) kaynak basina kota + toplam 40.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

DEFAULT_TERMS = "config/prefilter_terms.yaml"


@lru_cache(maxsize=4)
def load_terms(path: str = DEFAULT_TERMS) -> dict:
    import yaml

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    return {
        "positive": list(data.get("positive") or []),
        "negative": list(data.get("negative") or []),
        "sources": dict(data.get("sources") or {}),
    }


def _compile(patterns: list[str]) -> list:
    out = []
    for p in patterns:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue
    return out


_SEMVER = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_EXEMPT = re.compile(r"security|\bcve\b|vulnerab|breaking", re.IGNORECASE)

#: Yama surum cezasi (security/breaking icermeyen x.y.Z, Z>0).
PATCH_PENALTY = -1.5


def version_bump(title: str) -> str | None:
    """Basliktaki ilk semver'e gore major/minor/patch. Yoksa None."""
    m = _SEMVER.search(title)
    if not m:
        return None
    major, minor, patch = (int(g) for g in m.groups())
    if patch > 0:
        return "patch"
    if minor > 0:
        return "minor"
    return "major"


def version_term(title: str, summary: str = "") -> float:
    """Yama surum -1.5; security/CVE/breaking muaf; diger 0.0."""
    if version_bump(title) != "patch":
        return 0.0
    if _EXEMPT.search(f"{title}\n{summary}"):
        return 0.0
    return PATCH_PENALTY


def keyword_score(text: str, source: str = "", terms_path: str = DEFAULT_TERMS) -> int:
    """Pozitif +1, negatif -2. Kaynaga ozel ek listeler de uygulanir."""
    terms = load_terms(terms_path)
    pos = _compile(terms["positive"])
    neg = _compile(terms["negative"])
    extra = (terms["sources"].get(source) or {}) if isinstance(terms["sources"], dict) else {}
    pos += _compile(list(extra.get("positive") or []))
    neg += _compile(list(extra.get("negative") or []))
    hits_pos = sum(1 for rx in pos if rx.search(text))
    hits_neg = sum(1 for rx in neg if rx.search(text))
    return hits_pos - 2 * hits_neg


def freshness(age_hours: float | None) -> float:
    """0-1 arasi: 0h -> 1.0, ~48h -> ~0.37, yas bilinmiyorsa 0.5 (notr)."""
    if age_hours is None or age_hours < 0:
        return 0.5
    return math.exp(-age_hours / 48.0)


def age_hours(published_at: str | None, now: datetime | None = None) -> float | None:
    if not published_at:
        return None
    try:
        dt = datetime.fromisoformat(published_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = now or datetime.now(UTC)
        return max(0.0, (now - dt).total_seconds() / 3600.0)
    except ValueError:
        return None


def score(
    title: str,
    summary: str = "",
    *,
    source: str = "",
    source_weight: float = 0.5,
    hn_points: int = 0,
    published_at: str | None = None,
    w_source: float = 1.0,
    w_pop: float = 0.5,
    w_fresh: float = 1.0,
    w_kw: float = 1.0,
    terms_path: str = DEFAULT_TERMS,
) -> tuple[float, dict[str, float]]:
    text = f"{title}\n{summary}"
    c_source = w_source * source_weight
    c_pop = w_pop * math.log1p(max(0, hn_points))
    c_fresh = w_fresh * freshness(age_hours(published_at))
    c_kw = w_kw * keyword_score(text, source, terms_path)
    c_ver = version_term(title, summary)
    total = c_source + c_pop + c_fresh + c_kw + c_ver
    return total, {"source": c_source, "pop": c_pop, "fresh": c_fresh, "kw": c_kw, "ver": c_ver}


def normalize_within_source(scored: list[dict]) -> list[dict]:
    """Ham skoru kaynak-ici min-max ile 0-1'e ceker (`norm` alani ekler).

    Tek girdili kaynakta norm 1.0 olur; kaynaklar arasi karsilastirma norm
    uzerinden yapilir, ham skor DB'de saklanmaya devam eder.
    """
    out = [dict(s) for s in scored]
    by_source: dict[str, list[float]] = {}
    for s in out:
        by_source.setdefault(s["source"], []).append(float(s["raw"]))
    for s in out:
        vals = by_source[s["source"]]
        lo, hi = min(vals), max(vals)
        s["norm"] = 1.0 if hi <= lo else (float(s["raw"]) - lo) / (hi - lo)
    return out


def select(
    scored: list[dict], *, per_source_cap: int = 10, total: int = 40
) -> tuple[list[dict], dict[str, str]]:
    """Norm siraya gore sec: once kaynak kotasi, sonra toplam.

    Donus: (secilenler, elenme_sebebi {id: sebep}).
    Sebepler: "secildi" | "kaynak-kotasi" | "esik-alti".
    """
    ranked = sorted(scored, key=lambda s: s["norm"], reverse=True)
    picked: list[dict] = []
    reasons: dict[str, str] = {}
    per_source: dict[str, int] = {}
    for s in ranked:
        key = str(s["id"])
        if per_source.get(s["source"], 0) >= per_source_cap:
            reasons[key] = "kaynak-kotasi"
            continue
        if len(picked) >= total:
            reasons[key] = "esik-alti"
            continue
        picked.append(s)
        per_source[s["source"]] = per_source.get(s["source"], 0) + 1
        reasons[key] = "secildi"
    return picked, reasons
