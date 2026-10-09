---
description: Bir kaynak için toplayıcı modül + test (rss, hn, github-releases, arxiv)
---
`src/devbrief/collect/` altında "$ARGUMENTS" kaynağı için toplayıcı yaz:
- `sources.yaml` yapılandırmasını kullan; fonksiyon imzası `async def collect(cfg) -> list[RawItem]`.
- URL normalizasyonu (utm_* sil, fragment sil, trailing slash) ve `content_hash` hesapla.
- Timeout 15 sn, User-Agent `DevBriefTR/0.1 (+iletişim)`, ETag/Last-Modified desteği, hata olursa o kaynağın hatası `source_runs` tablosuna yazılır, istisna dışarı sızmaz.
- Son `max_age_hours` içindeki girdileri al.
- `tests/fixtures/` altına örnek yanıt koy, `respx` ile mock'layarak test yaz.
- `uv run devbrief collect --source <ad>` CLI komutunu bağla (yoksa ekle).
Bittiğinde testleri çalıştır ve özetle.
