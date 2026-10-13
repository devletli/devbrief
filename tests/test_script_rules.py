"""Dil denetimi + olgu/yorum ayrimi + failed audit satirlari (mock, ag yok).

Deterministik katmanlar GEVSETILMEDI: esikler ve katman davranisi ayni,
yalnizca surum/ozel-ad kurali kaynak-atfina izin verir (tasarim duzeltmesi).
"""

import json

from devbrief.script import verifier as ver

SRC = ("MiniDB 2.1 yayinlandi ve hiz artti. Guncelleme onerilir. "
       "Ayrica yeni API belgeleri yayinlandi.")


def _script(voice="Güncelleme yayınlandı ve hız arttı.",
            action="Kaynağı okuyup test ortamında deneyin.",
            impact="Etkisi geniştir.",
            hook="Kanca",
            claims=None):
    return {"hook": hook,
            "scenes": [{"id": 1, "voice": voice, "on_screen": "Etiket",
                        "visual": "title", "code": None, "evidence": ["e"],
                        "src": []}],
            "impact": impact, "action": action,
            "claims": claims if claims is not None else [
                {"text": "Hız arttı.", "evidence": "e", "kind": "fact"}],
            "source_url": "https://x"}


def test_english_action_rejected():
    sc = _script(action="You should update your system for better "
                        "performance with the new release")
    issues = ver.check_grounding(sc, SRC)
    hits = [i for i in issues if i.kind == "dil" and i.field == "action"]
    assert hits, [vars(i) for i in issues]
    assert "Türkçe olmalı" in hits[0].value or "Turkce olmali" in hits[0].value


def test_turkish_with_tech_term_passes():
    sc = _script(action="Breaking change içeren sürümü dikkatle incele ve test et.")
    assert ver.check_language(sc) == []


def test_action_exempt_from_coverage():
    src = ("HizliKurye 4.0 yayinlandi ve ortalama teslim suresi 25 dakikaya dustu. "
           "Kurye komisyonu yuzde 12 olarak sabitlendi. "
           "Destek ekibi gunun her saati canli yardim sunuyor.")
    unsupported = "Uydu destegi de eklendi ve cevrimdisi kullanim basladi."
    voice = ("HizliKurye 4.0 yayinlandi ve ortalama teslim suresi 25 dakikaya dustu. "
             + unsupported)
    sc = _script(voice=voice, action=unsupported,
                 claims=[{"text": "Teslim suresi dustu.", "evidence": "e",
                          "kind": "fact"}])
    issues, derived = ver.check_voice_grounding(sc, src)
    assert any(i.kind == "voice_coverage" and "voice" in i.field
               for i in issues), [vars(i) for i in issues]
    act = [d for d in derived if d["field"] == "action"]
    assert act and all(d["status"] == "yorum" for d in act)
    assert not [i for i in issues if i.field == "action"]


def test_action_unknown_version_rejected():
    sc = _script(action="Sürümü 9.9 sürümüne güncelle ve test et.")
    issues, derived = ver.check_voice_grounding(sc, SRC)
    hits = [i for i in issues if i.field == "action" and i.kind == "voice_numbers"]
    assert hits, ([vars(i) for i in issues], derived)


def test_action_known_abbreviation_passes():
    sc = _script(action="API değişikliklerini incele ve test et.")
    issues, derived = ver.check_voice_grounding(sc, SRC)
    assert not [i for i in issues if i.field == "action"]
    act = [d for d in derived if d["field"] == "action"]
    assert act and act[0]["status"] == "yorum"


def test_failed_episode_audit_rows(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    script = _script(voice="Sürüm 9.9 yayınlandı ve hız arttı.",
                     action="Kaynağı okuyup test ortamında deneyin.")
    day, slug = "2026-10-09", "ep-hatali"
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " verify_json, reject_reason, created_at, updated_at)"
            " VALUES (1, ?, ?, 'failed', ?, '', 'kural', 't', 't')",
            (day, slug, json.dumps(script, ensure_ascii=False)))
    epid = conn.execute("SELECT id FROM episodes WHERE slug = ?",
                        (slug,)).fetchone()["id"]
    conn.close()
    fetch = tmp_path / "out" / day / slug / "fetch.json"
    fetch.parent.mkdir(parents=True, exist_ok=True)
    fetch.write_text(json.dumps({"url": "https://x", "text": SRC},
                                ensure_ascii=False), encoding="utf-8")
    r = CliRunner().invoke(app, ["audit-pack", "--episode", str(epid)])
    assert r.exit_code == 0, r.output
    audit = (tmp_path / "out" / day / slug / "audit.md").read_text(encoding="utf-8")
    assert "Reddedilen" in audit
    assert "karar=red:" in audit
    assert any(t in audit for t in ("kural-hatasi", "yazar-kalitesi", "dogrulayici"))
