"""Yapisal senaryo: src gonderimi + onarim mesaji + capraz-dil metrik + cap (mock)."""

import inspect

from devbrief import eval as evmod
from devbrief.pipeline import CURATE_CALLS, run_call_cap
from devbrief.script import verifier as ver


def _scenes(voices):
    return [dict(v) for v in voices]


def _script_src():
    return {"hook": "x", "scenes": _scenes([
        {"voice": "Vite 8.3.4 yayinlandi. Boyut 31 MB.",
         "on_screen": "E", "visual": "title", "code": None,
         "evidence": ["e"], "src": [0, 1]}]),
        "impact": "i", "action": "a", "claims": []}


_SRC = "Vite 8.3.4 yayinlandi. Indirme boyutu 31 MB oldu."


def test_src_valid_passes():
    assert ver.check_scene_sources(_script_src(), _SRC) == []


def test_src_concrete_message():
    sc = _script_src()
    sc["scenes"][0]["src"] = [1, 1]  # 8.3.4 ilk cumlede degil
    issues = ver.check_scene_sources(sc, _SRC)
    assert any(i.kind == "src" and "kaynak #1" in i.value for i in issues)
    text = ver.format_issues(issues)
    assert "cumle 1: '8.3.4' kaynak #1'te yok" in text
    assert text.count("kaynakta yok") == 0  # somut mesaj, ek son ek yok


def test_src_arity_and_range():
    sc = _script_src()
    sc["scenes"][0]["src"] = [0]
    assert any(i.kind == "src_arity" for i in ver.check_scene_sources(sc, _SRC))
    sc["scenes"][0]["src"] = [0, 9]
    assert any(i.kind == "src_range" for i in ver.check_scene_sources(sc, _SRC))
    sc["scenes"][0].pop("src")  # eski cikti: denetlenmez
    assert ver.check_scene_sources(sc, _SRC) == []


def test_empty_src_schema_error_and_legacy():
    from pydantic import ValidationError

    from devbrief.script.schemas import Script

    def _d(with_src):
        return {
            "hook": "Kanca", "source_url": "https://x",
            "scenes": [{"id": i + 1,
                        "voice": " ".join(["MiniDB 2.1 yayinda."] * 8),
                        "on_screen": "E", "visual": "title", "code": None,
                        "evidence": ["e"],
                        **({"src": [0] * 8} if with_src else {})}
                       for i in range(3)],
            "impact": "Etki.", "action": "Aksiyon.",
            "claims": [{"text": "2.1 yayinda.", "evidence": "e"}],
            "duration_estimate_s": 40}

    Script.model_validate(_d(True))  # yeni cikti: src dolu, gecer
    try:
        Script.model_validate(_d(False))
    except ValidationError as e:
        assert "src eksik" in str(e)
    else:
        raise AssertionError("bos src reddedilmeli")
    Script.model_validate(_d(False), context={"legacy": True})  # eski kayit gecer


def test_numberless_fabrication_caught_by_coverage():
    sc = _script_src()
    sc["scenes"][0]["voice"] = "Vite 8.3.4 yayinlandi. Bulut senkronu eklendi."
    sc["scenes"][0]["src"] = [0, 0]
    issues = ver.check_scene_sources(sc, _SRC)
    assert any(i.kind == "src_coverage" for i in issues)


def test_fusion_sentence_rejected():
    sc = {"hook": "x", "scenes": [{"voice": "Bir. Iki. Uc. Dort.",
                                   "on_screen": "E", "visual": "title",
                                   "code": None, "evidence": ["e"],
                                   "src": [0, 1, 2, 3]}],
          "impact": "i", "action": "a", "claims": []}
    src = "Birinci cumle. Ikinci cumle. Ucuncu cumle. Dorduncu cumle."
    issues = ver.check_scene_sources(sc, src)
    assert any(i.kind == "src_fusion" and "fuzyon" in i.value for i in issues)


def test_cross_metric_selection():
    src_en = "MiniDB 2.1 was released with faster rebuilds."
    sc = {"hook": "x", "scenes": [{"voice": "MiniDB 2.1 cikti ve hiz artti.",
                                   "on_screen": "E", "visual": "title",
                                   "code": None, "evidence": ["e"]}],
          "impact": "i", "action": "a", "claims": []}
    _, derived = ver.check_voice_grounding(sc, src_en)
    substantive = [d for d in derived if len(ver._content_roots(d["text"])) >= 4]
    assert substantive and substantive[0]["metric"] == "capraz-ogeler"
    src_tr = "MiniDB 2.1 cikti ve hiz artti. Surum notlari yayinlandi."
    _, derived2 = ver.check_voice_grounding(sc, src_tr)
    substantive2 = [d for d in derived2 if len(ver._content_roots(d["text"])) >= 4]
    assert substantive2 and substantive2[0]["metric"] == "kok-kapsama"


def test_cap_consistency():
    from devbrief.script.verifier import EPISODE_CALL_CAP, verify_episode

    assert EPISODE_CALL_CAP == 12
    assert inspect.signature(verify_episode).parameters["max_calls"].default == 12
    assert CURATE_CALLS == 3
    assert run_call_cap() == 3 * EPISODE_CALL_CAP + CURATE_CALLS == 39
    assert run_call_cap(2) == 2 * EPISODE_CALL_CAP + CURATE_CALLS


def test_classify_reject():
    assert evmod.classify_reject("failed", "call_cap") == "cagri-kotasi"
    assert evmod.classify_reject(
        "failed", "LLM dogrulama:\n- x") == "dogrulayici-reddi"
    assert evmod.classify_reject(
        "failed", "Kural ihlalleri:\n- x") == "yazar-kalitesi"
    assert evmod.classify_reject(
        "failed", "Dogrulama altyapi hatasi: m: Timeout") == "altyapi"
    assert evmod.classify_reject("draft", "") == "draft"
    assert evmod.classify_reject("metadata_failed", "x") == "metadata_failed"
    rows = [{"status": "failed", "reject_reason": "call_cap"},
            {"status": "failed", "reject_reason": "LLM dogrulama:\n- x"},
            {"status": "draft", "reject_reason": ""}]
    assert evmod.reject_breakdown(rows) == {
        "yazar-kalitesi": 0, "dogrulayici-reddi": 1,
        "altyapi": 0, "cagri-kotasi": 1}


async def test_writer_numbers_source(monkeypatch):
    from devbrief.script import writer as _w

    seen = {}

    async def fake_complete(task, prompt_name, variables, schema, **kw):
        seen.update(variables)
        from devbrief.script.schemas import Script

        scenes = [{"id": i + 1, "voice": " ".join(f"kelime{j}" for j in range(25)),
                   "on_screen": "Etiket", "visual": "title", "code": None,
                   "evidence": ["e"], "src": [0]} for i in range(3)]
        return Script.model_validate({
            "hook": "Kanca", "scenes": scenes, "impact": "Etki.", "action": "Aksiyon.",
            "source_url": "https://x",
            "claims": [{"text": "c.", "evidence": "e"}],
            "duration_estimate_s": 40})

    monkeypatch.setattr(_w, "complete_json", fake_complete)
    await _w.write_script("Birinci cumle. Ikinci cumle.", "https://x")
    assert "[0] Birinci cumle." in seen["source_text"]
    assert "[1] Ikinci cumle." in seen["source_text"]


def _seed_failed_episode(tmp_path):
    """Senaryolu failed bolum (reddedilen icerik hicbir akisa giremez)."""
    import json as _json

    import devbrief.db as db

    conn = db.connect(tmp_path / "data" / "devbrief.db")
    db.migrate(conn)
    script = {"hook": "Kanca", "source_url": "https://x",
              "scenes": [{"id": 1, "voice": "Kotu icerik burada.",
                          "on_screen": "E", "visual": "title", "code": None,
                          "evidence": ["e"]}],
              "impact": "E.", "action": "A.",
              "claims": [{"text": "kotu.", "evidence": "e"}],
              "duration_estimate_s": 40}
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " metadata_json, reject_reason, created_at, updated_at)"
            " VALUES (NULL, '2026-10-12', 'epfail', 'failed', ?, ?, ?, ?, ?)",
            (_json.dumps(script), _json.dumps({"title": "T"}),
             "dogrulayici-reddi", db.now_iso(), db.now_iso()))
        epid = int(cur.lastrowid)
    conn.close()
    return epid


def _cli_env_no_net(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "sources.yaml").write_text("rss: []\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_MODELS_FAST", "")
    monkeypatch.setenv("LLM_MODELS_SMART", "")


def test_failed_script_blocked_from_flows(monkeypatch, tmp_path):
    """Reddedilen senaryo render/metadata'ya giremez (statu kapisi; ag yok)."""
    from typer.testing import CliRunner

    from devbrief.cli import app
    from devbrief.pipeline import render_allowed

    _cli_env_no_net(monkeypatch, tmp_path)
    epid = _seed_failed_episode(tmp_path)
    runner = CliRunner()
    r = runner.invoke(app, ["render", "--episode", str(epid)])
    assert r.exit_code == 1 and "ilerlemez (durum:failed)" in r.output
    assert not list((tmp_path / "out").rglob("video.mp4"))
    r = runner.invoke(app, ["metadata", "--episode", str(epid)])
    assert r.exit_code == 1 and "ilerlemez (durum:failed)" in r.output
    import devbrief.db as db

    conn = db.connect(tmp_path / "data" / "devbrief.db")
    row = conn.execute("SELECT * FROM episodes WHERE id = ?", (epid,)).fetchone()
    conn.close()
    assert render_allowed(row) == "durum:failed"


def test_failed_episode_api_gates(monkeypatch, tmp_path):
    """Approve/package failed bolume 422 verir (ag yok, ASGI)."""
    import httpx
    from httpx import ASGITransport

    from devbrief.api.routes import create_app

    _cli_env_no_net(monkeypatch, tmp_path)
    monkeypatch.setenv("DEVBRIEF_API_TOKEN", "test-token")
    monkeypatch.setenv("PUBLISH_MODE", "live")
    monkeypatch.setenv("LIVE_CONFIRM", "EVET_CANLI")
    from devbrief.config import get_settings

    get_settings.cache_clear()
    epid = _seed_failed_episode(tmp_path)
    app = create_app(tmp_path / "data" / "devbrief.db")

    async def go():
        async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                     base_url="http://t") as c:
            h = {"Authorization": "Bearer test-token"}
            r1 = await c.post(f"/episodes/{epid}/approve", headers=h)
            r2 = await c.get(f"/episodes/{epid}/package", headers=h)
            return r1.status_code, r2.status_code

    import asyncio

    s1, s2 = asyncio.run(go())
    assert (s1, s2) == (422, 422)
