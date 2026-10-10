"""Insufficient cikis yolu + render reddi (mock writer, ag yok)."""

import json

import devbrief.db as db
from devbrief.script.schemas import Script


def _insufficient():
    return Script(hook="Kanca", scenes=[{
        "id": 1, "voice": "kisa ozet", "on_screen": "Etiket", "visual": "title",
        "code": None, "evidence": ["e"]}],
        impact="Etki.", action="Aksiyon.", source_url="https://x", claims=[],
        duration_estimate_s=30, insufficient_source=True, reason="somut bilgi yok")


def test_script_marks_low_source(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app
    from devbrief.script import writer as writer_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "sources.yaml").write_text("rss: []\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "a:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "s:free")

    conn = db.connect()
    db.migrate(conn)
    conn.execute(
        "INSERT INTO episodes (cluster_id, day, slug, status, created_at, updated_at)"
        " VALUES (NULL, '2026-10-09', 'ep1', 'draft', ?, ?)", (db.now_iso(), db.now_iso()))
    conn.commit()
    conn.close()
    fetchdir = tmp_path / "out" / "2026-10-09" / "ep1"
    fetchdir.mkdir(parents=True)
    (fetchdir / "fetch.json").write_text(json.dumps(
        {"url": "https://x", "text": "Vite 8.3.4 kisa metin.", "source_hash": "h",
         "low_source": False, "reason": ""}))

    async def fake_write(*a, **k):
        return _insufficient()

    monkeypatch.setattr(writer_mod, "write_script", fake_write)
    result = CliRunner().invoke(app, ["script", "--episode", "1"])
    assert result.exit_code == 0, result.output
    assert "low_source" in result.output
    conn = db.connect()
    row = conn.execute("SELECT status, low_source FROM episodes WHERE id = 1").fetchone()
    assert row["low_source"] == 1 and row["status"] == "draft"
    conn.close()

    result = CliRunner().invoke(app, ["render", "--episode", "1"])
    assert result.exit_code == 1 and "ilerlemez" in result.output


def test_eval_verifier_any_model_mocked(monkeypatch, tmp_path):
    import httpx
    import respx
    from typer.testing import CliRunner

    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "out").mkdir()
    (tmp_path / "sources.yaml").write_text("rss: []\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "")
    monkeypatch.setenv("LLM_MODELS_SMART", "")
    monkeypatch.setenv("LLM_MODELS_VERIFY", "")
    monkeypatch.setenv("LLM_RATE_PER_MINUTE", "60000")

    payload = {"verdicts": [{"i": 0, "verdict": "entailed", "note": "ok"}],
               "overall": "pass"}

    def _resp(request):
        return httpx.Response(200, json={
            "id": "g", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant",
                        "content": json.dumps(payload)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    fixture = str(__import__("pathlib").Path(__file__).parent / "fixtures" / "verifier_cases.json")
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://openrouter.ai/api/v1/chat/completions").mock(side_effect=_resp)
        result = CliRunner().invoke(app, [
            "eval-verifier", "--cases", fixture, "--any-model", "x:free", "--yes",
            "--out-dir", "out/ev"])
    assert result.exit_code == 0, result.output
    assert "x:free" in result.output
    assert (tmp_path / "out" / "ev" / "summary_verifier.md").exists()
