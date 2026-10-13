"""Kisalik onarimi + kip/cerceveleme + non_json (respx mock, gercek ag yok).

Esikler ve katmanlar aynidir (src_coverage %70, LLM dogrulama degismez);
yalnizca yeni kural katmanlari (kip/cerceveleme) ve onarim davranisi
test edilir.
"""

import httpx
import respx

import devbrief.db as db
from devbrief.config import get_settings
from devbrief.script import verifier as ver

URL = "https://openrouter.ai/api/v1/chat/completions"


def _payload(content: str) -> dict:
    return {
        "id": "gen-1", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                      "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10,
                  "completion_tokens_details": {"reasoning_tokens": 0}},
    }


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    return conn


def _env(monkeypatch, models="a:free"):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", models)
    monkeypatch.setenv("LLM_MODELS_SMART", models)
    monkeypatch.setenv("LLM_DAILY_BUDGET", "400")
    get_settings.cache_clear()


async def _client():
    import httpx as _httpx
    from openai import AsyncOpenAI as _OOAI

    http = _httpx.AsyncClient()
    return _OOAI(base_url="https://openrouter.ai/api/v1", api_key="k",
                 timeout=10.0, max_retries=0, http_client=http)


def _short_script_json() -> str:
    import json as _json

    scenes = [{"id": i + 1,
               "sentences": [{"text": "MiniDB hizlandi ve test edildi.",
                              "src": [0]},
                             {"text": "Kurulum tek komutla tamamlaniyor.",
                              "src": [1]}],
               "on_screen": "Etiket", "visual": "title", "code": None,
               "evidence": ["e"]}
              for i in range(3)]
    return _json.dumps({
        "hook": "MiniDB kisa kanca", "scenes": scenes,
        "impact": "Hiz artisi yasanir.", "action": "Surumu deneyin.",
        "source_url": "https://x",
        "claims": [{"text": "Hiz artti.", "evidence": "e"}],
        "duration_estimate_s": 40}, ensure_ascii=False)


_SRC6 = ("MiniDB 2.1 yayinda ve hiz artti. "
         "Kurulum tek komutla tamamlaniyor. "
         "Bellek kullanimi %30 azaldi. "
         "Yeni API uc ucu nokta sunuyor. "
         "Destek ekibi 7 gun yanit veriyor. "
         "Lisans ucreti 5 dolar olarak sabitlendi.")


def _numbered(src: str) -> str:
    numbered, _ = ver.number_source_sentences(src)
    return f"<KAYNAK>\n{numbered}\n</KAYNAK>"


def _call_bodies(route) -> list[str]:
    import json as _json

    out = []
    for call in route.calls:
        body = _json.loads(call.request.content.decode("utf-8"))
        msgs = body.get("messages") or []
        out.append(msgs[0].get("content", "") if msgs else "")
    return out


async def test_short_repair_lists_unused_source(monkeypatch):
    """Kisa metin onariminda kullanilmayan kaynak cumleleri (numarali) gider."""
    from devbrief.llm import client as llm
    from devbrief.script.schemas import Script

    _env(monkeypatch)
    conn = _conn()
    client = await _client()
    variables = {"source_text": _numbered(_SRC6), "source_url": "https://x",
                 "style_guide": "g", "previous_errors": "yok", "injection_note": ""}
    try:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(URL).mock(
                return_value=httpx.Response(200, json=_payload(_short_script_json())))
            try:
                await llm.complete_json(
                    "write_script", "write_script_v1", variables, Script,
                    tier="smart", temperature=0.6, conn=conn, client=client,
                    rate_limit_per_min=60000)
            except llm.AllModelsFailed:
                pass
            else:
                raise AssertionError("kisa senaryo basarili olmamali")
            # ilk uretim + 2 onarim denemesi
            assert route.call_count == 3
            bodies = _call_bodies(route)
            assert "KISA METIN ONARIMI" in bodies[1]
            assert "[4]" in bodies[1] and "7 gun" in bodies[1]
            assert "[2]" in bodies[1] and "%30" in bodies[1]
    finally:
        await client.close()
    conn.close()


async def test_no_fact_sentence_means_low_source(monkeypatch):
    """Olgu cumlesi kalmayinca onarim turu harcanmaz, bolum low_source olur."""
    from devbrief.script import writer as _w

    _env(monkeypatch)
    conn = _conn()
    client = await _client()
    src = ("MiniDB 2.1 yayinda. Hiz %40 artti. Harika bir deneyim sunar. "
           "Hemen deneyin.")
    assert ver.fact_sentence_indices(src) == [0, 1]
    import json as _json

    scenes = [{"id": i + 1,
               "sentences": [{"text": "MiniDB yayinda ve hizli.",
                              "src": [0]},
                             {"text": "Hiz artti ve test edildi.",
                              "src": [1]}],
               "on_screen": "Etiket", "visual": "title", "code": None,
               "evidence": ["e"]}
              for i in range(3)]
    short = _json.dumps({
        "hook": "MiniDB kisa kanca", "scenes": scenes,
        "impact": "Hiz artisi yasanir.", "action": "Surumu deneyin.",
        "source_url": "https://x",
        "claims": [{"text": "Hiz artti.", "evidence": "e"}],
        "duration_estimate_s": 40}, ensure_ascii=False)
    try:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(URL).mock(
                return_value=httpx.Response(200, json=_payload(short)))
            out = await _w.write_script(src, "https://x", conn=conn, client=client,
                                        rate_limit_per_min=60000)
            assert out.insufficient_source and out.reason == "kaynak yetersiz"
            # tek uretim cagrisi; onarim turu harcanmadi
            assert route.call_count == 1
            statuses = [r["status"] for r in conn.execute(
                "SELECT status FROM llm_calls ORDER BY id").fetchall()]
            assert statuses == ["short_no_source"]
    finally:
        await client.close()
    conn.close()


def _scene(sentences):
    return {"id": 1,
            "sentences": [{"text": t, "src": s} for t, s in sentences],
            "on_screen": "Etiket", "visual": "title", "code": None,
            "evidence": ["e"]}


def test_framing_rejected_without_source_basis():
    sc = {"hook": "x", "scenes": [_scene([("Bu onemli bir degisiklik.", [0])])],
          "impact": "i", "action": "a", "claims": []}
    src = "Yeni bir sayfa acildi. MiniDB 2.1 yayinda."
    issues = ver.check_scene_sources(sc, src)
    hit = [i for i in issues if i.kind == "cerceveleme"]
    assert hit, [vars(i) for i in issues]
    assert "sahne 1 cumle 1" in hit[0].value
    assert "degisiklik" in hit[0].value or "değişiklik" in hit[0].value


def test_framing_allowed_with_source_basis():
    sc = {"hook": "x", "scenes": [_scene([("Bu onemli bir degisiklik.", [0])])],
          "impact": "i", "action": "a", "claims": []}
    src = "Onemli bir degisiklik yapildi. MiniDB 2.1 yayinda."
    assert ver.check_scene_sources(sc, src) == []


def test_mood_shift_rejected():
    sc = {"hook": "x", "scenes": [_scene([("Oduller kazanilmali.", [0])])],
          "impact": "i", "action": "a", "claims": []}
    issues = ver.check_scene_sources(sc, "You may earn rewards.")
    hit = [i for i in issues if i.kind == "kip"]
    assert hit, [vars(i) for i in issues]
    assert "kazanilmali" in hit[0].value


def test_mood_kept_passes():
    sc = {"hook": "x", "scenes": [_scene([("Oduller kazanilabilir.", [0])])],
          "impact": "i", "action": "a", "claims": []}
    assert ver.check_scene_sources(sc, "You may earn rewards.") == []


def test_number_words_normalized():
    assert ver.normalize_number_words("otuz saniye ve twenty kusur") == \
        "30 saniye ve 20 kusur"
    assert ver.normalize_number_words("yirmi gun, bes kisi") == \
        "20 gun, 5 kisi"
    items = ver._sentence_items("Sure otuz saniyeye dustu.",
                                "Sure 30 saniyeye dustu.",
                                ver._content_roots("Sure 30 saniyeye dustu."))
    assert "30" in items["numbers"]


def test_explain_reject_prints_items_and_stats(monkeypatch, tmp_path):
    """explain-reject: kaynak istatistigi + src cumle + eslesen/eslesmeyen + kip."""
    import json as _json

    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    script = {"hook": "x", "source_url": "https://x",
              "scenes": [_scene([("Hiz otuz artti ve test edildi.", [0])])],
              "impact": "i", "action": "a", "claims": []}
    src = "Hiz 30 artti. MiniDB 2.1 yayinda."
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " reject_reason, created_at, updated_at)"
            " VALUES (1, '2026-10-09', 'epex', 'failed', ?, 'kural', 't', 't')",
            (_json.dumps(script, ensure_ascii=False),))
        epid = conn.execute(
            "SELECT id FROM episodes WHERE slug = 'epex'").fetchone()["id"]
    conn.close()
    fetch = tmp_path / "out" / "2026-10-09" / "epex" / "fetch.json"
    fetch.parent.mkdir(parents=True, exist_ok=True)
    fetch.write_text(_json.dumps({"url": "https://x", "text": src},
                                 ensure_ascii=False), encoding="utf-8")
    r = CliRunner().invoke(app, ["explain-reject", "--episode", str(epid)])
    assert r.exit_code == 0, r.output
    assert "kullanilabilir olgu cumlesi" in r.output
    assert "src #0" in r.output and "Hiz 30 artti" in r.output
    assert "eslesmeyen" in r.output and "kip:" in r.output


async def test_llm_status_never_empty(monkeypatch):
    """Her cagrida status yazilir (basari + onarimli akis; mock)."""
    from pydantic import BaseModel

    from devbrief.llm import client as llm

    class Item(BaseModel):
        name: str
        n: int = 0

    _env(monkeypatch)
    conn = _conn()
    client = await _client()
    try:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(URL).mock(side_effect=[
                httpx.Response(200, json=_payload("bozuk {{{")),
                httpx.Response(200, json=_payload('{"name": "x", "n": 1}')),
            ])
            out = await llm.complete_json(
                "t", "classify_batch_v1", {"items": "x"}, Item,
                tier="fast", conn=conn, client=client,
                rate_limit_per_min=60000)
            assert out.name == "x"
        rows = conn.execute("SELECT status FROM llm_calls ORDER BY id").fetchall()
        assert rows and all(r["status"] for r in rows)
    finally:
        await client.close()
    conn.close()


def test_bir_never_converts_to_digit():
    """Turkce 'bir' sayi sozcugu olarak cevrilmez (belirsizlik artigi)."""
    assert ver.normalize_number_words("onemli bir degisiklik") == \
        "onemli bir degisiklik"
    items = ver._sentence_items("Bu onemli bir degisiklik.",
                                "Bu onemli bir degisiklik.",
                                ver._content_roots("Bu onemli bir degisiklik."))
    assert items["numbers"] == []


def test_one_counts_only_in_numeric_context():
    """'one critical, two high' -> 1,2,4,7; 'no one'/'one of' sayilmaz."""
    src = "Found one critical, two high, four medium and seven low bugs."
    items = ver._sentence_items(src, src, ver._content_roots(src))
    assert items["numbers"] == ["1", "2", "4", "7"]
    assert ver.normalize_number_words("no one came") == "no one came"
    assert ver.normalize_number_words("one of them left") == "one of them left"
    assert ver.normalize_number_words("one can do it") == "one can do it"
    assert ver.normalize_number_words("fixed twenty one bugs") == \
        "fixed 20 1 bugs"


def test_short_root_residue_not_tech_item():
    """Kisa kok artigi ('say') ve sozluk-disi kokler oge olmaz."""
    sent = "Sayi her raporda belirtilmeli."
    src = "Sayi her raporda belirtilmeli ve arsivlenmeli."
    items = ver._sentence_items(sent, src, ver._content_roots(src))
    assert "say" not in items["tech"]
    assert items["tech"] == []
    good = ver._sentence_items("Cache temizlendi.",
                               "Uygulama Cache bellegini temizledi.",
                               ver._content_roots(
                                   "Uygulama Cache bellegini temizledi."))
    assert good["tech"] == ["cach"]


def test_explain_reject_mood_output(monkeypatch, tmp_path):
    """explain-reject kip: isaret, aranan liste, src cumle, eslesme kaynagi."""
    import json as _json

    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    script = {"hook": "x", "source_url": "https://x",
              "scenes": [_scene([("Cache katmanini test etmelisin ve 9 hata "
                                  "duzeltildi.", [0])])],
              "impact": "i", "action": "a", "claims": []}
    src = ("You may update now and test the Cache layer. "
           "Administrators must approve the release first.")
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " reject_reason, created_at, updated_at)"
            " VALUES (1, '2026-10-09', 'epkip', 'failed', ?, 'kural', 't', 't')",
            (_json.dumps(script, ensure_ascii=False),))
        epid = conn.execute(
            "SELECT id FROM episodes WHERE slug = 'epkip'").fetchone()["id"]
    conn.close()
    fetch = tmp_path / "out" / "2026-10-09" / "epkip" / "fetch.json"
    fetch.parent.mkdir(parents=True, exist_ok=True)
    fetch.write_text(_json.dumps({"url": "https://x", "text": src},
                                 ensure_ascii=False), encoding="utf-8")
    r = CliRunner().invoke(app, ["explain-reject", "--episode", str(epid)])
    assert r.exit_code == 0, r.output
    assert "etmelisin" in r.output  # eslesen sozcuk (kip isareti)
    assert "aranan=[must" in r.output  # src'de aranan zorunluluk listesi
    assert "You may update now" in r.output  # src cumlesi
    assert "must (#1 cumlesinden)" in r.output  # eslesme kaynagi


async def test_nondict_response_skips_repair(monkeypatch):
    """Dize yanit onarilmaz; siradaki model denenir (status non_json)."""
    from pydantic import BaseModel

    from devbrief.llm import client as llm

    class Item(BaseModel):
        name: str

    _env(monkeypatch, models="a:free,b:free")
    conn = _conn()
    client = await _client()
    try:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(URL).mock(side_effect=[
                httpx.Response(200, json=_payload('"just a string"')),
                httpx.Response(200, json=_payload('{"name": "ok"}')),
            ])
            out = await llm.complete_json(
                "t", "classify_batch_v1", {"items": "x"}, Item,
                tier="fast", conn=conn, client=client,
                rate_limit_per_min=60000)
            assert out.name == "ok"
            assert route.call_count == 2  # onarim denemesi yok
            rows = conn.execute(
                "SELECT task, ok, status FROM llm_calls ORDER BY id").fetchall()
            assert [(r["task"], r["ok"], r["status"]) for r in rows] == [
                ("t", 0, "non_json"), ("t", 1, "ok")]
            assert not [r for r in rows if "repair" in r["task"]]
    finally:
        await client.close()
    conn.close()
