-- 013_community: topluluk onerileri + duzeltme kayitlari + A/B grubu + seri etiketi.
CREATE TABLE IF NOT EXISTS suggestions (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT DEFAULT '',
  note TEXT DEFAULT '', status TEXT DEFAULT 'new',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY, episode_id INT NOT NULL REFERENCES episodes(id),
  note TEXT NOT NULL, created_at TEXT NOT NULL
);
ALTER TABLE episodes ADD COLUMN ab_group TEXT;
ALTER TABLE episodes ADD COLUMN series TEXT;
