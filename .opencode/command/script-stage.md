---
description: Makale çekme, senaryo yazma, doğrulama hattı
---
docs/02-ARCHITECTURE.md §7-8 ve docs/03-PROMPTS.md'ye göre `src/devbrief/script/` yaz:
- `fetch_article`: trafilatura ile ana metin, 6000 karakter kırpma, hash; çekilemezse RSS özetine düş ve episode'u `low_source` olarak işaretle.
- `schemas.py`: pydantic senaryo şeması (kelime sayısı ve sahne sayısı doğrulayıcıları dahil).
- `writer.py`: write_script, `previous_errors` ile yeniden deneme.
- `verifier.py`: önce kural tabanlı (sürüm/sayı/tarih kaynakta var mı), sonra LLM doğrulayıcı; max 2 yeniden yazım turu.
- Başarısız olursa episode `failed`, nedenini kaydet.
- CLI: `devbrief script --episode <id>`.
- Testler: kaynakta olmayan sürüm numarası içeren senaryonun reddedildiğini kanıtlayan test şart.
$ARGUMENTS
