---
description: Test ve lint çalıştır, hataları düzelt
---
Sırayla çalıştır: `uv run ruff check . --fix`, `uv run pytest -q`.
Hata varsa kök nedeni bul ve düzelt (testi zayıflatarak geçirme). Yeşil olana kadar tekrarla, en fazla 5 tur. Sonunda neyi neden değiştirdiğini kısaca özetle. $ARGUMENTS
