"""Font yukleyici: her zaman repodaki assets/fonts/ kullanilir.

Gerekce: Dockerfile yalnizca fonts-dejavu-core kuruyor; apt'e bagimli kalmak
Windows'ta ve Docker'da farkli goruntu uretir. Inter (baslik/govde) ve
JetBrains Mono (kod) OFL lisanslidir, repoya gomulebilir.

Kullanim:
    from devbrief.render.fonts import load_font
    font = load_font("inter", 64)
"""

from __future__ import annotations

from pathlib import Path

FONT_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"

BUNDLED_FONTS = {
    "inter": "Inter-Regular.ttf",
    "inter-bold": "Inter-Bold.ttf",
    "mono": "JetBrainsMono-Regular.ttf",
    "mono-bold": "JetBrainsMono-Bold.ttf",
}

# Geriye donuk uyumluluk: eski kod `TR_FONT` bekleyebilir.
TR_FONT = str(FONT_DIR / BUNDLED_FONTS["inter"])


def bundled_font_path(name: str = "inter") -> Path:
    fname = BUNDLED_FONTS.get(name, BUNDLED_FONTS["inter"])
    return FONT_DIR / fname


def list_bundled_fonts() -> dict[str, bool]:
    return {k: bundled_font_path(k).exists() for k in BUNDLED_FONTS}


def load_font(name: str = "inter", size: int = 64):
    """Repocular fontu yukle; yoksa Pillow varsayilanina dus (asla apt yoluna bakma).

    Variable TTF'de kalinlik otomatik secilmez: `-bold` istenirse
    `set_variation_by_name("Bold")`, olmazsa eksen uzerinden 700 denenir.
    Destek yoksa normal agirlik doner (patlamaz).
    """
    from PIL import ImageFont

    path = bundled_font_path(name)
    try:
        if path.exists():
            font = ImageFont.truetype(str(path), size)
            if name.endswith("-bold"):
                _apply_bold(font)
            return font
    except OSError:
        pass
    return ImageFont.load_default()


def _apply_bold(font) -> None:
    try:
        font.set_variation_by_name("Bold")
        return
    except Exception:  # noqa: BLE001 - ortama gore destek degisir
        pass
    try:
        axes = font.get_variation_axes()
        if len(axes) == 1:
            font.set_variation_by_axes([700])
        elif len(axes) >= 2:
            # Inter: [opsz, wght] -> opsz varsayilanda kal, wght=700.
            font.set_variation_by_axes([axes[0]["default"], 700])
    except Exception:  # noqa: BLE001
        pass
