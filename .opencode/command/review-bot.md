---
description: (Opsiyonel yedek) CLI onay kuyruğu, n8n kullanılıyorsa gerekmez
---
`src/devbrief/publish/review_queue.py` yaz:
- CLI: `devbrief review` bekleyen (`rendered`) bölümleri listeler; video yolu, senaryo, claim doğrulama sonuçları (özellikle `partial`), kaynak linki gösterilir; a/r/e girişi ile onayla/reddet(sebep)/düzenle.
- Telegram (opsiyonel, `TELEGRAM_BOT_TOKEN` varsa): videoyu ve özeti gönder, inline butonlar Onayla/Reddet. Sadece `TELEGRAM_CHAT_ID`'den gelen komutları kabul et.
- Durum geçişleri yalnızca `db` katmanındaki durum makinesi fonksiyonlarıyla; geçersiz geçiş hata fırlatır.
- Testler: durum makinesi ve yetkisiz chat reddi.
$ARGUMENTS
