# opencode ile Kullanım Rehberi

## Kurulum
1. Bu klasörü repo olarak aç: `git init && git add -A && git commit -m "init docs"`
2. opencode'u kur (güncel yöntem: https://opencode.ai/docs/) ve proje klasöründe `opencode` çalıştır.
3. `/connect` ile OpenRouter'ı bağla, `/models` ile geliştirme için bir model seç. (Geliştirme için kullandığın model istekleri de OpenRouter limitinden yer; ücretsiz istek bütçeni uygulama çalışma zamanına ayırmak istersen geliştirmede farklı bir sağlayıcı/model kullan.)
   **Önemli:** Geliştirme (opencode) ve uygulama (`devbrief`) için **ayrı API anahtarı** kullan. Ayrı anahtarlarda limit/vade takibi birbirine karışmaz; `devbrief doctor` ve `report --llm` uygulama anahtarının kotasını gösterir. Uygulama anahtarını opencode'a bağlama.
4. İlk açılışta `/init` **çalıştırma**: `AGENTS.md` zaten hazır.

## Dosyalar
| Dosya | Amaç |
|---|---|
| `AGENTS.md` | Kalıcı proje kuralları (opencode otomatik okur) |
| `opencode.json` | İzinler ve ek talimat dosyaları |
| `.opencode/command/*.md` | Özel slash komutları (`/komut argüman`) |
| `.opencode/agent/*.md` | Özel alt ajanlar (`@reviewer` ile çağrılır) |
Not: Sürüme göre klasör adları çoğul (`commands/`, `agents/`) da olabilir; komutlar görünmezse https://opencode.ai/docs/commands/ sayfasına bak ve klasörü yeniden adlandır.

## Komutlar
| Komut | Ne yapar |
|---|---|
| `/phase-start 1` | Fazı roadmap'ten okur, görev listesi çıkarır, onayını ister |
| `/scaffold` | Faz 0: proje iskeleti, bağımlılıklar, doctor komutu |
| `/db-schema` | Şema + migrasyon + repository katmanı |
| `/collector hn` | Verilen kaynak için toplayıcı + test yazar |
| `/llm-client` | OpenRouter istemcisi (retry, fallback, bütçe, cache) |
| `/curate` | Ön puanlama, sınıflandırma, seçim aşaması |
| `/script-stage` | Senaryo yazma + doğrulama hattı |
| `/render-stage` | TTS, altyazı, slayt, ffmpeg |
| `/api-stage` | FastAPI servisi, iş kuyruğu, callback, platform paketleri |
| `/n8n-flow <ad>` | n8n akışını docs/05'e göre tasarla/güncelle |
| `/review-bot` | (Opsiyonel) CLI onay kuyruğu, n8n'siz yedek |
| `/publisher export` | (Opsiyonel) Elle yayın için dışa aktarma paketi |
| `/test-fix` | Testleri çalıştırır, hataları düzeltir |
| `/audit-content <dosya>` | Bir üretilen senaryoyu doğruluk/üslup açısından denetler |
| `/phase-done 1` | DoD kontrolü, roadmap kutularını işaretler, commit mesajı önerir |

## Önerilen çalışma döngüsü
```
/phase-start 0  →  /scaffold  →  /test-fix  →  /phase-done 0
/phase-start 1  →  /db-schema →  /collector rss → /collector hn → /collector github-releases → /test-fix → /phase-done 1
/phase-start 2  →  /llm-client → /curate → /test-fix → /phase-done 2
/phase-start 3  →  /script-stage → /audit-content ... → /phase-done 3
/phase-start 4  →  /render-stage → /phase-done 4
/phase-start 5  →  /api-stage → /test-fix → /phase-done 5
/phase-start 6  →  /n8n-flow daily-trigger → /n8n-flow approve-and-publish → /phase-done 6
```
Her `/phase-done` sonrası `git commit`.

## Vibe coding ipuçları
- Plan modunda (Tab ile Plan/Build geçişi) önce planı gör, sonra Build'e geç.
- Bir komut çok büyük çıktı üretirse "yalnızca ilk alt görevi yap" de.
- Test kırmızıyken yeni özelliğe geçme.
- Prompt dosyalarını sen oku ve düzenle; ürünün kalitesi orada.
- Her gün sonunda `git commit`; opencode'un değişikliklerini `git diff` ile gözden geçir.
