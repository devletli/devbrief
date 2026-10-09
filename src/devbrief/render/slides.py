"""Slaytlar: Pillow ile 1080x1920 PNG. Tek tasarim sistemi (theme).

Gorsel turleri: title | bullets | code | diff | source.
Fontlar assets/fonts/ icinden (render/fonts.py); apt yoluna bakilmaz.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from devbrief.render import fonts, theme

_CODE_COLORS = {
    "Token.Keyword": theme.GREEN,
    "Token.Name.Decorator": theme.GREEN,
    "Token.Literal.String": (255, 215, 130),
    "Token.Comment": theme.MUTED,
    "Token.Literal.Number": (255, 150, 130),
    "Token.Operator": theme.ACCENT,
}

BW = theme.W - 2 * theme.MARGIN_X  # govde genisligi


def _font(name: str, size: int):
    return fonts.load_font(name, size)


def _small():
    return _font(theme.FONT_BODY, theme.SMALL_SIZE)


def _base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (theme.W, theme.H), theme.BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, theme.W, 14], fill=theme.ACCENT)
    return img, d


def _header(d: ImageDraw.ImageDraw, kicker: str) -> None:
    d.text((theme.MARGIN_X, 90), kicker,
           font=_font(theme.FONT_BOLD, theme.SMALL_SIZE), fill=theme.MUTED)


def _footer(d: ImageDraw.ImageDraw, left: str, right: str) -> None:
    y = theme.H - 160
    d.text((theme.MARGIN_X, y), left[:60], font=_small(), fill=theme.MUTED)
    r = str(right)
    w = d.textlength(r, font=_small())
    d.text((theme.W - theme.MARGIN_X - w, y), r, font=_small(), fill=theme.MUTED)


def _wrap(d: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _body(d, text, font, y, fill=None) -> int:
    for line in _wrap(d, text, font, BW):
        d.text((theme.MARGIN_X, y), line, font=font, fill=fill or theme.TEXT)
        y += int(font.size * 1.35)
    return y


def _code_lines(d: ImageDraw.ImageDraw, code: str, y: int) -> int:
    try:
        from pygments import lex
        from pygments.lexers import get_lexer_by_name

        lexer = get_lexer_by_name("bash")
    except Exception:  # noqa: BLE001 - pygments yoksa duz metin
        lexer = None
    font = _font(theme.FONT_MONO, theme.CODE_SIZE)
    pad = 40
    for raw in code.splitlines()[:14]:
        x = theme.MARGIN_X + pad
        if lexer is None:
            d.text((x, y), raw, font=font, fill=theme.TEXT)
        else:
            for ttype, value in lex(raw, lexer):
                color = _CODE_COLORS.get(str(ttype), theme.TEXT)
                for frag in str(value).split("\n"):
                    if not frag:
                        continue
                    d.text((x, y), frag, font=font, fill=color)
                    x += d.textlength(frag, font=font)
        y += int(theme.CODE_SIZE * 1.5)
    return y


def render_scene(scene: dict, *, hook: str, index: int, total: int, out: str | Path) -> Path:
    """Tek sahne PNG'si. scene: {on_screen, visual, code}."""
    img, d = _base()
    visual = str(scene.get("visual") or "title")
    _header(d, f"DevBrief • {index + 1}/{total}")
    y = 300
    if visual == "title":
        y = _body(d, hook, _font(theme.FONT_BOLD, theme.HOOK_SIZE), y)
        y = _body(d, str(scene.get("on_screen") or ""),
                  _font(theme.FONT_BODY, theme.TITLE_SIZE), y + 40, theme.ACCENT)
    elif visual == "bullets":
        y = _body(d, hook, _font(theme.FONT_BOLD, theme.TITLE_SIZE), y)
        for part in str(scene.get("on_screen") or "").split("•"):
            if part.strip():
                y = _body(d, f"• {part.strip()}",
                          _font(theme.FONT_BODY, theme.BODY_SIZE), y + 16) + 16
    elif visual in ("code", "diff"):
        y = _body(d, hook, _font(theme.FONT_BOLD, theme.BODY_SIZE), y)
        d.rounded_rectangle(
            [theme.MARGIN_X - 20, y + 10, theme.W - theme.MARGIN_X + 20, y + 590],
            radius=24, fill=theme.CODE_BG,
        )
        _code_lines(d, str(scene.get("code") or scene.get("on_screen") or ""), y + 40)
    elif visual == "source":
        y = _body(d, "Kaynak", _font(theme.FONT_BOLD, theme.TITLE_SIZE), y, theme.ACCENT)
        y = _body(d, str(scene.get("on_screen") or ""),
                  _font(theme.FONT_BODY, theme.BODY_SIZE), y + 30)
    else:
        y = _body(d, str(scene.get("on_screen") or hook),
                  _font(theme.FONT_BODY, theme.TITLE_SIZE), y)
    _footer(d, "DevBrief TR", f"{index + 1}/{total}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def render_thumb(hook: str, out: str | Path) -> Path:
    img, d = _base()
    _header(d, "DevBrief")
    _body(d, hook, _font(theme.FONT_BOLD, theme.HOOK_SIZE), 700)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
