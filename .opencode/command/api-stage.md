---
description: FastAPI servisi, iş kuyruğu, callback ve platform paketleri
---
docs/05-N8N-AUTOMATION.md "Python API sözleşmesi" bölümüne birebir uyarak `src/devbrief/api/` yaz:
- `routes.py`: tüm endpoint'ler; `/episodes/{id}/video` için FileResponse (Range desteği).
- `auth.py`: Bearer token (`DEVBRIEF_API_TOKEN`), sabit zamanlı karşılaştırma; `/health` hariç hepsi korumalı.
- `jobs.py`: `jobs` tablosu (migrasyon ekle), tek işçi kuyruğu, aynı anda tek `run` (409), yeniden başlatmada `running → failed`, aşama bilgisi.
- `callback.py`: iş bitince `N8N_CALLBACK_URL`'e `X-Callback-Secret` başlığıyla POST, 3 deneme + üstel bekleme.
- `publish/platforms.py`: platform başına paket (youtube, x, linkedin, instagram, tiktok, threads, bluesky); limitler sabit sözlükte, limit aşımında kısaltma ve uyarı; limit değerlerini yorumda "doğrula" notuyla işaretle.
- Durum geçişleri yalnızca db durum makinesinden; `approve` yalnızca `rendered`.
- `devbrief serve --host --port` CLI komutu.
- Testler: httpx `AsyncClient` ile; yetkisiz 401, çift run 409, geçersiz durum geçişi, callback yeniden deneme, paket limit doğrulama. Pipeline'ı sahte aşamalarla çalıştır.
$ARGUMENTS
