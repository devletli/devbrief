-- 004_injection_flag: supheli cumle kaydi (hat duzeyi enjeksiyon savunmasi).
ALTER TABLE episodes ADD COLUMN injection_suspected INT DEFAULT 0;
ALTER TABLE episodes ADD COLUMN injection_removed TEXT DEFAULT '';
