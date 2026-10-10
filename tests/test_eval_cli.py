"""eval komut davranislari: any-model + butce bitiminde kismi ozet (mock)."""

import httpx
import respx

import devbrief.db as db


def _env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "sources.yaml").write_text("rss: []\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "fa:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "sa:free")
    monkeypatch.setenv("LLM_DAILY_BUDGET", "400")
    monkeypatch.setenv("LLM_RATE_PER_MINUTE", "60000")


def _ok_response():
    return httpx.Response(200, json={
        "id": "g", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": '{"verdicts": [{"i": 0, "verdict": "entailed", "note": "ok"}]}'},
                    "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def test_any_model_runs_single_model(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    _env(monkeypatch, tmp_path)
    fixture = str(__import__("pathlib").Path(__file__).parent / "fixtures" / "verifier_cases.json")
    (tmp_path / "out").mkdir()
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=_ok_response())
        result = CliRunner().invoke(app, [
            "eval-verifier", "--cases", fixture, "--any-model", "solo:free", "--yes",
            "--out-dir", "out/ev"])
    assert result.exit_code == 0, result.output
    assert "40 vaka x 1 model" in result.output
    assert (tmp_path / "out" / "ev" / "solo_free").is_dir()


def test_budget_exhausted_writes_partial_summary(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief.cli import app

    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("LLM_DAILY_BUDGET", "1")
    conn = db.connect()
    db.migrate(conn)
    slot = db.reserve_llm_slot(conn, "onceki", "m", 1)
    db.finish_llm_slot(conn, slot, ok=True, ms=1)
    conn.close()
    fixture = str(__import__("pathlib").Path(__file__).parent / "fixtures" / "tr_paragraphs.txt")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=_ok_response())
        result = CliRunner().invoke(app, [
            "eval-models", "--file", fixture, "--only", "P1", "--yes", "--out-dir", "out/ev"])
    assert result.exit_code == 0, result.output
    assert "kismi ozet" in result.output
    assert route.call_count == 0
    assert (tmp_path / "out" / "ev" / "summary.md").exists()
