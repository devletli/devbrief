---
description: (Opsiyonel) Elle yayın için export paketi veya doğrudan youtube modülü
---
"$ARGUMENTS" için yayın modülünü yaz.
youtube ise: OAuth (yerel token dosyası `secrets/`), resumable `videos.insert`, yalnızca `approved` bölümler, başlık/açıklama `episodes` tablosundan, kota hatasında düzgün durma, sonuç `publications` tablosuna.
export ise: `exports/<slug>/` içine mp4, title.txt, description.txt, hashtags.txt, x_post.txt, linkedin_post.txt koy.
Gerçek API'ye test yazma; arayüzü mock'la. Yükleme kodunu `--dry-run` ile test edilebilir yap.
