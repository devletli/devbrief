---
description: n8n akışını tasarla veya güncelle (daily-trigger, approve-and-publish, error-handler, weekly-report)
---
"$ARGUMENTS" akışı için docs/05-N8N-AUTOMATION.md ve mevcut `n8n/*.json` dosyalarını oku.
1. Akışı düğüm düğüm açıkla (kısa tablo).
2. İçe aktarılabilir geçerli JSON üret/güncelle (`n8n/<ad>.json`); `python -c "import json..."` ile JSON geçerliliğini doğrula.
3. Credential ve Config değerlerini yer tutucu bırak, sır yazma.
4. n8n sürümüne bağlı düğüm parametrelerinden emin olmadığın yerleri docs/05'in "içe aktarmadan sonra yapılacaklar" bölümüne not et.
5. İş mantığını n8n'e koyma; gerekirse Python API'ye endpoint eklenmesini öner.
