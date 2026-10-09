"""Render tasarim sistemi: tek renk/font kaynagi. Slaytlar buradan beslenir."""

from __future__ import annotations

# 1080x1920 dikey video.
W, H = 1080, 1920

# Renkler.
BG = (13, 17, 23)  # github-dark zemin
BG_ALT = (22, 27, 34)  # kart zemini
ACCENT = (88, 166, 255)  # baslik vurgusu
TEXT = (240, 246, 252)  # govde
MUTED = (139, 148, 158)  # ikincil
CODE_BG = (22, 27, 34)
GREEN = (63, 185, 80)
RED = (248, 81, 73)

# Font adlari (render/fonts.py uzerinden cozulur).
FONT_BODY = "inter"
FONT_BOLD = "inter-bold"
FONT_MONO = "mono"
FONT_MONO_BOLD = "mono-bold"

# Punto buyuklukleri.
HOOK_SIZE = 84
TITLE_SIZE = 72
BODY_SIZE = 52
SMALL_SIZE = 40
CODE_SIZE = 40

# Guvenli alan (altyazi alt-orta, kenar bosluklari).
MARGIN_X = 80
SUBTITLE_SAFE_BOTTOM = 260
