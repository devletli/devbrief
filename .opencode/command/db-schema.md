---
description: SQLite şeması, migrasyon ve repository katmanı
---
docs/02-ARCHITECTURE.md §5'teki şemayı `src/devbrief/db.py` içinde uygula:
- Sürümlü basit migrasyon sistemi (`schema_version` tablosu, SQL dosyaları `src/devbrief/migrations/`).
- Küçük repository fonksiyonları (upsert_item, get_new_items, save_classification, set_episode_status, count_llm_calls_today vb.). ORM kullanma.
- WAL modu, foreign keys açık, bağlantı context manager.
- Her fonksiyon için `tests/test_db.py` içinde testler (bellekte `:memory:` veritabanı).
Çalıştır: `uv run pytest -q`. $ARGUMENTS
