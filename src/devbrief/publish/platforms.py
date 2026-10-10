"""Platform paketleri: metin + limit dogrulama + kisaltma.

Limit degerleri degisken platform kurallaridir; "dogrula" isaretliler
guncel dokumandan teyit edilmeli. Asimda metin kesilir + uyari listelenir.
Aciklamaya kaynak linki + sentetik icerik beyanı her zaman eklenir.
"""

from __future__ import annotations

AI_NOTE = ("Yapay zekâ destekli hazırlanmıştır ve sentetik ses içerir; "
           "hata fark edersen yaz.")

# platform -> alan -> sert ust sinir (karakter). DOGRULA: guncel dokuman.
LIMITS: dict[str, dict[str, int]] = {
    "youtube": {"title": 100, "description": 5000, "tags": 8},  # dogrula: YT yardim
    "x": {"text": 280},  # dogrula: X yardim (ucretli katman degisken)
    "linkedin": {"text": 3000},  # dogrula: LinkedIn yardim
    "instagram": {"caption": 2200},  # dogrula: Meta Graph API
    "tiktok": {"caption": 2200},  # dogrula: Content Posting API
    "threads": {"text": 500},  # dogrula: Threads API
    "bluesky": {"text": 300},  # dogrula: Bluesky dokuman
}
BASE_TAGS = ["devbrief", "teknoloji", "yazilim"]


def _cut(text: str, limit: int, warnings: list[str], what: str) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    warnings.append(f"{what} {len(text)}->{limit} kirpildi")
    return text[: max(0, limit - 1)].rstrip() + "…"


def build_package(episode: dict, video_url: str, mode: str = "live") -> dict:
    """episode: id/slug/title/description/script_json/source_url anahtarlari.

    Modlar: shadow -> publish_allowed:false + bos platforms; private -> yalnizca
    ozel youtube; live -> hepsi, youtube public. is_demo asla public olamaz.
    """
    import json as _json

    warnings: list[str] = []
    is_demo = bool(episode.get("is_demo"))
    if mode == "shadow":
        return {"episode_id": episode.get("id"), "video_url": video_url,
                "publish_allowed": False, "platforms": {}, "warnings": warnings}
    script = {}
    try:
        script = _json.loads(episode.get("script_json") or "{}")
    except ValueError:
        pass
    hook = str(script.get("hook") or episode.get("title") or episode.get("slug"))
    source = episode.get("source_url") or script.get("source_url") or ""
    impact = str(script.get("impact") or "")
    action = str(script.get("action") or "")
    base_desc = str(episode.get("description") or f"{impact} {action}".strip() or hook)
    source_line = f"\n\nKaynak: {source}" if source else ""
    description = f"{base_desc}{source_line}\n{AI_NOTE}"
    privacy = "private"
    if mode == "live" and not is_demo:
        privacy = "public"
    elif is_demo:
        warnings.append("demo bolum: public yasak, private")
    plat: dict[str, dict] = {
        "youtube": {
            "title": _cut(episode.get("title") or hook, LIMITS["youtube"]["title"],
                           warnings, "yt-title"),
            "description": _cut(description, LIMITS["youtube"]["description"],
                                warnings, "yt-desc"),
            "tags": BASE_TAGS[: LIMITS["youtube"]["tags"]],
            "privacy": privacy,
        },
    }
    if mode == "live":
        plat.update({
            "x": {"text": _cut(f"{hook} {source}".strip(), LIMITS["x"]["text"],
                               warnings, "x")},
            "linkedin": {"text": _cut(description, LIMITS["linkedin"]["text"],
                                      warnings, "linkedin")},
            "instagram": {"caption": _cut(
                f"{hook}\n{source}\n#{' #'.join(BASE_TAGS)}",
                LIMITS["instagram"]["caption"], warnings, "instagram")},
            "tiktok": {"caption": _cut(f"{hook}\n{source}", LIMITS["tiktok"]["caption"],
                                       warnings, "tiktok")},
            "threads": {"text": _cut(f"{hook} {source}".strip(), LIMITS["threads"]["text"],
                                     warnings, "threads")},
            "bluesky": {"text": _cut(f"{hook} {source}".strip(), LIMITS["bluesky"]["text"],
                                     warnings, "bluesky")},
        })
    return {"episode_id": episode.get("id"), "video_url": video_url,
            "publish_allowed": True, "platforms": plat, "warnings": warnings}
