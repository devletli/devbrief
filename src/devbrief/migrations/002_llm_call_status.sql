-- 002_llm_call_status: cagri teshisi (docs: Faz 3 guvenilirlik).
-- status: ok|http_429|http_5xx|timeout|empty_content|invalid_json|schema_error|length|other
ALTER TABLE llm_calls ADD COLUMN status TEXT DEFAULT NULL;
ALTER TABLE llm_calls ADD COLUMN finish_reason TEXT DEFAULT NULL;
ALTER TABLE llm_calls ADD COLUMN repair_attempt INT DEFAULT 0;
