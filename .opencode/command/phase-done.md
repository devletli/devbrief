---
description: Faz bitiş kontrolü, roadmap güncelleme, commit önerisi
---
Faz $ARGUMENTS için:
1. docs/01-ROADMAP.md'deki "Bitti sayılır" ölçütlerini tek tek doğrula (komutları çalıştırarak). Doğrulanamayanı açıkça belirt.
2. `uv run pytest -q` ve `uv run ruff check .` çalıştır.
3. Doğrulanan kutuları roadmap'te işaretle.
4. Kısa bir değişiklik özeti ve conventional commit mesajı öner (commit atma).
