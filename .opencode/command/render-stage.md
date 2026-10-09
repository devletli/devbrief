---
description: TTS, altyazı, slayt ve ffmpeg ile video üretimi
---
docs/02-ARCHITECTURE.md §9'a göre `src/devbrief/render/` yaz:
- `tts.py`: edge-tts, WordBoundary zamanlamalarını kaydet; ses sahne bazında üretilsin.
- `subtitles.py`: 3-5 kelimelik gruplar, ASS stili (büyük, kalın, alt-orta güvenli alan).
- `slides.py`: Pillow ile 1080x1920 şablonlar (title, bullets, code, diff, source); tek tasarım sistemi (renk/font sabitleri `render/theme.py`). Fontları `assets/fonts/` altında lisansı uygun olanlardan kullan.
- `compose.py`: ffmpeg subprocess ile birleştirme, zoompan, müzik opsiyonel, altyazı yakma; çıktı `out/<tarih>/<slug>/video.mp4`, `thumb.png`, `meta.json`.
- CLI: `devbrief render --episode <id>`.
- Test: 5 sn'lik örnek sahte senaryodan video üret, `ffprobe` ile süre/çözünürlük/ses akışını doğrula.
$ARGUMENTS
