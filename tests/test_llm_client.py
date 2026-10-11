"""LLM istemcisi: sahte OpenRouter (respx), gercek ag yok."""

import httpx
import respx
from pydantic import BaseModel

import devbrief.db as db
from devbrief.config import get_settings
from devbrief.llm import client as llm

URL = "https://openrouter.ai/api/v1/chat/completions"


class Item(BaseModel):
    name: str
    n: int


def _payload(content: str, tokens: int = 10, finish: str = "stop",
               rtok: int = 0) -> dict:
    usage = {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": tokens,
             "completion_tokens_details": {"reasoning_tokens": rtok}}
    return {
        "id": "gen-1", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": finish}],
        "usage": usage,
    }


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    return conn


def _models(monkeypatch, fast="a:free,b:free", budget="400"):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_MODELS_FAST", fast)
    monkeypatch.setenv("LLM_MODELS_SMART", "s")
    monkeypatch.setenv("LLM_DAILY_BUDGET", budget)
    get_settings.cache_clear()


async def _call(conn, **kw):
    import httpx as _httpx
    from openai import AsyncOpenAI as _OOAI

    # openai v3 dahili httpx2 kullanir, respx yakalayamaz; testte duz httpx verilir.
    # Uretimde (client=None) SDK varsayilani kullanilir.
    http = _httpx.AsyncClient()
    client = _OOAI(base_url="https://openrouter.ai/api/v1", api_key="k",
                   timeout=10.0, max_retries=0, http_client=http)
    kw.setdefault("rate_limit_per_min", 60000)
    try:
        return await llm.complete_json(
            "t", "classify_batch_v1", {"items": "x"}, Item, conn=conn, client=client, **kw)
    finally:
        await client.close()


async def test_429_then_success(monkeypatch):
    _models(monkeypatch, fast="a:free")
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}, json={"error": "busy"}),
            httpx.Response(200, json=_payload('{"name": "x", "n": 1}')),
        ])
        out = await _call(conn)
        assert out == Item(name="x", n=1)
        assert route.call_count == 2
    rows = conn.execute("SELECT ok FROM llm_calls").fetchall()
    assert [r["ok"] for r in rows] == [1]


async def test_fallback_on_400(monkeypatch):
    _models(monkeypatch, fast="a:free,b:free")
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(URL).mock(side_effect=[
            httpx.Response(400, json={"error": "bad"}),
            httpx.Response(200, json=_payload('{"name": "y", "n": 2}')),
        ])
        out = await _call(conn)
        assert out.name == "y"
    rows = conn.execute("SELECT model, ok FROM llm_calls ORDER BY id").fetchall()
    assert [(r["model"], r["ok"]) for r in rows] == [("a:free", 0), ("b:free", 1)]


async def test_broken_json_repaired(monkeypatch):
    _models(monkeypatch, fast="a:free")
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(side_effect=[
            httpx.Response(200, json=_payload("bozuk {{{")),
            httpx.Response(200, json=_payload('{"name": "z", "n": 3}')),
        ])
        out = await _call(conn)
        assert out.n == 3
        assert route.call_count == 2


async def test_cache_hit_no_extra_call(monkeypatch):
    _models(monkeypatch, fast="a:free")
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(
            return_value=httpx.Response(200, json=_payload('{"name": "c", "n": 4}')))
        await _call(conn)
        await _call(conn)
        assert route.call_count == 1
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 1


async def test_budget_exceeded(monkeypatch):
    _models(monkeypatch, fast="a:free", budget="1")
    conn = _conn()
    slot = db.reserve_llm_slot(conn, "onceki", "a", 1)
    db.finish_llm_slot(conn, slot, ok=True, ms=1)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(
            return_value=httpx.Response(200, json=_payload('{"name": "q", "n": 5}')))
        try:
            await _call(conn)
        except db.BudgetExceeded:
            pass
        else:
            raise AssertionError("BudgetExceeded bekleniyordu")
        assert route.call_count == 0


def test_unwrap_envelope():
    assert llm.unwrap_envelope({"script": {"a": 1}}) == {"a": 1}
    assert llm.unwrap_envelope({"data": {"data": {"a": 1}}}) == {"a": 1}
    assert llm.unwrap_envelope({"a": 1, "b": 2}) == {"a": 1, "b": 2}
    assert llm.unwrap_envelope([1, 2]) == [1, 2]
    assert llm.unwrap_envelope({"other": {"a": 1}}) == {"other": {"a": 1}}


async def test_wrapped_output_validates(monkeypatch):
    _models(monkeypatch, fast="a:free")
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(URL).mock(
            return_value=httpx.Response(200, json=_payload('{"script": {"name": "w", "n": 6}}')))
        out = await _call(conn)
        assert out == Item(name="w", n=6)


async def test_call_cap_blocks_before_http(monkeypatch):
    _models(monkeypatch, fast="a:free")
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(
            return_value=httpx.Response(200, json=_payload('{"name": "q", "n": 5}')))
        try:
            await _call(conn, cap=llm.CallCap(0))
        except llm.CallCapExceeded:
            pass
        else:
            raise AssertionError("CallCapExceeded bekleniyordu")
        assert route.call_count == 0


async def test_breaker_skips_after_3_timeouts(monkeypatch):
    _models(monkeypatch, fast="a:free,b:free")
    monkeypatch.setattr(llm, "MAX_RETRIES", 0)
    llm.reset_breakers()
    conn = _conn()
    try:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(URL).mock(side_effect=httpx.ConnectError("down"))
            for _ in range(3):
                try:
                    await _call(conn)
                except llm.AllModelsFailed:
                    pass
            assert route.call_count == 3 * 2  # her model denendi
            try:
                await _call(conn)
            except llm.AllModelsFailed:
                pass
            # Iki model de devrede disi: yeni HTTP yok.
            assert route.call_count == 3 * 2
    finally:
        llm.reset_breakers()


async def test_status_columns_written(monkeypatch):
    _models(monkeypatch, fast="a:free")
    monkeypatch.setattr(llm, "MAX_RETRIES", 0)
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(URL).mock(return_value=httpx.Response(429, json={"error": "busy"}))
        monkeypatch.setattr(llm, "MAX_RETRIES", 0)
        try:
            await _call(conn)
        except llm.AllModelsFailed:
            pass
    row = conn.execute("SELECT ok, status, repair_attempt FROM llm_calls").fetchone()
    assert (row["ok"], row["status"], row["repair_attempt"]) == (0, "http_429", 0)


async def test_paid_model_blocked(monkeypatch):
    _models(monkeypatch, fast="ucretli-model")
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(
            return_value=httpx.Response(200, json=_payload('{"name": "q", "n": 5}')))
        try:
            await _call(conn)
        except llm.PaidModelBlocked as e:
            assert "ALLOW_PAID_MODELS" in str(e)
        else:
            raise AssertionError("PaidModelBlocked bekleniyordu")
        assert route.call_count == 0
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0


async def test_paid_allowlist_permits_and_flags(monkeypatch):
    _models(monkeypatch, fast="ucretli-model")
    monkeypatch.setenv("ALLOW_PAID_MODELS", "true")
    monkeypatch.setenv("PAID_MODELS_ALLOWLIST", "ucretli-model")
    get_settings.cache_clear()
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(URL).mock(
            return_value=httpx.Response(200, json=_payload('{"name": "p", "n": 7}')))
        out = await _call(conn)
        assert out == Item(name="p", n=7)
    row = conn.execute("SELECT ok, is_paid FROM llm_calls").fetchone()
    assert (row["ok"], row["is_paid"]) == (1, 1)


async def test_paid_allowlist_missing_still_blocked(monkeypatch):
    _models(monkeypatch, fast="baska-model")
    monkeypatch.setenv("ALLOW_PAID_MODELS", "true")
    monkeypatch.setenv("PAID_MODELS_ALLOWLIST", "ucretli-model")
    get_settings.cache_clear()
    conn = _conn()
    try:
        await _call(conn)
    except llm.PaidModelBlocked:
        pass
    else:
        raise AssertionError("PaidModelBlocked bekleniyordu")


async def test_length_grows_limit_once(monkeypatch):
    _models(monkeypatch, fast="a:free")
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(side_effect=[
            httpx.Response(200, json=_payload("", finish="length")),
            httpx.Response(200, json=_payload('{"name": "g", "n": 1}')),
        ])
        out = await _call(conn)
        assert out == Item(name="g", n=1)
        assert route.call_count == 2
    rows = conn.execute("SELECT task, ok FROM llm_calls ORDER BY id").fetchall()
    assert [r["task"] for r in rows] == ["t", "t:length"]
    assert all(r["ok"] == 1 for r in rows)


async def test_length_twice_falls_to_next_model(monkeypatch):
    _models(monkeypatch, fast="a:free,b:free")
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(side_effect=[
            httpx.Response(200, json=_payload("", finish="length")),
            httpx.Response(200, json=_payload("", finish="length")),
            httpx.Response(200, json=_payload('{"name": "h", "n": 2}')),
        ])
        out = await _call(conn)
        assert out == Item(name="h", n=2)
        assert route.call_count == 3


async def test_no_endpoint_for_params_status(monkeypatch):
    _models(monkeypatch, fast="a:free")
    monkeypatch.setattr(llm, "MAX_RETRIES", 0)
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(URL).mock(return_value=httpx.Response(404, json={
            "error": {"message": "No endpoints found that support all parameters",
                      "code": 404}}))
        try:
            await _call(conn)
        except llm.AllModelsFailed:
            pass
    row = conn.execute("SELECT status FROM llm_calls").fetchone()
    assert row["status"] == "no_endpoint_for_params"


async def test_no_endpoint_falls_back_to_open_routing(monkeypatch):
    _models(monkeypatch, fast="a:free")
    conn = _conn()
    seen = []
    with respx.mock(assert_all_called=False) as mock:
        def _cap(request):
            import json as _j

            body = _j.loads(request.content)
            seen.append("provider" in body)
            if len(seen) == 1:
                return httpx.Response(404, json={
                    "error": {"message": "No endpoints found that support all parameters",
                              "code": 404}})
            return httpx.Response(200, json=_payload('{"name": "r", "n": 1}'))

        mock.post(URL).mock(side_effect=_cap)
        out = await _call(conn)
        assert out == Item(name="r", n=1)
    assert seen == [True, False]
    fmt = conn.execute("SELECT response_format FROM llm_calls ORDER BY id DESC LIMIT 1"
                       ).fetchone()["response_format"]
    assert fmt == "plain"


def test_reasoning_payload_and_support(monkeypatch):
    assert llm.reasoning_payload("off") == {"effort": "none"}
    assert llm.reasoning_payload("low") == {"effort": "low"}
    assert llm.reasoning_payload("default") == {}
    body = {"data": [
        {"id": "a:free", "supported_parameters": ["temperature", "reasoning"]},
        {"id": "b:free", "supported_parameters": ["temperature"]},
    ]}
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://openrouter.ai/api/v1/models").mock(
            return_value=httpx.Response(200, json=body))
        llm._catalog_cache = None
        llm._catalog_failed = False
        assert llm.reasoning_support("a:free") is True
        assert llm.reasoning_support("b:free") is False
        assert llm.reasoning_support("yok:free") is None
    llm._catalog_cache = None
    llm._catalog_failed = False


async def test_reasoning_tokens_recorded(monkeypatch):
    _models(monkeypatch, fast="a:free")
    conn = _conn()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(URL).mock(
            return_value=httpx.Response(200, json=_payload('{"name": "r", "n": 9}', rtok=42)))
        await _call(conn)
    row = conn.execute("SELECT reasoning_tokens, response_format FROM llm_calls").fetchone()
    assert row["reasoning_tokens"] == 42
    assert row["response_format"] in ("schema", "plain")
