-- 011_llm_cost: OpenRouter usage.cost (kredi) takibi (docs: API Reference Responses).
-- usage.cost yoksa NULL kalir, rapor "bilinmiyor" yazar.
ALTER TABLE llm_calls ADD COLUMN cost REAL DEFAULT NULL;
