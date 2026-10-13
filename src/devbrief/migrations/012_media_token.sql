-- 012_media_token: bolum medya anahtari (tahmin edilemez token) + yukleme zamani (TTL).
-- Anahtar <prefix>/<token>/video.mp4 bicimindedir; slug/gun icermez.
ALTER TABLE episodes ADD COLUMN media_token TEXT;
ALTER TABLE episodes ADD COLUMN media_uploaded_at TEXT;
