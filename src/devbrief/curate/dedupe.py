"""Baslik benzerligine dayali kumeleme (LLM'siz). Faz 2 kilidi DISINDA.

Ayni canonical_url her zaman ayni kumededir (URL dedupe DB'de UNIQUE).
Buna ek olarak basliklar karsilastirilir: Jaccard (token kumesi) VEYA
difflib orani esigi gecerse benzer sayilir. Esikler config'den gelir.
Tek baglantili (single-link) acl gozlu kumeleme: her girdi, benzer oldugu
ilk kumeye katilir, yoksa yeni kume acar. Zincirleme (A~B~C) riskine karsi
kume boyutu `max_size` ile sinirlidir; dolu kumeye benzeyen girdi yeni kume
acar ve uyari loglanir.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+")
_SEMVER = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def parse_version(title: str) -> tuple[int, int, int] | None:
    m = _SEMVER.search(title)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def representative(members: list[dict]) -> dict:
    """Kumeyi temsil eden girdi: en yuksek semver, yoksa ilk girdi.

    Ayni repodan gelen surum kumelerinde en yeni surum secilir; yama
    surumler secim kotasini tuketmez (prefilter yama cezasini verir).
    """
    with_ver = [(parse_version(str(m.get("title") or "")), m) for m in members]
    winners = [(v, m) for v, m in with_ver if v is not None]
    if winners:
        return max(winners, key=lambda t: t[0])[1]
    return members[0]


def normalize_title(title: str) -> str:
    return " ".join(_TOKEN.findall(title.lower()))


def tokens(title: str) -> set[str]:
    return set(normalize_title(title).split())


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def title_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def similar(
    a: str, b: str, *, jaccard_thr: float = 0.5, difflib_thr: float = 0.8
) -> bool:
    if not a.strip() or not b.strip():
        return False
    return jaccard(a, b) >= jaccard_thr or title_ratio(a, b) >= difflib_thr


def cluster(
    items: list[dict],
    *,
    jaccard_thr: float = 0.5,
    difflib_thr: float = 0.8,
    max_size: int = 6,
) -> list[list[dict]]:
    """items: {"id", "title", "canonical_url"?}. Donus: kumeler listesi."""
    clusters: list[list[dict]] = []
    canon_to_cluster: dict[str, int] = {}
    for it in items:
        canon = str(it.get("canonical_url") or "")
        if canon and canon in canon_to_cluster:
            ci = canon_to_cluster[canon]
            if len(clusters[ci]) < max_size:
                clusters[ci].append(it)
                continue
            log.warning("dedupe: kume %d dolu (%d), yeni kume aciliyor", ci, max_size)
        placed = False
        for ci, members in enumerate(clusters):
            if len(members) >= max_size:
                continue
            if any(
                similar(
                    str(it.get("title") or ""),
                    str(m.get("title") or ""),
                    jaccard_thr=jaccard_thr,
                    difflib_thr=difflib_thr,
                )
                for m in members
            ):
                members.append(it)
                placed = True
                if canon:
                    canon_to_cluster[canon] = ci
                break
        if not placed:
            if canon:
                canon_to_cluster[canon] = len(clusters)
            clusters.append([it])
    return clusters


def pair_score(a: str, b: str) -> float:
    """Iki baslik arasi benzerlik (0-1): Jaccard ve difflib'in buyugu."""
    return max(jaccard(a, b), title_ratio(a, b))


def cluster_quality(members: list[dict]) -> float:
    """Kume-ici en dusuk cift benzerligi. Tekil kumede 1.0."""
    if len(members) < 2:
        return 1.0
    worst = 1.0
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            worst = min(
                worst,
                pair_score(str(members[i].get("title") or ""), str(members[j].get("title") or "")),
            )
    return worst
