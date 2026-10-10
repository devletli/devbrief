-- 005_low_source: yetersiz kaynak cikis yolu (senaryo uretilemez, ilerlemez).
ALTER TABLE episodes ADD COLUMN low_source INT DEFAULT 0;
