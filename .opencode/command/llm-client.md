---
description: OpenRouter istemcisi (retry, fallback, bütçe, cache, JSON doğrulama)
---
docs/02-ARCHITECTURE.md §4 ve AGENTS.md kural 2-3'e göre `src/devbrief/llm/client.py` yaz:
- `openai` SDK, base_url OpenRouter; modeller `.env`'deki LLM_MODELS_FAST / LLM_MODELS_SMART listelerinden.
- `complete_json(task, prompt_name, variables, schema, tier)` async fonksiyonu.
- Token bucket ile dakikalık hız sınırı (varsayılan 15 istek/dk, config'ten ayarlanır), 429'da `Retry-After`'a saygı, üstel geri çekilme, model fallback.
- `llm_cache` ve `llm_calls` tabloları, günlük bütçe sayacı ve `BudgetExceeded`.
- Prompt dosyalarını `llm/prompts/*.md` içinden yükle; docs/03-PROMPTS.md'deki promptların v1 dosyalarını oluştur.
- JSON ayıklama + pydantic doğrulama + en fazla 2 onarım denemesi.
- Testler: sahte HTTP sunucusu (respx) ile 429, bozuk JSON, fallback, cache isabeti, bütçe aşımı.
Gerçek API'ye istek atan test yazma. $ARGUMENTS
