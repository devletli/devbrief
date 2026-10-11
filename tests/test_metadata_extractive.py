"""Extractive metadata: havuz/secim/birlestirme/assert (ag yok, mock secim)."""

import json as _json

import pytest
from pydantic import ValidationError

from devbrief.script import metadata as _mm
from devbrief.script.metadata import MetadataSelection

SRC_URL = "https://ornek.test/vite-8-3-4"


def _script():
    return {"hook": "Vite kanca",
            "source_url": SRC_URL,
            "scenes": [{"voice": "Vite 8.3.4 yama surumu yayinlandi. Indirme boyutu 31 MB."}],
            "claims": [{"text": "Vite 8.3.4 yayinda.", "kind": "fact"}],
            "impact": "Etki kucuk.",
            "action": "Surumu kur."}


_SRC = "Vite 8.3.4 yama surumu yayinlandi. Indirme boyutu 31 MB."


def _sel(**over):
    d = {"title_idx": 3, "x_idx": [1], "linkedin_idx": [1], "hashtags": []}
    d.update(over)
    return MetadataSelection(**d)


def test_build_pool_splits_and_indexes():
    pool = _mm.build_pool(_script())
    assert [p["idx"] for p in pool] == list(range(len(pool)))
    fields = [p["field"] for p in pool]
    assert fields[0] == "hook"
    assert "voice" in fields and "claim" in fields
    assert "impact" in fields and "action" in fields
    # voice iki cumleye bolunur
    voices = [p["text"] for p in pool if p["field"] == "voice"]
    assert voices == ["Vite 8.3.4 yama surumu yayinlandi.", "Indirme boyutu 31 MB."]
    # birebir tekrarlar teklenir, sira kararli
    again = _mm.build_pool(_script())
    assert pool == again
    dup = _mm.build_pool({"hook": "Ayni cumle.", "scenes": [{"voice": "Ayni cumle."}],
                          "claims": [], "impact": "", "action": ""})
    assert len(dup) == 1 and dup[0]["idx"] == 0


def test_check_selection_indices_rejects():
    pool = _mm.build_pool(_script())
    n = len(pool)
    assert _mm.check_selection_indices(_sel(), pool) == []
    errs = _mm.check_selection_indices(_sel(title_idx=99), pool)
    assert any("title_idx" in e and "99" in e for e in errs)
    errs = _mm.check_selection_indices(_sel(title_idx=-1), pool)
    assert any("title_idx" in e for e in errs)
    errs = _mm.check_selection_indices(_sel(x_idx=[0, n + 4]), pool)
    assert any("x_idx" in e for e in errs)
    with pytest.raises(ValidationError):
        _mm.MetadataSelection(title_idx=0, x_idx=[], linkedin_idx=[0], hashtags=[])


def test_parse_selection_rejects_free_text():
    sel = _mm.parse_selection(
        '{"title_idx": 0, "x_idx": [1], "linkedin_idx": [1], "hashtags": ["vite"]}')
    assert (sel.title_idx, sel.x_idx) == (0, [1])
    with pytest.raises((ValidationError, ValueError)):
        _mm.parse_selection("merhaba dunya, bugun hava guzel")
    with pytest.raises((ValidationError, ValueError)):
        _mm.parse_selection('{"title_idx": 0, "x_idx": [1], "linkedin_idx": [1],'
                            ' "hashtags": [], "ozet": "serbest metin"}')


def test_assert_pool_membership():
    pool = _mm.build_pool(_script())
    hook = pool[0]["text"]
    voice = next(p["text"] for p in pool if p["field"] == "voice")
    ok = _mm.assert_pool_membership(
        {"title": hook, "description": voice, "x_post": voice,
         "linkedin_post": voice}, pool, None)
    assert ok == []
    bad = _mm.assert_pool_membership(
        {"title": hook, "description": voice, "x_post": "Havuz disi cumle burada.",
         "linkedin_post": voice}, pool, None)
    assert len(bad) == 1 and "x_post" in bad[0] and "Havuz disi" in bad[0]
    # title_cut: hook oneki gecerli
    cut = hook.split()[0] if " " in hook else hook[:5]
    ok2 = _mm.assert_pool_membership(
        {"title": cut, "description": voice, "x_post": voice,
         "linkedin_post": voice}, pool, hook)
    assert ok2 == []
    # kesme kaynagi yoksa ayni kirpma ret
    bad2 = _mm.assert_pool_membership(
        {"title": cut, "description": voice, "x_post": voice,
         "linkedin_post": voice}, pool, None)
    if cut != hook:
        assert any("title" in e for e in bad2)


def test_resolve_title_fallback_and_cut():
    pool = [{"idx": 0, "field": "hook", "text": "x" * 100},
            {"idx": 1, "field": "voice", "text": "Kisa baslik burada."},
            {"idx": 2, "field": "claim", "text": "Diger kisa cumle burada."}]
    title, idx, cut = _mm.resolve_title(pool, 0, {"hook": "x" * 100})
    assert (title, idx, cut) == ("Kisa baslik burada.", 1, False)
    pool2 = [{"idx": 0, "field": "hook", "text": "y" * 90},
             {"idx": 1, "field": "voice", "text": "z" * 80}]
    script2 = {"hook": "Kisa hook burada duruyor simdi"}
    title2, _, cut2 = _mm.resolve_title(pool2, 0, script2)
    assert cut2 is True and len(title2) <= _mm.TITLE_LIMIT
    assert script2["hook"].startswith(title2.split()[0])


def test_filter_hashtags_vocab_and_scenario():
    """Repo kokunden calisir (sozluk dosyasi gerekir); chdir yok."""
    import devbrief.script.metadata as mm

    mm.load_hashtag_vocab.cache_clear()
    try:
        assert "vite" in mm.load_hashtag_vocab()
    finally:
        mm.load_hashtag_vocab.cache_clear()
    assert mm.filter_hashtags(["vite", "bilinmeyenxyz123", "#Vite"], _script(), _SRC) == [
        "vite", "Vite"]
    # sozlukte var, senaryoda yok -> kalir
    assert mm.filter_hashtags(["python"], _script(), _SRC) == ["python"]
    assert mm.filter_hashtags(["uzaylietiketi999"], _script(), _SRC) == []


def test_assemble_no_truncation_under_budget(monkeypatch):
    import devbrief.script.metadata as mm

    _mock_no_verify(monkeypatch)
    pool = _mm.build_pool(_script())
    sel = _sel(x_idx=[1], linkedin_idx=[1], hashtags=["vite"])
    meta, errs, warns, support = mm.assemble_extractive(
        pool, sel, _script(), _SRC)
    assert errs == [], errs
    assert not [w for w in warns if w.startswith("truncated:")]
    assert meta.title == "Vite 8.3.4 yayinda."
    assert meta.x_post.startswith("Vite 8.3.4 yama surumu yayinlandi.")
    assert all(s["verdict"] == "extractive" for s in support)
    assert all(s["basis"].startswith("pool#") for s in support)


def _mock_no_verify(monkeypatch):
    import devbrief.script.verifier as _ver

    async def boom(source_text, claims, **kw):
        raise AssertionError("extractive dogrulama LLM cagirmamali")

    monkeypatch.setattr(_ver, "verify_llm", boom)


async def test_produce_extractive_success_no_llm_verify(monkeypatch):
    """Secimli uretim: tek secim cagrisi, LLM dogrulama yok, dayanak pool#."""
    import devbrief.script.metadata as mm

    _mock_no_verify(monkeypatch)
    calls = []

    async def fake_select(pool, x_budget, linkedin_budget, **kw):
        calls.append(1)
        assert x_budget > 0 and linkedin_budget > 0
        return _sel(x_idx=[1], linkedin_idx=[1], hashtags=["vite"])

    monkeypatch.setattr(mm, "select_metadata", fake_select)
    meta, warns, budget, support = await mm.produce_extractive(_script(), _SRC)
    assert calls == [1]  # tur basina tek secim cagrisi
    assert meta.title == "Vite 8.3.4 yayinda."
    assert SRC_URL in meta.description and SRC_URL in meta.x_post
    assert all(s["basis"].startswith("pool#") for s in support)
    assert budget["platforms"]["x_post"]["limit"] == 270


async def test_produce_extractive_retries_bad_index(monkeypatch):
    import devbrief.script.metadata as mm

    _mock_verify_ok(monkeypatch)
    calls = []

    async def fake_select(pool, x_budget, linkedin_budget, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _sel(title_idx=99)
        return _sel(x_idx=[1], linkedin_idx=[1], hashtags=[])

    monkeypatch.setattr(mm, "select_metadata", fake_select)
    meta, warns, _budget, _support = await mm.produce_extractive(_script(), _SRC)
    assert len(calls) == 2
    assert meta.title == "Vite 8.3.4 yayinda."


def _mock_verify_ok(monkeypatch):
    import devbrief.script.verifier as _ver

    async def boom(source_text, claims, **kw):
        raise AssertionError("extractive dogrulama LLM cagirmamali")

    monkeypatch.setattr(_ver, "verify_llm", boom)


async def test_produce_extractive_free_text_retries_then_fails(monkeypatch):
    """Bozuk secim ciktisi: ret + tur, sonunda MetadataFailed."""
    from pydantic import ValidationError

    import devbrief.script.metadata as mm
    from devbrief.llm.client import CallCap

    _mock_verify_ok(monkeypatch)

    try:
        mm.MetadataSelection.model_validate({"title_idx": 0})
    except ValidationError as e:
        boom_err = e
    else:  # pragma: no cover - sema her zaman reddeder
        raise AssertionError("sema hatasi bekleniyordu")

    async def boom_complete(task, prompt_name, variables, schema, **kw):
        raise boom_err

    monkeypatch.setattr(mm, "complete_json", boom_complete)
    try:
        await mm.produce_extractive(_script(), _SRC, cap=CallCap(30))
    except mm.MetadataFailed as e:
        assert "secim" in str(e).lower()
    else:
        raise AssertionError("MetadataFailed bekleniyordu")


def test_select_prompt_renders_with_code_variables():
    """Prompt sablonu kodun verdigi degiskenlerle eksiksiz dolar."""
    from devbrief.llm.client import load_prompt, render_prompt

    template = load_prompt("select_metadata_v1")
    pool = _mm.build_pool(_script())
    pool_text = "\n".join(f"[{p['idx']}] ({p['field']}) {p['text']}" for p in pool)
    budgets = _mm.body_budgets(SRC_URL)
    rendered = render_prompt(template, {
        "pool": pool_text, "pool_size": str(len(pool)),
        "x_budget": str(budgets["x_post"]),
        "linkedin_budget": str(budgets["linkedin_post"]),
        "hashtag_max": "5", "previous_errors": "yok"})
    assert "[0] (hook)" in rendered
    assert "{{" not in rendered


async def test_select_metadata_sends_pool_and_schema(monkeypatch):
    import devbrief.script.metadata as mm

    seen = {}

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        seen.update(task=task, prompt_name=prompt_name,
                    pool_size=variables.get("pool_size"), schema=schema)
        assert "previous_errors" in variables
        assert kw.get("max_repairs") == 0  # onarim yok, ret + tur
        return _sel()

    monkeypatch.setattr(mm, "complete_json", fake_complete)
    pool = _mm.build_pool(_script())
    sel = await mm.select_metadata(pool, 200, 400, conn=None)
    assert sel.title_idx == 3
    assert seen["task"] == "select_metadata"
    assert seen["prompt_name"] == "select_metadata_v1"
    assert seen["pool_size"] == str(len(pool))
    assert seen["schema"] is mm.MetadataSelection


def test_generative_needs_gate_record(monkeypatch, tmp_path):
    """Kapi kaydi yoksa generative secilemez (acik hata)."""
    import asyncio as _aio

    import devbrief.db as db
    import devbrief.script.metadata as mm

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    assert db.get_eval_gate(conn, "eval-metadata") is None
    with pytest.raises(mm.MetadataFailed) as ei:
        _aio.run(mm.produce_auto(_script(), _SRC, conn=conn, mode="generative"))
    assert "eval-metadata" in str(ei.value)
    db.record_eval_gate(conn, "eval-metadata", True, 1.0, 0.0)
    rec = db.get_eval_gate(conn, "eval-metadata")
    assert rec and rec["passed"] is True
    conn.close()


def test_resolve_metadata_mode_defaults_and_errors(monkeypatch):
    import devbrief.script.metadata as mm
    from devbrief.config import get_settings

    monkeypatch.delenv("METADATA_MODE", raising=False)
    get_settings.cache_clear()
    try:
        assert mm.resolve_metadata_mode() == "extractive"
        assert mm.resolve_metadata_mode("generative") == "generative"
        assert mm.resolve_metadata_mode(" Extractive ") == "extractive"
        with pytest.raises(ValueError):
            mm.resolve_metadata_mode("bogus")
    finally:
        get_settings.cache_clear()


def test_cli_metadata_extractive_success(monkeypatch, tmp_path):
    """Varsayilan modda CLI secimli uretir (mock secim, ag yok)."""
    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app
    from devbrief.config import get_settings

    monkeypatch.delenv("METADATA_MODE", raising=False)
    get_settings.cache_clear()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " created_at, updated_at) VALUES (NULL, 'd', 's', 'verified', ?, ?, ?)",
            (_json.dumps(_script()), db.now_iso(), db.now_iso()))
    conn.close()

    import devbrief.script.metadata as mm

    async def fake_select(pool, x_budget, linkedin_budget, **kw):
        return _sel(x_idx=[1], linkedin_idx=[1], hashtags=["vite"])

    monkeypatch.setattr(mm, "select_metadata", fake_select)
    r = CliRunner().invoke(app, ["metadata", "--episode", "1"])
    assert r.exit_code == 0, r.output
    assert "metadata: episode 1 -> ok" in r.output
    conn = db.connect()
    row = conn.execute(
        "SELECT title, metadata_json FROM episodes WHERE id = 1").fetchone()
    conn.close()
    saved = _json.loads(row["metadata_json"])
    assert row["title"] == "Vite 8.3.4 yayinda."
    assert any(s["basis"].startswith("pool#") for s in saved["sentence_support"])


def test_cli_metadata_generative_without_gate_fails(monkeypatch, tmp_path):
    """Kapı kaydı yoksa --mode generative açık hatayla reddedilir."""
    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app
    from devbrief.config import get_settings

    monkeypatch.setenv("METADATA_MODE", "extractive")
    get_settings.cache_clear()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " created_at, updated_at) VALUES (NULL, 'd', 's', 'verified', ?, ?, ?)",
            (_json.dumps(_script()), db.now_iso(), db.now_iso()))
    conn.close()
    r = CliRunner().invoke(app, ["metadata", "--episode", "1", "--mode", "generative"])
    assert r.exit_code == 1
    assert "eval-metadata" in (r.output or "")
    conn = db.connect()
    row = conn.execute("SELECT status FROM episodes WHERE id = 1").fetchone()
    conn.close()
    assert row["status"] == "metadata_failed"


def test_cli_metadata_bad_mode_arg(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    with conn:
        conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " created_at, updated_at) VALUES (NULL, 'd', 's', 'verified', ?, ?, ?)",
            (_json.dumps(_script()), db.now_iso(), db.now_iso()))
    conn.close()
    r = CliRunner().invoke(app, ["metadata", "--episode", "1", "--mode", "bogus"])
    assert r.exit_code == 2
    assert "gecersiz" in (r.output or "")


async def _produce_and_save(monkeypatch, conn, epid=1):
    """Mock secimle extractive uret + kaydet; (meta, support) dondur."""
    import devbrief.script.metadata as mm

    async def fake_select(pool, x_budget, linkedin_budget, **kw):
        return _sel(x_idx=[1], linkedin_idx=[1], hashtags=["vite"])

    monkeypatch.setattr(mm, "select_metadata", fake_select)
    meta, warns, budget, support = await mm.produce_extractive(_script(), _SRC)
    mm.save_metadata(conn, epid, meta, warns, budget, support)
    return meta, support


def test_audit_pack_shows_pool_basis(monkeypatch, tmp_path):
    """Denetim ciktisinda her cumlenin pool# dayanagi gorunur."""
    import asyncio as _aio

    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    conn = db.connect()
    db.migrate(conn)
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " created_at, updated_at) VALUES (NULL, 'd', 's2', 'verified', ?, ?, ?)",
            (_json.dumps(_script()), db.now_iso(), db.now_iso()))
        epid = int(cur.lastrowid)
    _aio.run(_produce_and_save(monkeypatch, conn, epid))
    conn.close()
    r = CliRunner().invoke(app, ["audit-pack", "--episode", str(epid)])
    assert r.exit_code == 0, r.output
    body = (tmp_path / "out" / "d" / "s2" / "audit.md").read_text(encoding="utf-8")
    assert "## Metadata cumle dayanaklari" in body
    assert "pool#" in body


async def test_approval_shows_pool_basis(tmp_path, monkeypatch):
    """Onay ciktisinda (GET episode) pool# dayanaklari gorunur."""
    import httpx
    from httpx import ASGITransport

    import devbrief.db as db
    from devbrief.api.routes import create_app

    monkeypatch.setenv("DEVBRIEF_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_MODELS_FAST", "")
    monkeypatch.setenv("LLM_MODELS_SMART", "")
    monkeypatch.setenv("PUBLISH_MODE", "live")
    monkeypatch.setenv("LIVE_CONFIRM", "EVET_CANLI")
    from devbrief.config import get_settings

    get_settings.cache_clear()
    dbpath = tmp_path / "t.db"
    conn = db.connect(dbpath)
    db.migrate(conn)
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " title, description, created_at, updated_at)"
            " VALUES (NULL, 'd', 's3', 'rendered', ?, '', '', ?, ?)",
            (_json.dumps(_script()), db.now_iso(), db.now_iso()))
        epid = int(cur.lastrowid)
    await _produce_and_save(monkeypatch, conn, epid)
    conn.close()
    app = create_app(dbpath)
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                 base_url="http://t") as c:
        r = await c.get(f"/episodes/{epid}",
                        headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert "pool#" in r.json()["metadata_support_text"]


def _eval_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "sources.yaml").write_text("rss: []\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "fa:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "sa:free")
    monkeypatch.setenv("LLM_DAILY_BUDGET", "400")
    monkeypatch.setenv("LLM_RATE_PER_MINUTE", "60000")


def _verdicts_response(n, idx_verdicts=None):
    import httpx

    idx_verdicts = idx_verdicts or {}
    return httpx.Response(200, json={
        "id": "g", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": _json.dumps(
            {"verdicts": [{"i": i,
                           "verdict": idx_verdicts.get(i, "entailed"),
                           "note": "ok"} for i in range(n)],
             "overall": "pass"})},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def test_eval_extractive_runs_offline(monkeypatch, tmp_path):
    """Extractive eval: anahtar/modelsiz, ag yok; e/f/g + 24 + ornekler."""
    from pathlib import Path

    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    fx = str(Path(__file__).parent / "fixtures" / "metadata_cases.json")
    ex = str(Path(__file__).parent / "fixtures" / "metadata_extractive_cases.json")
    r = CliRunner().invoke(app, ["eval-metadata", "--mode", "extractive",
                                 "--cases", fx, "--extractive-cases", ex,
                                 "--yes", "--out-dir", "out/ev"])
    assert r.exit_code == 0, r.output
    body = (Path("out/ev/summary_metadata.md")).read_text(encoding="utf-8")
    assert "7/7" in body  # e/f/g reddi
    assert "24/24" in body  # uyelik
    assert "bozuk metin girdisi reddedilir" in body
    assert "Akicilik elle okunmali" in body
    assert "M1a" in body  # ornek cikti basligi
    assert (Path("out/ev/extractive/E1.json")).exists()
    assert (Path("out/ev/extractive/M1a.json")).exists()


def test_eval_extractive_only_and_bad_mode(monkeypatch, tmp_path):
    from pathlib import Path

    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    fx = str(Path(__file__).parent / "fixtures" / "metadata_cases.json")
    ex = str(Path(__file__).parent / "fixtures" / "metadata_extractive_cases.json")
    r = CliRunner().invoke(app, ["eval-metadata", "--mode", "extractive",
                                 "--cases", fx, "--extractive-cases", ex,
                                 "--only", "E1,M1a", "--out-dir", "out/ev"])
    assert r.exit_code == 0, r.output
    assert (Path("out/ev/extractive/E1.json")).exists()
    assert not (Path("out/ev/extractive/E2.json")).exists()
    assert (Path("out/ev/extractive/M1a.json")).exists()
    assert not (Path("out/ev/extractive/M1b.json")).exists()
    r = CliRunner().invoke(app, ["eval-metadata", "--mode", "bogus"])
    assert r.exit_code == 2
    r = CliRunner().invoke(app, ["eval-metadata", "--mode", "extractive",
                                 "--cases", fx, "--extractive-cases", ex,
                                 "--only", "YOK1"])
    assert r.exit_code == 2 and "M1a" in r.output


def test_eval_extractive_summarize_only(monkeypatch, tmp_path):
    from pathlib import Path

    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    fx = str(Path(__file__).parent / "fixtures" / "metadata_cases.json")
    ex = str(Path(__file__).parent / "fixtures" / "metadata_extractive_cases.json")
    r = CliRunner().invoke(app, ["eval-metadata", "--mode", "extractive",
                                 "--cases", fx, "--extractive-cases", ex,
                                 "--only", "E1,M1a", "--out-dir", "out/ev"])
    assert r.exit_code == 0, r.output
    (Path("out/ev/summary_metadata.md")).unlink()
    r = CliRunner().invoke(app, ["eval-metadata", "--mode", "extractive",
                                 "--cases", fx, "--extractive-cases", ex,
                                 "--summarize-only", "--out-dir", "out/ev"])
    assert r.exit_code == 0, r.output
    body = (Path("out/ev/summary_metadata.md")).read_text(encoding="utf-8")
    assert "1/1" in body


def test_eval_generative_records_gate(monkeypatch, tmp_path):
    """Generative kosu kapı sonucunu kaydeder (respx mock ag)."""
    from pathlib import Path

    import respx
    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app

    _eval_env(monkeypatch, tmp_path)
    fx = str(Path(__file__).parent / "fixtures" / "metadata_cases.json")
    (tmp_path / "out").mkdir()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://openrouter.ai/api/v1/chat/completions").mock(
            side_effect=[_verdicts_response(6), _verdicts_response(5)])
        r = CliRunner().invoke(app, ["eval-metadata", "--mode", "generative",
                                     "--cases", fx, "--only", "M1a,M1b",
                                     "--any-model", "solo:free", "--yes",
                                     "--out-dir", "out/ev"])
    assert r.exit_code == 0, r.output
    assert route.call_count == 2
    conn = db.connect()
    rec = db.get_eval_gate(conn, "eval-metadata")
    conn.close()
    assert rec is not None and rec["passed"] is True
    assert rec["recall"] == 1.0 and rec["fp_rate"] == 0.0


def test_eval_extractive_writes_no_gate_record(monkeypatch, tmp_path):
    from pathlib import Path

    from typer.testing import CliRunner

    import devbrief.db as db
    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    fx = str(Path(__file__).parent / "fixtures" / "metadata_cases.json")
    ex = str(Path(__file__).parent / "fixtures" / "metadata_extractive_cases.json")
    r = CliRunner().invoke(app, ["eval-metadata", "--mode", "extractive",
                                 "--cases", fx, "--extractive-cases", ex,
                                 "--only", "E1", "--out-dir", "out/ev"])
    assert r.exit_code == 0, r.output
    conn = db.connect()
    db.migrate(conn)
    assert db.get_eval_gate(conn, "eval-metadata") is None
    conn.close()
