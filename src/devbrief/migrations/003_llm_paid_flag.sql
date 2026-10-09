-- 003_llm_paid_flag: ucretli model korumasi denetimi icin cagri bazinda bayrak.
ALTER TABLE llm_calls ADD COLUMN is_paid INT DEFAULT 0;
