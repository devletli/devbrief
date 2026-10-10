# Yol Haritası

Tahmini toplam: **5-6 hafta**, günde 1-2 saat vibe coding ile. Her faz sonunda çalışan, test edilmiş bir çıktı olmalı. Kutuları tamamladıkça işaretle.

## Faz 0 — Kurulum (1-2 gün)
- [x] Repo, `uv init`, dizin yapısı, `ruff`, `pytest` iskeleti
- [x] OpenRouter hesabı, API anahtarı, **limit durumunu doğrula** (ücretsiz modellerde günlük 1000 istek için genelde hesapta en az 10 USD kredi gerekir; yoksa limit çok daha düşüktür. Güncel kuralı openrouter.ai/docs/api-reference/limits sayfasından kontrol et)
- [ ] 2-3 `:free` model seç (biri hızlı/küçük, biri güçlü), config'e yaz; Türkçe kalite testi yap (10 örnek paragraf) (kapı adayı yok; sabah tekrarı bekliyor)
- [x] ffmpeg kurulu mu, `edge-tts` Türkçe ses denemesi (birkaç sesi karşılaştır)
- [ ] YouTube Cloud projesi aç, OAuth istemcisi oluştur (yayın Faz 5'te)
- **Bitti sayılır:** `uv run devbrief doctor` anahtarları, ffmpeg'i, model erişimini kontrol edip yeşil basar.

## Faz 1 — Toplama ve Depolama (hafta 1)
- [x] SQLite şeması + migrasyon (`docs/02` §5)
- [x] Toplayıcılar: RSS, HN (Algolia API), GitHub release atom
- [x] URL normalizasyonu, `content_hash`, tekrar engelleme
- [x] `uv run devbrief collect` → son 24 saatin girdileri DB'de
- [x] Testler: kaynak başına kayıtlı örnek yanıtla (fixture)
- **Bitti:** 7 gün boyunca günlük ortalama ≥ 60 girdi, hata oranı < %5, ve her gün ilk 40'ta elle en az 5 "videoya çevirirdim" haber. Hatalı bir kaynak tüm akışı durdurmuyor (`source_runs` logu).

## Faz 2 — Eleme ve Seçim (hafta 1-2)
- [x] LLM istemcisi: retry, fallback, bütçe sayacı, cache, `llm_calls` logu
- [x] LLM'siz ön puanlama (kaynak ağırlığı, HN puanı, tazelik, anahtar kelime) → ilk 40
- [x] Toplu sınıflandırma: 20'şerli batch, JSON çıktı (kategori, önem 1-5, geliştiriciyi etkiler mi, kısa neden)
- [x] Kümeleme: aynı olay için birden çok kaynak → tek `cluster`
- [x] `select`: günün 1-3 adayı + yedek, gerekçeli
- **Bitti:** `devbrief curate` günlük 2-4 LLM isteği ile adayları gerekçesiyle listeler. 7 gün elle karşılaştır: seçimlerin %70+'ı "ben de seçerdim" mi?

## Faz 3 — Senaryo ve Doğrulama (hafta 2-3)
- [x] Tam makale metni çekme (`trafilatura`), uzunluk kırpma, kaynak hash
- [x] Senaryo: yapılandırılmış JSON (kanca, 3-5 sahne, her sahnede ekran metni + konuşma + kaynak alıntı id'si)
- [x] **Doğrulayıcı:** ikinci prompt, her iddiayı kaynak metinle eşler; `unsupported_claims` varsa senaryo reddedilir ve yeniden yazılır (en fazla 2 tur)
- [x] Kural tabanlı kontrol: sayı/sürüm/tarih regex ile kaynakta var mı
- [x] Başlık, açıklama, hashtag, kart/gönderi metni (X/LinkedIn) üretimi
- **Bitti:** 20 gerçek haberde elle denetim: kritik hata (yanlış sürüm, uydurma özellik) = 0.

## Faz 4 — Render (hafta 3-4)
- [x] TTS: `edge-tts` ile ses + kelime zamanlaması → SRT/ASS altyazı
- [x] Slayt şablonları (Pillow, 1080x1920): kanca, başlık, kod/diff kartı, "Sana etkisi", kaynak kartı
- [x] ffmpeg birleştirme: sahne süreleri sese göre, altyazı yakma, hafif telifsiz müzik (düşük ses)
- [x] Kod parçası görselleştirme (pygments → görsel)
- [x] Çıktı: `out/YYYY-MM-DD/<slug>/video.mp4`, `thumb.png`, `meta.json`
- **Bitti:** Tek komutla 45 sn'lik video < 2 dk'da üretiliyor; telefonda izlenebilir kalite, altyazı okunuyor.

## Faz 5 — API ve Onay (hafta 4)
- [x] Durum makinesi: `draft → verified → rendered → approved → published | rejected`
- [x] FastAPI servisi (`devbrief serve`): `/run`, `/jobs`, `/episodes*`, `/approve`, `/reject`, `/package`, `/published`, `/health` (docs/05)
- [x] Bearer token kimlik doğrulama, tek iş kilidi, callback (3 deneme)
- [x] `publish/platforms.py`: platform başına metin paketi + limit doğrulama (limitleri güncel dokümandan kontrol et)
- Not: YouTube yükleme Python'da yapılmaz; n8n YouTube düğümü kullanır (Faz 6). `publish/youtube.py` yazılmaz.
- [ ] İsteğe bağlı: `publish/storage.py` (R2/S3) — Instagram/hub için herkese açık video URL'si
- **Bitti:** `curl` ile `/run` → callback → `/episodes?status=rendered` → approve → package akışı çalışıyor; API testleri yeşil.

## Faz 6 — n8n Otomasyonu (hafta 5)
- [ ] `docker-compose` ile n8n + API ayağa kalk (dosya hazır, çalıştırma denenmedi)
- [ ] `n8n/01-daily-trigger.json` ve `02-approve-and-publish.json` içe aktar, credential'ları bağla
- [ ] Telegram onayı uçtan uca test (tek video)
- [ ] YouTube yükleme n8n YouTube düğümüyle (özel), ardından yayın merkezi ile en az 3 platform
  - Google Cloud: proje + YouTube Data API v3, OAuth izin ekranı External/Testing, kendini test kullanıcısı ekle, Web application + n8n callback URL'si
  - Testing modunda refresh token ~7 günde düşer → takvime haftalık yeniden yetkilendirme yaz; kalıcı çözüm "In production" + Google doğrulaması (güncel dokümanı kontrol et)
- [ ] Hata akışı (Error Trigger → Telegram), haftalık rapor akışı
- [ ] DB yedekleme akışı (n8n Schedule → Python `/backup` veya basit script)
- **Bitti:** 7 gün müdahalesiz çalışıyor (onay hariç), her sabah Telegram'a video geliyor, onayla basınca platformlara gidiyor.

## Faz 7 — Büyüme ve Kalite (sürekli)
- [ ] Analitik toplama (izlenme, tutma süresi) → `metrics`; haftalık "ne tuttu" raporu
- [ ] Kanca ve başlık A/B denemeleri
- [ ] Seri formatları: "Haftanın 5 önemli sürümü", "Breaking change alarmı", "30 saniyede X nedir"
- [ ] Haftalık derleme, metin bültenine dönüştürme (içeriği yeniden kullan)
- [ ] arXiv/araştırma kaynağı, topluluk öneri formu
- [ ] Hata düzeltme politikası: yanlış bilgi → sabit yorum + düzeltme notu

## Zaman çizelgesi
| Hafta | Odak | Çıktı |
|---|---|---|
| 1 | Faz 0-1 | Toplama çalışıyor |
| 2 | Faz 2 | Günlük aday listesi |
| 3 | Faz 3-4 | Senaryo + ilk video |
| 4 | Faz 4-5 | Render + API/onay |
| 5 | Faz 6 | n8n otomasyon + yayın |
| 6+ | Faz 7 | Büyüme |

## Karar noktaları
- **Hafta 2 sonu:** Seçim kalitesi <%70 ise ön puanlama ve prompt'ları iyileştir, ilerleme.
- **Hafta 4 sonu:** İlk 10 videoyu gerçekten yayınla. Mükemmeliyetçilikle geciktirme.
- **Gün 30:** Hangi format tuttu? Tutmayanı bırak.
