---
description: Ön puanlama, toplu sınıflandırma, kümeleme ve günlük seçim
---
docs/01-ROADMAP.md Faz 2 ve docs/02-ARCHITECTURE.md §6'ya göre `src/devbrief/curate/` modüllerini yaz:
1. `prefilter.py`: LLM'siz prescore, katsayılar config'ten; ilk 40 girdi.
2. `dedupe.py`: aynı URL/benzer başlık (örn. normalize edilmiş başlık + difflib oranı) → cluster.
3. `rank.py`: 20'şerli batch ile `classify_batch` çağır, sonuçları DB'ye yaz.
4. `select.py`: `select_daily` ile en fazla 3 küme + 1 yedek.
5. CLI: `devbrief curate [--dry-run]` gerekçeli tablo yazdırsın.
Sahte LLM ile uçtan uca test yaz. $ARGUMENTS
