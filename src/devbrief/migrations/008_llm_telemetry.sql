-- 008_llm_telemetry: akil yurutme tokenleri + yanit bicimi (rapor ayrimi icin).
ALTER TABLE llm_calls ADD COLUMN reasoning_tokens INT DEFAULT 0;
ALTER TABLE llm_calls ADD COLUMN response_format TEXT DEFAULT '';
