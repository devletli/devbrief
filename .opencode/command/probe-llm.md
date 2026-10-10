---
description: Model canlilik yoklamasi (ucuz ping + teshis kaydi)
---
`devbrief probe` calistir (tum yapilandirilmis modeller, model basina 1 istek):
- Her model icin `max_tokens: 5` ping at, gecikme + HTTP durumunu
  `llm_calls` tablosuna `task="probe"` ile yaz (butceye dahildir).
- Ucretli korumasi gecerli: allowlist disi model cagrilmadan atlanir.
- Yeni tablo/sutun gerekirse migration ac; migration numarasini mevcut son
  numaranin bir fazlasi yap (su an 004).
- Cikti tablo + `devbrief report --llm` ozeti; 429/timeout yogunlugunu not et.
$ARGUMENTS
