# Devreye Alma (Deploy)

## (a) Lokal Docker adımları
1. `.env` doldur (`init-secrets`, OpenRouter anahtarı, modeller, `PUBLISH_MODE=shadow`).
2. `docker compose build && docker compose up -d`.
3. n8n ilk kullanıcı: `http://localhost:5678` → hesap aç.
4. 5 JSON'u içe aktar (`n8n/01..05`): Workflows → Import from file.
5. Credential'lar: Telegram (bot token), YouTube OAuth2 (Web app + n8n callback URL'si), "DevBrief API Token" (Header Auth: `Authorization: Bearer <DEVBRIEF_API_TOKEN>`), "DevBrief Callback Secret" (Header Auth: `X-Callback-Secret: <N8N_CALLBACK_SECRET>`).
6. Config düğümlerindeki `DEGISTIR_*` değerlerini doldur.
7. **Error Workflow:** her akışın Settings → Error Workflow = "DevBrief 03 - Hata Bildirimi" (elle seçilir, JSON'a yazılamaz).
8. `devbrief deploy-check` → FAIL sıfırla. Demo turu: `seed-demo-episode` → 02 akışını elle çalıştır → Telegram onayı → YouTube `private` kontrol.

## (b) shadow → private → live geçişleri
- **shadow (varsayılan):** package boş döner, `/published` 409. Kriter: 7-10 gün çalışsın, 20 bölümlük audit kritik-hata=0, eval-verifier kapısı geçsin.
- **private:** `.env` `PUBLISH_MODE=private` + restart. Yalnızca özel YouTube. Kriter: 3-5 özel video telefonda izlendi, altyazı/kanca/ses doğal.
- **live:** `.env` `PUBLISH_MODE=live` **ve** `LIVE_CONFIRM=EVET_CANLI` (ikisi de şart; yoksa sessizce private'a düşer + log). Ayrı insan onayı gerekir (yazılı karar: tarih + imza). Demo bölümler live'da bile private kalır.

## (c) VPS taşıma (ör. Hetzner 2 vCPU/4 GB)
1. Repo + `.env` + `backups/` kopyala; `docker compose up -d`.
2. Caddy ters vekil (örnek Caddyfile):
   ```
   n8n.ornek.com {
       reverse_proxy 127.0.0.1:5678
       basicauth / {
           devbrief <BCRYPT_SIFRE>
       }
   }
   ```
   8000 portunu dışarı **açma** (yalnızca `expose`, iç ağ).
3. n8n Basic Auth veya IP kısıtı şart; `N8N_ENCRYPTION_KEY` yedeğini `.env` dışında ayrı sakla (kaybolursa credential'lar çözülemez).
4. `WEBHOOK_URL` ve `N8N_CALLBACK_URL`'i gerçek adrese çevir.

## (d) Yedek / geri yükleme
- Otomatik: 05 akışı her gece `POST /backup` çağırır (sqlite backup API + integrity + 7 günlük rotasyon).
- Elle: `devbrief backup`; liste `backups/` altında.
- Geri yükleme: `devbrief restore backups/devbrief-XXX.db` (canlı DB önce `.bak` alınır, onay ister).
- **Ayda bir tatbikat:** yedeği kopyala → `restore` ile test DB'sine yükle → `integrity_check` + bölüm sayısı karşılaştır.

## (e) Anahtar ve secret döndürme
- OpenRouter anahtarı: panelden yenile → `.env` → `doctor` + `eval-models --only P9 --yes` ile 1 istek testi.
- Telegram bot token / API token değişirse: n8n credential + `.env` aynı anda güncellenir, `deploy-check` koşulur.
- Ayrı anahtar kuralı (docs/04): geliştirme ve uygulama anahtarları farklı.

## (f) Sorun giderme
| Belirti | Bak |
|---|---|
| Callback ulaşmıyor | Docker iç ağ adı (`devbrief-api:8000`), `N8N_CALLBACK_URL`, webhook Header Auth eşleşmesi |
| Windows'ta `host.docker.internal` gerekli | compose'da `extra_hosts` veya n8n `api_url`'yi host IP yap |
| ffmpeg bulunamadı | `FFMPEG_PATH` / repo `bin/` / Docker imajı |
| 401 fırtınası | `.env` token ile n8n Header Auth aynı mı |
| Free kota bitti (429) | `report --llm`, sabah tekrar; probe grafiği |

## (g) Medya deposu (R2/S3) yaşam döngüsü
- Anahtarlar yalnız `.env`'de durur (`MEDIA_S3_*`); n8n JSON ve loglara asla yazılmaz (`deploy-check` bunu tarar).
- Nesne anahtarı `<prefix>/<token>/video.mp4` biçimindedir; token bölüm kaydındadır, slug/gün içermez.
- Süre dolumu iki katmanlıdır: `devbrief media-prune` (`MEDIA_S3_TTL_DAYS`, varsayılan 7) DB kaydındaki nesneleri siler; yedek olarak bucket'ta yaşam döngüsü kuralı önerilir:
  - **R2:** bucket → Settings → Object lifecycle: prefix `<prefix>/` için `Expire after N days` (N = TTL).
  - **S3:** bucket → Management → Lifecycle: aynı prefix için `Expiration` N gün; ayrıca **Block public access: ListObjects kapalı olduğunu** el ile doğrula (nesneler herkese açık okunur, **kova listelemesi kapalı** olmalı) — otomatik denetlenemez, `deploy-check` WARN verir.
- El ile doğrulama adımları: (1) imzasız `GET <video_url>` 200 dönmeli; (2) `GET <bucket-kökü>/?list-type=2` (veya R2 eşdeğeri) `AccessDenied` vermeli; (3) `media-upload --dry-run` imza üretmeli, ağa çıkmamalı.
