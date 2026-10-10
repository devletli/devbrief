"""Baslik/aciklama/gonderi metinleri: write_metadata (varsayilan tier smart).

Prompt tier:fast der; kapi FAST adayi cikarmadigi icin smart kullanilir.
FAST adayi bulununca geri alinir.

Kayit oncesi kural denetimi (`check_metadata`, mevcut match_value/normalizasyon
ile — ikinci kod yolu yok): sayi/surum/tarih/ozel ad senaryoda veya kaynakta
olmalidir. Ret -> en fazla 2 yeniden uretim (somut hata + call_cap); olmadiysa
bolum `metadata_failed` olur, ilerlemez. Abarti ve AI beyanı uyarı duzeyindedir.
"""

from __future__ import annotations

import json as _json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from devbrief.llm.client import complete_json
from devbrief.publish.platforms import AI_NOTE

HYPE_PATH = "config/hype_terms.yaml"
MAX_TURNS = 2


class Metadata(BaseModel):
    title: str = Field(min_length=1, max_length=70)
    description: str = Field(min_length=1)
    hashtags: list[str] = Field(max_length=5)
    x_post: str = Field(min_length=1, max_length=270)
    linkedin_post: str = Field(min_length=1, max_length=600)


class MetadataFailed(RuntimeError):
    pass


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


def _strip_ai_note(text: str) -> str:
    """AI beyan cumlesi sabit metindir; dogrulama disi tutulur."""
    first = AI_NOTE.split(";")[0].strip()[:30]
    out = text.replace(AI_NOTE, "")
    if first:
        out = re.sub(re.escape(first) + r"[^.\n]*[.\n]?", "", out)
    return out


def _proper_nouns(text: str) -> list[str]:
    from devbrief.script.schemas import _MID_CAP_ALLOW as _ALLOW
    from devbrief.script.schemas import _PROPER_NOUN as _RX

    found = []
    for sent in re.split(r"(?<=[.!?…])\s+|\n+", text or ""):
        toks = sent.split()
        for tok in toks[1:]:
            word = tok.strip(".,;:!?()\"'")
            if (word and _RX.fullmatch(word) and not word.isupper()
                    and word not in _ALLOW and word not in found):
                found.append(word)
    return found


def _values(meta: Metadata) -> list[tuple[str, str]]:
    desc = _strip_ai_note(meta.description)
    return [("title", meta.title), ("description", desc), ("x_post", meta.x_post),
            ("linkedin_post", meta.linkedin_post),
            ("hashtags", " ".join(meta.hashtags))]


def check_metadata(meta: Metadata, script: dict, source_text: str) -> list[str]:
    """Sert ret listesi (bos = gecti). Senaryo metni + kaynak metni referans.

    - Her platform metninde kaynak linki (http) sart.
    - Sayi/surum/tarih/ozel ad: senaryoda VEYA kaynakta gecmeli (match_value).
    """
    from devbrief.script import verifier as _ver

    ref_texts = [source_text]
    for key in ("hook", "impact", "action"):
        ref_texts.append(str(script.get(key) or ""))
    for sc in script.get("scenes") or []:
        if isinstance(sc, dict):
            ref_texts.append(str(sc.get("voice") or ""))
    for c in script.get("claims") or []:
        if isinstance(c, dict):
            ref_texts.append(str(c.get("text") or ""))
    ref = "\n".join(ref_texts)
    errors: list[str] = []
    for field, text in _values(meta):
        if field in ("title", "description", "x_post", "linkedin_post") and "http" not in text:
            errors.append(f"{field}: kaynak linki yok")
    for field, text in _values(meta):
        for v in dict.fromkeys(_ver._VERSION.findall(text) + _ver._PERCENT.findall(text)):
            if _ver.match_value(v, ref) == "miss":
                errors.append(f"{field}: {v!r} senaryoda/kaynakta yok")
        for v in dict.fromkeys(_ver._NUMBER.findall(text)):
            if not _ver._VERSION.fullmatch(v or "") and _ver.match_value(v, ref) == "miss":
                errors.append(f"{field}: {v!r} senaryoda/kaynakta yok")
        for v in dict.fromkeys(_ver._DATE.findall(text)):
            if _ver.match_value(v, ref) == "miss":
                errors.append(f"{field}: {v!r} senaryoda/kaynakta yok")
        for w in _proper_nouns(text):
            if field != "hashtags" and w.lower() not in _ver._norm(ref):
                errors.append(f"{field}: ozel ad {w!r} senaryoda/kaynakta yok")
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


async def write_metadata(script: dict, conn=None, client=None,
                         rate_limit_per_min: float | None = None,
                         tier: str = "smart") -> Metadata:
    """Onayli senaryodan yayin metinleri. Kaynak linki sart (sema disi kalirsa ret)."""
    out = await complete_json(
        "write_metadata", "write_metadata_v1",
        {"script": _json.dumps(script, ensure_ascii=False)},
        Metadata, tier=tier, temperature=0.6,
        conn=conn, client=client, rate_limit_per_min=rate_limit_per_min,
    )
    if "http" not in out.description:
        raise ValueError("aciklamada kaynak linki yok")
    return out


async def produce(script: dict, source_text: str, conn=None, client=None,
                  rate_limit_per_min: float | None = None, tier: str = "smart",
                  cap=None, max_turns: int = MAX_TURNS) -> tuple[Metadata, list[str]]:
    """Denetimli uretim: ret -> somut hatayla en fazla `max_turns` yeniden deneme.

    Donus: (metadata, hype_uyarilari). Basarisizlikta MetadataFailed.
    Cagri sayaci `cap` uzerinden bolum kotasina islenir.
    """
    from devbrief.llm.client import CallCap, CallCapExceeded

    if cap is None:
        cap = CallCap(8)
    errors: list[str] = []
    for _ in range(max_turns + 1):
        if cap:
            cap.charge()
        try:
            meta = await write_metadata(script, conn=conn, client=client,
                                        rate_limit_per_min=rate_limit_per_min, tier=tier)
        except CallCapExceeded:
            raise
        except Exception as e:  # noqa: BLE001 - uretim hatasi da tur sayilir
            errors = [f"uretim: {type(e).__name__}: {e}"[:300]]
            continue
        problems = check_metadata(meta, script, source_text)
        if not problems:
            return meta, check_hype(meta, script, source_text)
        errors = problems
    raise MetadataFailed("metadata ret: " + "; ".join(errors[:6]))


def save_metadata(conn: sqlite3.Connection, episode_id: int, meta: Metadata,
                  warnings: list[str] | None = None) -> None:
    import devbrief.db as db

    with conn:
        conn.execute("UPDATE episodes SET title = ?, description = ?, metadata_json = ?,"
                     " metadata_warnings = ?, updated_at = ? WHERE id = ?",
                     (meta.title, meta.description, meta.model_dump_json(),
                      _json.dumps(warnings or [], ensure_ascii=False),
                      db.now_iso(), episode_id))


def fail_metadata(conn: sqlite3.Connection, episode_id: int, reason: str) -> None:
    import devbrief.db as db

    with conn:
        conn.execute("UPDATE episodes SET status = 'metadata_failed', reject_reason = ?,"
                     " updated_at = ? WHERE id = ?",
                     (reason[:2000], db.now_iso(), episode_id))
