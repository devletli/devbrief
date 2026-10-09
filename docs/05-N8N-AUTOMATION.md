# n8n Orkestrasyonu + Python API

## Karar
**Tüm tetikleme, onay ve çok platformlu dağıtım n8n'de. Tüm ağır iş (toplama, LLM, doğrulama, render) Python servisinde.**
Flowise kullanılmaz (LLM zinciri aracı; zamanlama/sosyal medya için uygun değil).

## Sorumluluk ayrımı
| n8n | Python (`devbrief serve`, FastAPI) |
|---|---|
| Zamanlayıcı (hafta içi 07:00) | collect → curate → script → verify → render |
| Telegram'da video + Onayla/Reddet | Durum makinesi, DB, bütçe sayacı |
| Platform başına yayın (fan-out) | Platform başına metin paketi üretimi |
| Hata bildirimi, haftalık rapor | Metrik toplama endpoint'i |
| Yeniden deneme, bekleme | İş kuyruğu (tek işçi) |

**Kural:** n8n'de iş mantığı yok. Karar, metin, doğrulama hep Python'da; n8n akışı sade kalır ve kod testlenebilir kalır.

## Mimari
```
n8n (Schedule 07:00)
   └─ POST /run {callback_url} ───────────────► Python API (job başlar, 202 döner)
                                                  collect→curate→script→verify→render
   ◄── POST callback (webhook) ────────────────  iş bitti: {job_id, episodes:[...]}
n8n (Webhook)
   ├─ GET /episodes?status=rendered
   ├─ her bölüm için: video indir → Telegram'a gönder → Onayla/Reddet bekle
   ├─ onay → POST /episodes/{id}/approve → GET /episodes/{id}/package
   ├─ paralel yayın: YouTube | X | LinkedIn | Instagram | TikTok | Threads/Bluesky ...
   └─ POST /episodes/{id}/published {platform sonuçları} → Telegram özet
```
Polling yok: Python işi bitirince n8n webhook'unu çağırır (callback). Hata olursa callback `status:"failed"` ile gelir.

## Python API sözleşmesi (`src/devbrief/api/`)
Kimlik doğrulama: `Authorization: Bearer <DEVBRIEF_API_TOKEN>` (zorunlu). Sadece n8n'in erişebildiği ağda çalıştır (Docker iç ağı); internete açma.

| Metot | Yol | Açıklama |
|---|---|---|
| POST | `/run` | Body: `{"callback_url": str, "max_episodes": 3, "stages": "all"}`. 202 + `{job_id}`. Zaten iş varsa 409. |
| GET | `/jobs/{job_id}` | `{status: queued|running|done|failed, stage, error, episode_ids}` |
| GET | `/episodes?status=rendered` | Liste (id, slug, title, source_url, claim özeti, partial_claims sayısı) |
| GET | `/episodes/{id}` | Ayrıntı: senaryo, doğrulama sonucu |
| GET | `/episodes/{id}/video` | mp4 dosyası (stream) |
| GET | `/episodes/{id}/thumb` | png |
| POST | `/episodes/{id}/approve` | `rendered → approved` |
| POST | `/episodes/{id}/reject` | Body: `{reason}`; `→ rejected` |
| GET | `/episodes/{id}/package` | Platform başına hazır metinler (aşağıda) |
| POST | `/episodes/{id}/published` | Body: `{results:[{platform, ok, url, external_id, error}]}` → `publications` kaydı, hepsi başarılıysa `published` |
| POST | `/metrics` | Opsiyonel: `{platform, external_id, views, ...}` |
| GET | `/health` | `doctor` özeti, bugünkü LLM isteği sayısı |

### Package yanıtı
```json
{
  "episode_id": 12, "video_url": "http://devbrief-api:8000/episodes/12/video",
  "platforms": {
    "youtube":   {"title": "...", "description": "... kaynak: URL ...", "tags": ["..."], "privacy": "public"},
    "x":         {"text": "≤ 270 karakter + link"},
    "linkedin":  {"text": "..."},
    "instagram": {"caption": "..."},
    "tiktok":    {"caption": "..."},
    "threads":   {"text": "..."},
    "bluesky":   {"text": "≤ 300 karakter"}
  }
}
```
Her platformun karakter/süre/boyut limitleri `publish/platforms.py` içinde sabit olarak tutulur ve **güncel dokümandan doğrulanır**; paket üretimi limitleri aşmayacak şekilde keser/doğrular. Metinler `write_metadata` promptundan gelir (platform başına kısa varyant); ek LLM isteği ≈ 1-2/bölüm.

### İş kuyruğu
- Tek işçi (`asyncio.Queue` + `BackgroundTasks`), aynı anda tek `run`. Render CPU yoğun: `ffmpeg` alt süreci ayrı thread/process'te.
- İş durumu `jobs` tablosunda (yeniden başlatmada `running → failed` işaretlenir).
- Callback: 3 deneme, üstel bekleme; başarısızsa log + `jobs.callback_failed=1`.

## Platform gerçekleri (dürüst tablo)
| Platform | Yöntem | Zorluk / not |
|---|---|---|
| YouTube Shorts | n8n YouTube düğümü veya API | Denetimsiz API projesinde yüklemeler özel kalabilir; günlük kota sınırlı |
| X | n8n X düğümü veya HTTP | Video yükleme/ücretli API katmanı koşulları değişken; kontrol et |
| LinkedIn | n8n LinkedIn düğümü / HTTP | Video yükleme için ek adımlar gerekebilir |
| Instagram Reels | Meta Graph API | İşletme hesabı + herkese açık video URL'si gerekir (yerel dosya yetmez) |
| TikTok | Content Posting API | Uygulama denetimi olmadan paylaşımlar özel/kısıtlı olabilir |
| Threads, Bluesky, Facebook | Native/HTTP | Genelde daha kolay |

**Gerçekçi öneri:** 5+ platformu tek tek entegre etmek yerine bir **yayın merkezi** kullan: self-host **Postiz** veya ücretli/ücretsiz katmanlı **Upload-Post, Ayrshare, Blotato** gibi servisler. n8n bunlara tek HTTP isteği atar; platform API denetimleri ve token yenilemeyi onlar yönetir. (Fiyat/limit/platform desteğini kendi ihtiyacına göre güncel sitelerinden karşılaştır.) Akışta YouTube yerel düğümle, geri kalanlar merkez üzerinden gider; ileride tek tek native düğüme geçebilirsin.
Instagram/hub için videonun herkese açık URL'ye ihtiyacı olabilir: Cloudflare R2 / S3 / Backblaze'e yükleyip imzalı URL ver (`publish/storage.py`, opsiyonel).

## n8n akışları (içe aktarma dosyaları `n8n/` klasöründe)
1. **01-daily-trigger.json**: Schedule (hafta içi 07:00) → Config → `POST /run`.
2. **02-approve-and-publish.json**: Webhook → bölümleri listele → video indir → Telegram'a gönder → Onayla/Reddet → onayda yayın → sonuç kaydı → Telegram özeti.
3. **03-error-handler.json** (sen ekle): Error Trigger → Telegram bildirimi; diğer akışların "Error Workflow" ayarına bağla.

İçe aktarmadan sonra yapılacaklar (JSON'lar iskelet; n8n sürümüne göre düğüm parametreleri küçük farklılık gösterebilir):
- Credential'ları oluştur (Telegram, YouTube OAuth, yayın merkezi API anahtarı, `DEVBRIEF_API_TOKEN` için Header Auth).
- `Config` düğümünde `api_url`, `chat_id`, `hub_url` değerlerini düzenle.
- Webhook URL'sini üretim URL'sine çevir ve Python `.env` içindeki `N8N_CALLBACK_URL`'e yaz.
- Onay düğümü (`Send and Wait`) sürümünde yoksa Telegram Trigger + callback butonları ile alternatif kur.
- Önce Telegram dışındaki yayın düğümlerini devre dışı bırakıp tek video ile uçtan uca test et (YouTube'u `private` ile).

## Dağıtım (docker-compose)
`docker-compose.yml`: `n8n` + `devbrief-api` + ortak `out/` birimi. n8n'e ve API'ye erişim yalnızca VPN/ters vekil + parola ile. n8n webhook'u dışarıdan çağrılmayacaksa iç ağ yeterli (callback iç ağdan gelir).
Telegram onayı için n8n'in internete çıkışı yeterli (long-polling/webhook seçimine göre).

## Güvenlik
- API token'ı yalnızca n8n credential'ında; log'lara yazma.
- Webhook'a gizli yol/başlık (`X-Callback-Secret`) koy, Python callback'te gönderir.
- Telegram'da yalnızca `chat_id` eşleşen kullanıcı onay verebilir.
- `approve` endpoint'i yalnızca `rendered` durumunda çalışır (Python tarafı doğrular), n8n atlayamaz.
