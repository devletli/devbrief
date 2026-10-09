-- 001_initial: docs/02-ARCHITECTURE.md §5 + jobs tablosu (docs/05-N8N-AUTOMATION.md).
-- /run kilidi bellekte degil bu tabloda tutulur: ayni anda tek `running`.
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, source TEXT, url TEXT UNIQUE, canonical_url TEXT,
  title TEXT, summary TEXT, published_at TEXT, fetched_at TEXT,
  content_hash TEXT, raw_json TEXT, prescore REAL, status TEXT DEFAULT 'new'
);
CREATE TABLE IF NOT EXISTS classifications (
  item_id INTEGER PRIMARY KEY REFERENCES items(id), category TEXT, importance INT,
  affects_devs INT, reason TEXT, model TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS clusters (
  id INTEGER PRIMARY KEY, day TEXT, topic TEXT, score REAL, selected INT DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cluster_items (
  cluster_id INT, item_id INT, PRIMARY KEY(cluster_id, item_id)
);
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY, cluster_id INT, day TEXT, slug TEXT UNIQUE,
  status TEXT,
  script_json TEXT, verify_json TEXT, source_text_hash TEXT,
  video_path TEXT, thumb_path TEXT, title TEXT, description TEXT,
  reject_reason TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY, episode_id INT, platform TEXT, external_id TEXT,
  url TEXT, published_at TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
  publication_id INT, day TEXT, views INT, avg_view_pct REAL, likes INT,
  PRIMARY KEY(publication_id, day)
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY, ts TEXT, task TEXT, model TEXT, ok INT, ms INT, tokens INT
);
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, response TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS source_runs (
  id INTEGER PRIMARY KEY, ts TEXT, source TEXT, ok INT, n_items INT, error TEXT
);
-- Tek isci kuyrugu: yalnizca bir satir `running` olabilir (uygulama katmani denetler).
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'queued',
  stage TEXT DEFAULT '', error TEXT DEFAULT '',
  episode_ids TEXT DEFAULT '[]', callback_failed INT DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
