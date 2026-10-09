# DevBrief TR — Proje Kuralları (opencode bu dosyayı otomatik okur)

## Proje özeti
Yazılımcılar ve teknik personel için günlük, kısa (30-60 sn) Türkçe teknik haber videoları üreten yarı otomatik pipeline.
Akış: topla → ele → seç → senaryo yaz → doğrula → render → insan onayı → yayınla → ölç.
Detaylar: `docs/02-ARCHITECTURE.md`, plan: `docs/01-ROADMAP.md`, promptlar: `docs/03-PROMPTS.md`.

## Orkestrasyon (önemli)
Tetikleme, onay (Telegram) ve çok platformlu yayın **n8n**'de; ağır işler Python'da (`devbrief serve`, FastAPI).
n8n'de iş mantığı olmaz; Python karar verir ve metin/paket üretir. API sözleşmesi: `docs/05-N8N-AUTOMATION.md`.
API kuralları: Bearer token zorunlu, `approve` yalnızca `rendered` durumunda, `/run` aynı anda tek iş (409), iş bitince callback (3 deneme).
Cron/GitHub Actions kullanılmaz; zamanlama n8n Schedule düğümündedir. CLI komutları hâlâ geliştirme ve hata ayıklama için kalır.

## Teknoloji
- Python 3.12, paket yöneticisi **uv** (`uv add`, `uv run`). pip kullanma.
- SQLite (tek dosya, `data/devbrief.db`), SQL için `sqlite3` + küçük repository katmanı. ORM yok.
- HTTP: `httpx` (async). RSS: `feedparser`. Makale gövdesi: `trafilatura`.
- LLM: OpenRouter (OpenAI uyumlu API), `openai` SDK ile `base_url=https://openrouter.ai/api/v1`.
- Doğrulama/şema: `pydantic` v2. CLI: `typer`. Log: `structlog` veya standart `logging` (JSON).
- TTS: `edge-tts` (kelime zamanlaması ile altyazı üretilir). Video: `ffmpeg` + `Pillow` ile slayt PNG'leri.
- API: `fastapi` + `uvicorn`, çıkış callback'i için `httpx`.
- Test: `pytest`, `pytest-asyncio`, `respx` (HTTP mock). Lint: `ruff`. Tip: `mypy` (gevşek).

## Dizin yapısı
```
src/devbrief/
  config.py          # pydantic-settings, .env okur
  db.py              # bağlantı, migrasyon, repository
  llm/client.py      # OpenRouter istemcisi: retry, fallback, bütçe sayacı, cache
  llm/prompts/       # *.md prompt şablonları (kodda gömülü string YOK)
  collect/           # hn.py, github_trending.py, rss.py, arxiv.py
  curate/            # dedupe.py, prefilter.py, rank.py, select.py
  script/            # writer.py, verifier.py, schemas.py
  render/            # tts.py, subtitles.py, slides.py, compose.py
  publish/           # review_queue.py, youtube.py, exporters.py
  pipeline.py        # aşama sırası (idempotent)
  api/               # FastAPI: routes.py, jobs.py, auth.py, callback.py
  publish/platforms.py  # platform başına metin paketi ve limit doğrulama
  cli.py             # typer komutları (serve dahil)
tests/ , data/ , out/ , sources.yaml , .env.example
```

## Kesin kurallar
1. **Her aşama idempotent** olmalı: aynı girdiyle tekrar çalışınca çift kayıt/çift LLM çağrısı olmamalı (içerik hash'i ile cache).
2. **Tüm LLM çağrıları `llm/client.py` üzerinden** geçer. Doğrudan HTTP ile model çağırma. İstemci: günlük bütçe sayacı (varsayılan üst sınır 400), 429/5xx için üstel geri çekilme, model fallback listesi, `llm_calls` tablosuna kayıt.
3. **LLM çıktısı daima JSON + pydantic doğrulama.** Geçersizse en fazla 2 onarım denemesi, sonra aşama `failed` işaretlenir.
4. **Uydurma yasağı:** Senaryodaki her teknik iddia kaynak metinde geçmeli. Doğrulama aşaması atlanamaz. Sürüm numarası, tarih, sayı, API adı gibi değerler kaynakla birebir eşleşmeli.
5. **Her videoda kaynak linki** (açıklama + ekranda) zorunlu.
6. **İnsan onayı olmadan yayınlama yok.** `publish` komutu yalnızca `approved` durumundaki bölümleri yükler.
7. **Sırlar** (.env) asla commit edilmez, loglanmaz. `.env.example` güncel tutulur.
8. Ağ çağrılarında timeout (10-20 sn), User-Agent belirt, robots.txt ve kaynakların koşullarına saygı göster; mümkünse RSS/resmi API kullan.
9. Prompt metinleri `llm/prompts/*.md` içinde, sürümlü (`v1`, `v2`). Değişiklik yaparsan `docs/03-PROMPTS.md` güncelle.
10. Küçük, test edilebilir fonksiyonlar. Yeni modül = en az bir test. Ağ gerektiren testler mock'lanır.

## Çalışma şekli (vibe coding için)
- Büyük işi küçük adımlara böl; her adımdan sonra `uv run pytest -q` ve `uv run ruff check .` çalıştır.
- Bir aşamayı bitirince `docs/01-ROADMAP.md` içindeki ilgili kutuyu işaretle.
- Belirsizlik varsa varsayım yapıp kodlamadan önce kısa bir soru sor.
- Türkçe içerik/prompt, İngilizce kod ve değişken adları.

## Üslup kuralları (üretilen içerik için)
- Türkçe, sade, teknik terimleri çevirme (`breaking change`, `release`, `pull request` kalabilir).
- İlk 3 saniyede kanca, sonra "Ne değişti? → Sana ne etkisi var? → Ne yapmalısın?" yapısı.
- Abartı, tıklama tuzağı, kesinlik iddiası yok. Emin olunmayan şey "kaynağa göre" diye belirtilir.
