-- 010_eval_gate: eval kapisi sonuclari (uretim modu kilidi icin).
-- `eval-metadata` (generative mod) her canli kosuda satiri yazar; uretimde
-- generative mod yalnizca passed=1 kaydi varsa secilebilir.
CREATE TABLE IF NOT EXISTS eval_gates (
  task TEXT PRIMARY KEY,
  passed INTEGER NOT NULL DEFAULT 0,
  recall REAL,
  fp_rate REAL,
  recorded_at TEXT NOT NULL DEFAULT ''
);
