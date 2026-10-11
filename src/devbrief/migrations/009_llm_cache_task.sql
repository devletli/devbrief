-- 009_llm_cache_task: onbellek gorev-baglantili olur + eski satirlar temizlenir.
-- Reddedilen/gecersiz yanitlar artik onbellegi kirletemez; ancak eski satirlar
-- hangi goreve ait bilinemez (anahtarda gorev yoktu). Bu yuzden metadata,
-- write_script ve verify gorevlerinin eski satirlari dahil TUMU silinir
-- (ucuz isler, ilk cagrida yeniden dolar).
ALTER TABLE llm_cache ADD COLUMN task TEXT DEFAULT '';
DELETE FROM llm_cache;
