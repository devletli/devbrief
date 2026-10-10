-- 007_metadata_store: uretilmis metinler + uyarilar (onay ekraninda gosterilir).
ALTER TABLE episodes ADD COLUMN metadata_json TEXT DEFAULT '';
ALTER TABLE episodes ADD COLUMN metadata_warnings TEXT DEFAULT '[]';
