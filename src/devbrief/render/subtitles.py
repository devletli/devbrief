"""Altyazi: kelime zamanlari -> 3-5 kelimelik gruplar -> SRT/ASS.

Birim: saniye (float). edge-tts WordBoundary kayitlari tts.py'de saniyeye cevrilir.
ASS stili: buyuk, kalin, alt-orta, guvenli alan icinde (theme).
"""

from __future__ import annotations

from pathlib import Path

from devbrief.render import theme

MIN_WORDS, MAX_WORDS = 3, 5


def group_words(words: list[dict], max_words: int = MAX_WORDS) -> list[dict]:
    """Kelime listesini [{word,start,end}] -> [{start,end,text}] gruplar.

    Grup 3-5 kelime; cumle sonu (.?!) grubun bitmesini zorlar (max asilmadikca).
    """
    groups: list[dict] = []
    cur: list[dict] = []
    for w in words:
        cur.append(w)
        ends_sentence = str(w.get("word", "")).rstrip().endswith((".", "?", "!"))
        if len(cur) >= max_words or (ends_sentence and len(cur) >= MIN_WORDS):
            groups.append(_flush(cur))
            cur = []
    if cur:
        if (
            groups
            and len(cur) < MIN_WORDS
            and len(groups[-1]["_words"]) + len(cur) <= max_words
        ):
            last = groups.pop()
            groups.append(_flush(last["_words"] + cur))
        else:
            groups.append(_flush(cur))
    for g in groups:
        g.pop("_words", None)
    return groups


def _flush(cur: list[dict]) -> dict:
    text = " ".join(str(w.get("word", "")) for w in cur)
    start = float(cur[0]["start"])
    end = float(cur[-1]["end"])
    return {"start": start, "end": end, "text": text, "_words": cur}


def fmt_ass(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, rem = divmod(rem, 60)
    s = int(rem)
    cs = int(round((rem - s) * 100))
    return f"{int(h)}:{int(m):02d}:{s:02d}.{cs:02d}"


def fmt_srt(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, rem = divmod(rem, 60)
    s = int(rem)
    ms = int(round((rem - s) * 1000))
    return f"{int(h):02d}:{int(m):02d}:{s:02d},{ms:03d}"


_ASS_STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour,"
    " Bold, Italic, Alignment, MarginL, MarginR, MarginV, Encoding"
)
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
@@FORMAT@@
Style: DevBrief,Arial,{size},&H00FFFFFF,&H80000000,&H80000000,-1,0,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Text
""".replace("@@FORMAT@@", _ASS_STYLE_FORMAT)


def write_ass(groups: list[dict], path: str | Path) -> Path:
    out = Path(path)
    lines = [
        ASS_HEADER.format(size=64, margin_v=theme.SUBTITLE_SAFE_BOTTOM).strip(),
    ]
    for g in groups:
        text = str(g["text"]).replace("\n", " ")
        lines.append(f"Dialogue: 0,{fmt_ass(g['start'])},{fmt_ass(g['end'])},DevBrief,{text}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def write_srt(groups: list[dict], path: str | Path) -> Path:
    out = Path(path)
    lines = []
    for i, g in enumerate(groups, 1):
        lines += [str(i), f"{fmt_srt(g['start'])} --> {fmt_srt(g['end'])}", str(g["text"]), ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
