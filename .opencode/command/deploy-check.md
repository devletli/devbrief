---
description: Devreye alma kontrol listesi (deploy-check)
---
`uv run devbrief deploy-check [--api-url http://127.0.0.1:8000]` çalıştır:
1. Çıktıdaki her FAIL için ilgili dosyayı düzelt (öneri satırda yazar).
2. WARN'ları değerlendir; bilinçli kabul edileni not et.
3. FAIL kalmayınca `git commit` öner.
Özel durumlar:
- `n8n-port` WARN: ters vekil arkasındaysan kabul edilebilir.
- `paid-korumasi` WARN: yalnızca allowlist daraltıldıysa kabul edilir.
- `yedek-yok/eski`: `devbrief backup` çalıştır, ayda bir `restore` tatbikatı yap.
$ARGUMENTS
