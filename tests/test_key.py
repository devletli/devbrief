"""Anahtar durumu: esikler + ag hatasi + kimlik sizdirmazlik (ag mock'lu)."""

from datetime import UTC, datetime, timedelta

import httpx
import respx

from devbrief.llm import key as keymod

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _data(**over):
    d = {"limit": 2, "limit_remaining": 1.0, "usage_daily": 0, "usage_weekly": 1,
         "expires_at": "2026-12-01T00:00:00Z",
         "free_model_daily_requests": {"used": 10, "limit": 1000, "remaining": 990}}
    d.update(over)
    return d


def test_expiry_thresholds():
    assert [lv for lv, _ in keymod.assess_key(_data(), NOW) if lv != "OK"] == []
    warn = keymod.assess_key(_data(expires_at="2026-10-19T00:00:00Z"), NOW)
    assert any(lv == "WARN" and "9.5 gun" in t for lv, t in warn)
    fail = keymod.assess_key(_data(expires_at="2026-10-11T00:00:00Z"), NOW)
    assert any(lv == "FAIL" for lv, _ in fail)
    past = keymod.assess_key(_data(expires_at="2026-10-01T00:00:00Z"), NOW)
    assert any(lv == "FAIL" and "dolmus" in t for lv, t in past)
    epoch = keymod.assess_key(
        _data(expires_at=(NOW + timedelta(days=2)).timestamp()), NOW)
    assert any(lv == "FAIL" for lv, _ in epoch)


def test_quota_thresholds():
    low_credit = keymod.assess_key(_data(limit_remaining=0.1), NOW)
    assert any(lv == "WARN" and "0.25" in t for lv, t in low_credit)
    low_free = keymod.assess_key(
        _data(free_model_daily_requests={"used": 950, "limit": 1000, "remaining": 50}), NOW)
    assert any(lv == "WARN" and "100" in t for lv, t in low_free)


def test_fetch_network_error_returns_none():
    with respx.mock:
        respx.get("https://openrouter.ai/api/v1/key").mock(
            side_effect=httpx.ConnectError("down"))
        assert keymod.fetch_key_status("k") is None


def test_fetch_strips_sensitive_fields():
    body = {"data": {"limit": 1, "label": "sk-or-v1-SECRET",
                     "creator_user_id": "user_X", "workspace_id": "ws-Y",
                     "free_model_daily_requests": {"used": 1, "limit": 2, "remaining": 1}}}
    with respx.mock:
        respx.get("https://openrouter.ai/api/v1/key").mock(
            return_value=httpx.Response(200, json=body))
        out = keymod.fetch_key_status("k")
    blob = str(out)
    assert "SECRET" not in blob and "user_X" not in blob and "ws-Y" not in blob
    assert out["free_model_daily_requests"]["remaining"] == 1


def test_doctor_network_warn_not_fail(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from devbrief import cli as cli_mod
    from devbrief.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "sources.yaml").write_text("rss: []\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", "a:free")
    monkeypatch.setenv("LLM_MODELS_SMART", "s:free")
    monkeypatch.setenv("DEVBRIEF_API_TOKEN", "x")
    monkeypatch.setenv("N8N_CALLBACK_SECRET", "y")
    monkeypatch.setenv("N8N_ENCRYPTION_KEY", "z")
    monkeypatch.setattr(cli_mod, "_tool_ok", lambda *a: True)
    monkeypatch.setattr("devbrief.llm.key.fetch_key_status", lambda *a: None)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "[WARN] openrouter-key" in result.output
