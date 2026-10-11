"""OpenRouter istemcisi: TEK LLM giris noktasi (AGENTS kural 2).

`complete_json`: prompt dosyasi yukle -> cache bak (gorev+dogrlayici
suzgeci) -> devre kesici -> butce slotu ayir -> hiz siniri -> model zinciri
(retry + fallback) -> zarf ac -> JSON cikar + pydantic dogrula (en fazla 2
somut onarim) -> gorev denetiminden gecerse cache'e yaz -> llm_calls'a isle
(status/finish_reason/repair_attempt).

Onbellek kurali: YALNIZCA sema + gorev denetimini gecmis cikti saklanir ve
kullanilir. Reddedilen/gecersiz yanit saklanmaz; eski/gecersiz satir
kullanilmaz (009 migration tumunu temizler, yeni satirlar gorev etiketli).
Onarim/yeniden uretim istekleri `cache_salt` (tur + hata hash'i) ile ayri
anahtar alir; ayni reddedilmis yanit ikinci turda onbellekten GELMEZ.

Dogrudan HTTP ile model cagirma; her sey buradan gecer. Prompt icerigi ve
anahtarlar asla loglanmaz (yalnizca hash, gorev, model, sure, format).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

import devbrief.db as db
from devbrief.config import get_settings
from devbrief.llm.ratelimit import shared as _shared_limiter

log = logging.getLogger("devbrief.llm")

BASE_URL = "https://openrouter.ai/api/v1"
PROMPTS_DIR = Path(__file__).parent / "prompts"
MAX_REPAIRS = 2
MAX_RETRIES = 2
_RETRYABLE_STATUS = (408, 409, 425, 429, 500, 502, 503, 504)
_WRAPPER_KEYS = ("script", "data", "scenario", "result", "response")
_FORMAT_HINTS = ("json_schema", "response_format", "strict")

# Devre kesici: art arda 3x 429/timeout -> 10 dk atla.
BREAKER_FAILS = 3
BREAKER_COOLDOWN_S = 600.0
_breakers: dict[str, dict] = {}


class NoModelsConfigured(RuntimeError):
    pass


class PromptNotFound(RuntimeError):
    pass


class AllModelsFailed(RuntimeError):
    pass


class CallCapExceeded(RuntimeError):
    pass


class PaidModelBlocked(RuntimeError):
    pass


class _NeedPlain(Exception):
    pass


class _NeedPlainReasoning(Exception):
    pass


class _NeedPlainRouting(Exception):
    pass


class CallCap:
    """Bolum basina toplam LLM cagrisi sinirlayici (varsayilan 8)."""

    def __init__(self, limit: int = 8):
        self.limit = limit
        self.used = 0

    def charge(self, n: int = 1) -> None:
        self.used += n
        if self.used > self.limit:
            raise CallCapExceeded(f"call_cap asildi ({self.limit})")


def reset_breakers() -> None:
    _breakers.clear()


def load_prompt(name: str) -> str:
    fname = name if name.endswith(".md") else f"{name}.md"
    path = PROMPTS_DIR / fname
    if not path.is_file():
        raise PromptNotFound(f"prompt yok: {fname}")
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, variables: dict) -> str:
    out = template
    for key, value in variables.items():
        out = out.replace("{{" + str(key) + "}}", str(value))
    leftover = re.findall(r"\{\{\w+\}\}", out)
    if leftover:
        raise ValueError(f"doldurulmamis degisken: {sorted(set(leftover))}")
    return out


def extract_json(text: str) -> object:
    t = (text or "").strip()
    if "```" in t:
        t = t[t.find("```"):]
        t = t.replace("```json", "").replace("```", "").strip()
    return json.loads(t)


def unwrap_envelope(payload: object) -> object:
    """Tek anahtarli sarmalayiciyi ac: {"script": {...}} -> {...}."""
    seen = 0
    while isinstance(payload, dict) and len(payload) == 1 and seen < 5:
        key = next(iter(payload))
        value = payload[key]
        if str(key).lower() in _WRAPPER_KEYS and isinstance(value, dict):
            payload = value
            seen += 1
        else:
            break
    return payload


def cache_key(tier: str, prompt_text: str, rendered: str, salt: str = "") -> str:
    """Onbellek anahtari. `salt` (tur + hata hash'i) yeniden uretim/onarim
    isteklerini ayri anahtara koyar; reddedilen yanit baska turda donmez."""
    h = hashlib.sha256()
    h.update(tier.encode())
    h.update(hashlib.sha256(prompt_text.encode()).digest())
    h.update(rendered.encode())
    h.update(b"\x00")
    h.update(str(salt or "").encode())
    return h.hexdigest()


def _lookup_cache(conn: sqlite3.Connection, key: str, schema: type[BaseModel],
                  task: str = ""):
    try:
        row = conn.execute(
            "SELECT response, task FROM llm_cache WHERE key = ?", (key,)).fetchone()
    except Exception:  # noqa: BLE001 - 009 oncesi sema (task kolonu yok)
        row = conn.execute(
            "SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            return schema.model_validate_json(row["response"])
        except Exception:  # noqa: BLE001 - eski cache, sema degismis olabilir
            return None
    if not row:
        return None
    try:
        if task and row["task"] and row["task"] != task:
            return None  # baska gorevin satiri bu gorevde kullanilmaz
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return schema.model_validate_json(row["response"])
    except Exception:  # noqa: BLE001 - eski cache, sema degismis olabilir
        return None


def _drop_cache(conn: sqlite3.Connection, key: str) -> None:
    try:
        with conn:
            conn.execute("DELETE FROM llm_cache WHERE key = ?", (key,))
    except Exception:  # noqa: BLE001
        pass


def _store_cache(conn: sqlite3.Connection, key: str, response_text: str,
                 task: str = "") -> None:
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, response, created_at, task)"
                " VALUES (?, ?, ?, ?)",
                (key, response_text, db.now_iso(), task),
            )
    except Exception:  # noqa: BLE001 - 009 oncesi sema: gorevsiz yaz
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, response, created_at)"
                " VALUES (?, ?, ?)",
                (key, response_text, db.now_iso()),
            )


def _retry_after_s(exc: BaseException) -> float:
    try:
        headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
        return max(0.0, float(headers.get("retry-after", 0)))
    except (TypeError, ValueError):
        return 0.0


def describe_error(exc: BaseException, payload: object) -> str:
    """Onarim turu icin somut hata listesi: alan yolu, kelime delatasi, sahne."""
    if isinstance(exc, json.JSONDecodeError):
        return f"JSON cozulemedi ({exc.msg}, satir {exc.lineno}); yalnizca JSON dondur."
    if isinstance(exc, ValidationError):
        parts = []
        for e in exc.errors()[:8]:
            loc = ".".join(str(x) for x in e.get("loc", ()))
            parts.append(f"eksik/hatali alan: {loc or '(kok)'} ({e.get('msg', '')})")
        if isinstance(payload, dict) and isinstance(payload.get("scenes"), list):
            n = sum(len(str(s.get("voice", "")).split())
                    for s in payload["scenes"] if isinstance(s, dict))
            if n < 70:
                parts.append(f"kelime: {n} (hedef 70-100, +{70 - n} gerekli)")
            elif n > 110:
                parts.append(f"kelime: {n} (ust sinir 110, -{n - 110} gerekli)")
            for s in payload["scenes"]:
                if isinstance(s, dict) and not str(s.get("on_screen", "")).strip():
                    parts.append(f"sahne {s.get('id', '?')}: on_screen bos")
        return "; ".join(parts) or str(exc)[:300]
    return f"{type(exc).__name__}: {exc}"[:300]


def _build_openai(api_key: str, timeout_s: float = 60.0):
    from openai import AsyncOpenAI

    # SDK retry kapali: retry politikasi (429 bekleme, butce) burada.
    return AsyncOpenAI(base_url=BASE_URL, api_key=api_key, timeout=timeout_s, max_retries=0)


def _status_of(exc: BaseException) -> str:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        PermissionDeniedError,
        RateLimitError,
    )

    if isinstance(exc, RateLimitError) or getattr(exc, "status_code", 0) == 429:
        return "http_429"
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return "auth"
    if isinstance(exc, APIStatusError):
        code = getattr(exc, "status_code", 0) or 0
        if code == 404 and "no endpoint" in str(exc).lower():
            return "no_endpoint_for_params"
        return "http_5xx" if code >= 500 else "http_4xx"
    if isinstance(exc, (APITimeoutError, APIConnectionError, TimeoutError)):
        return "timeout"
    return "http_4xx"


def _breaker_skipped(model: str) -> bool:
    import time

    b = _breakers.get(model)
    if b and time.monotonic() < b["skip_until"]:
        log.info("llm breaker model=%s atlaniyor", model)
        return True
    return False


def _breaker_note(model: str, exc: BaseException) -> None:
    import time

    if _status_of(exc) not in ("http_429", "timeout"):
        return
    b = _breakers.setdefault(model, {"failures": 0, "skip_until": 0.0})
    b["failures"] += 1
    if b["failures"] >= BREAKER_FAILS:
        b["skip_until"] = time.monotonic() + BREAKER_COOLDOWN_S
        log.warning("llm breaker model=%s %ds acildi", model, BREAKER_COOLDOWN_S)


def _breaker_reset(model: str) -> None:
    _breakers.pop(model, None)


_REASONING_HINTS = ("reasoning", "effort", "thinking")
_catalog_cache: dict | None = None
_catalog_failed = False


def reasoning_support(model: str) -> bool | None:
    """Model akil yurutme parametresini destekliyor mu?

    Donus: True/False (katalog biliyorsa) | None (bilinmiyorsa).
    Katalog herkese acik /models ucundan bir kez cekilir, anahtar istemez.
    """
    global _catalog_cache, _catalog_failed
    if _catalog_cache is None and not _catalog_failed:
        import httpx

        try:
            data = httpx.get("https://openrouter.ai/api/v1/models", timeout=15).json()
            _catalog_cache = {m.get("id"): m for m in data.get("data", [])}
        except Exception:  # noqa: BLE001 - katalog yoksa dene-yanil
            _catalog_failed = True
            return None
    if not _catalog_cache:
        return None
    entry = _catalog_cache.get(model)
    if not entry:
        return None
    params = entry.get("supported_parameters") or []
    if "reasoning" in params:
        return True
    if params:
        return False
    return None


def reasoning_payload(mode: str) -> dict:
    """LLM_REASONING=off|low|default -> istek govdesi parcasi (dokuman bicimi)."""
    if mode == "off":
        return {"effort": "none"}
    if mode == "low":
        return {"effort": "low"}
    return {}


async def _chat_once(client, model: str, prompt: str, temperature: float, max_tokens: int,
                     schema_json: dict | None, plain: bool, reasoning: dict | None,
                     open_routing: bool = True):
    """Tek istek; (metin, token, finish_reason, reasoning_token, format, cost).

    cost: OpenRouter `usage.cost` (kredi; dokuman: API Reference/Responses).
    Yoksa None doner, rapor "bilinmiyor" yazar.
    """
    if plain:
        response_format: dict = {"type": "json_object"}
        extra: dict = dict(reasoning or {})
        if extra:
            extra = {"reasoning": extra}
    else:
        response_format = {"type": "json_schema",
                           "json_schema": {"name": "devbrief", "schema": schema_json or {},
                                           "strict": False}}
        # Yalnizca tum parametreleri destekleyen saglayici (dokuman: provider.require_parameters).
        extra = {"provider": {"require_parameters": True}}
        if reasoning:
            extra["reasoning"] = reasoning
    if not open_routing:
        extra.pop("provider", None)
    try:
        resp = await client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=temperature, max_tokens=max_tokens, response_format=response_format,
            extra_body=extra or None,
        )
    except Exception as e:  # noqa: BLE001 - format destegi asagida denenir
        from openai import APIStatusError, BadRequestError

        msg = str(e).lower()
        if (isinstance(e, APIStatusError) and (getattr(e, "status_code", 0) or 0) == 404
                and "no endpoint" in msg and open_routing):
            raise _NeedPlainRouting from e
        if not plain and isinstance(e, BadRequestError) and any(h in msg for h in _FORMAT_HINTS):
            raise _NeedPlain from e
        if reasoning and isinstance(e, BadRequestError) and any(h in msg for h in _REASONING_HINTS):
            raise _NeedPlainReasoning from e
        raise
    try:
        choices = resp.choices or []
        msg = choices[0].message if choices else None
        text = (msg.content if msg else None) or ""
        finish = choices[0].finish_reason if choices else None
    except (IndexError, AttributeError, TypeError) as e:
        raise ValueError(f"bos/gecersiz yanit: {type(e).__name__}") from e
    try:
        tokens = int(resp.usage.total_tokens or 0)
    except (AttributeError, TypeError, ValueError):
        tokens = 0
    try:
        details = resp.usage.completion_tokens_details or {}
        rtok = int((details.get("reasoning_tokens") if isinstance(details, dict)
                    else getattr(details, "reasoning_tokens", 0)) or 0)
    except (AttributeError, TypeError, ValueError):
        rtok = 0
    return text, tokens, finish, rtok, "plain" if plain else "schema", extract_cost(resp)


def extract_cost(resp) -> float | None:
    """OpenRouter `usage.cost` (kredi). Alan adi dokumandan dogrulandi.

    SDK yaniti obje ya da dict olabilir; sayisal degilse/bossa None.
    """
    try:
        usage = getattr(resp, "usage", None)
        if usage is None and isinstance(resp, dict):
            usage = resp.get("usage")
        if usage is None:
            return None
        if isinstance(usage, dict):
            cost = usage.get("cost")
        else:
            cost = getattr(usage, "cost", None)
            if cost is None and isinstance(getattr(usage, "model_extra", None), dict):
                cost = usage.model_extra.get("cost")
        if cost is None:
            return None
        return float(cost)
    except (TypeError, ValueError, AttributeError):
        return None


async def _chat_with_retry(client, model: str, prompt: str, temperature: float, max_tokens: int,
                           schema_json: dict | None, reasoning: dict | None,
                           ) -> tuple[str, int, int, str | None, int, str, float | None]:
    """(metin, token, deneme, finish, reasoning_token, format, cost).

    Desteklenmeyen json_schema/reasoning sessizce dusurulur (loglanir).
    """
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        PermissionDeniedError,
    )

    plain = False
    use_reasoning: dict | None = dict(reasoning) if reasoning else None
    open_routing = True
    last: BaseException | None = None
    attempt = 0
    tries = 0
    while True:
        tries += 1
        try:
            text, tokens, finish, rtok, fmt, cost = await _chat_once(
                client, model, prompt, temperature, max_tokens, schema_json, plain,
                use_reasoning, open_routing)
            return text, tokens, attempt + 1, finish, rtok, fmt, cost
        except _NeedPlain:
            plain = True
            log.info("llm format model=%s json_schema yok, duz JSON", model)
            continue
        except _NeedPlainReasoning:
            use_reasoning = None
            log.info("llm reasoning model=%s desteklemiyor, parametresiz", model)
            continue
        except _NeedPlainRouting:
            plain, open_routing = True, False
            log.info("llm yonlendirme model=%s providersiz duz JSON", model)
            continue
        except (AuthenticationError, PermissionDeniedError):
            raise
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            status = getattr(e, "status_code", 0) or 0
            if isinstance(e, APIStatusError) and status not in _RETRYABLE_STATUS:
                raise
            last = e
            if attempt >= MAX_RETRIES or tries > MAX_RETRIES + 4:
                break
            wait = min(2.0**attempt, 30.0) + _retry_after_s(e)
            log.warning("llm retry model=%s attempt=%d wait=%.1f", model, attempt + 1, wait)
            await asyncio.sleep(wait)
            attempt += 1
    assert last is not None
    raise last


async def complete_json(
    task: str,
    prompt_name: str,
    variables: dict,
    schema: type[BaseModel],
    tier: Literal["fast", "smart"] = "fast",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    conn: sqlite3.Connection | None = None,
    client=None,
    rate_limit_per_min: float | None = None,
    cap: CallCap | None = None,
    timeout_s: float | None = None,
    cache_validator=None,
    cache_salt: str = "",
    cache_info: dict | None = None,
    max_repairs: int | None = None,
    model: str | None = None,
) -> BaseModel:
    """JSON ureten tek giris. Basarida dogrulanmis `schema` dondurur.

    `max_tokens` verilmezse gorev tablosundan alinir (config LLM_MAX_TOKENS_*).
    Bos yanit + finish=length ise ayni modelde 2x sinirla bir kez daha denenir.

    `model` verilirse zincir yerine YALNIZCA o model denenir (cift-model
    dogrulama icin; ucretli korumasi aynen uygulanir, izinli degilse
    PaidModelBlocked). Verilmezse tier zinciri (fallback) calisir.

    Onbellek: `cache_validator` verilen gorevde (or. metadata) YALNIZCA
    sema + gorev denetimini gecen cikti saklanir/kullanilir. Reddedilen
    yanit saklanmaz; denetimi gecemeyen hazir satir silinip gormezden
    gelinir. `cache_salt` (tur + hata hash'i) onarim/yeniden uretim
    istegine ayri anahtar verir. Basarili onbellek vurusunda `cache_info`
    sozlugune {"hit": True} yazilir (cagri izi icin).

    `max_repairs=0` ise sema/JSON hatasinda onarim denemez, ham hata
    yukari tasinir (secimli uretim: ret + yeniden uretim turu).
    """
    s = get_settings()
    if model is not None:
        if not s.model_allowed(model):
            raise PaidModelBlocked(
                f"ucretli model korumasi: {model} reddedildi "
                "(ALLOW_PAID_MODELS=true + PAID_MODELS_ALLOWLIST gerekli)")
        models = [model]
        eff_tier = s.tier_of(model)
    else:
        models = [m for m in (s.fast_models if tier == "fast" else s.smart_models)
                  if s.model_allowed(m)]
        eff_tier = tier
    if not models:
        configured = s.fast_models if tier == "fast" else s.smart_models
        if configured:
            raise PaidModelBlocked(
                f"ucretli model korumasi: {configured} reddedildi "
                "(ALLOW_PAID_MODELS=true + PAID_MODELS_ALLOWLIST gerekli)")
        raise NoModelsConfigured(f"LLM_MODELS_{tier.upper()} bos")
    if not s.openrouter_api_key and client is None:
        raise NoModelsConfigured("OPENROUTER_API_KEY bos")

    prompt_text = load_prompt(prompt_name)
    rendered = render_prompt(prompt_text, variables)
    try:
        schema_json: dict | None = schema.model_json_schema()
    except Exception:  # noqa: BLE001
        schema_json = None
    own_conn = conn is None
    if own_conn:
        conn = db.connect()
        db.migrate(conn)
    assert conn is not None
    try:
        key = cache_key(eff_tier, prompt_text, rendered, cache_salt)
        hit = _lookup_cache(conn, key, schema, task)
        if hit is not None:
            if cache_validator is not None:
                try:
                    gate_errors = cache_validator(hit) or []
                except Exception as e:  # noqa: BLE001 - dogrulayici hatasi = vurus yok
                    log.warning("llm cache-validator task=%s err=%s", task,
                                type(e).__name__)
                    gate_errors = ["validator-hatasi"]
                if gate_errors:
                    # Reddedilen satir kullanilmaz ve temizlenir.
                    log.info("llm cache-drop task=%s (%d kural)", task, len(gate_errors))
                    _drop_cache(conn, key)
                    hit = None
            if hit is not None:
                log.info("llm cache task=%s tier=%s", task, tier)
                if cache_info is not None:
                    cache_info["hit"] = True
                return hit

        limiter = _shared_limiter(f"llm-{eff_tier}", rate_limit_per_min or s.llm_rate_per_minute)
        own_client = client is None
        if own_client:
            client = _build_openai(
                s.openrouter_api_key,
                s.timeout_for(eff_tier) if timeout_s is None else timeout_s)
        limit = max_tokens if max_tokens is not None else s.max_tokens_for(task)
        reasoning = reasoning_payload(s.llm_reasoning or "default")
        try:
            errors: list[str] = []
            for model in models:
                if _breaker_skipped(model):
                    continue
                if reasoning and reasoning_support(model) is False:
                    log.info("llm reasoning model=%s desteklemiyor, parametresiz", model)
                    model_reasoning: dict | None = None
                else:
                    model_reasoning = reasoning
                await limiter.wait()
                if cap:
                    cap.charge()
                paid = not model.endswith(":free")
                slot = db.reserve_llm_slot(conn, task, model, s.llm_daily_budget)
                t0 = _now_ms()
                status: str | None = None
                finish: str | None = None
                rtok, fmt, cost = 0, "schema", None
                try:
                    text, tokens, _, finish, rtok, fmt, cost = await _chat_with_retry(
                        client, model, rendered, temperature, limit, schema_json,
                        model_reasoning)
                    text = text or ""  # bazi uclar content:null doner
                    if not text.strip() and finish == "length":
                        grown = min(limit * 2, s.llm_max_tokens_cap)
                        if grown > limit:
                            log.info("llm length model=%s limit %d->%d", model, limit, grown)
                            if cap:
                                cap.charge()
                            slot2 = db.reserve_llm_slot(
                                conn, f"{task}:length", model, s.llm_daily_budget)
                            t1 = _now_ms()
                            try:
                                (text, tokens, _, finish, rtok,
                                 fmt, cost2) = await _chat_with_retry(
                                     client, model, rendered, temperature, grown,
                                     schema_json, model_reasoning)
                                text = text or ""
                                db.finish_llm_slot(
                                    conn, slot2, ok=bool(text.strip()),
                                    ms=_now_ms() - t1, tokens=tokens, status=None,
                                    finish_reason=finish, is_paid=paid,
                                    reasoning_tokens=rtok, response_format=fmt,
                                    cost=cost2)
                            except (db.BudgetExceeded, CallCapExceeded):
                                raise
                            except Exception:
                                db.finish_llm_slot(
                                    conn, slot2, ok=False, ms=_now_ms() - t1,
                                    status="length", finish_reason=finish, is_paid=paid,
                                    reasoning_tokens=rtok, response_format=fmt)
                                raise
                    if not text.strip():
                        status = "length" if finish == "length" else "empty_content"
                        raise ValueError(f"bos yanit (finish={finish})")
                    payload: object = text
                    try:
                        payload = unwrap_envelope(extract_json(text))
                        validated = schema.model_validate(payload)
                    except (json.JSONDecodeError, ValidationError) as e:
                        if isinstance(e, json.JSONDecodeError):
                            status = "length" if finish == "length" else "invalid_json"
                        else:
                            status = "schema_error"
                        if max_repairs is not None and max_repairs <= 0:
                            raise
                        validated = await _repair(
                            conn, client, model, text, describe_error(e, payload),
                            schema, temperature, limit, task, s.llm_daily_budget,
                            limiter, cap, model_reasoning, max_repairs=max_repairs)
                    gate_errors: list[str] = []
                    if cache_validator is not None:
                        # Taze yanit denetimi gecemezse SAKLANMAZ (ret turu
                        # baska anahtarla yeniden dener; reddedilen donmez).
                        try:
                            gate_errors = list(cache_validator(validated) or [])
                        except Exception as e:  # noqa: BLE001
                            log.warning("llm cache-validator task=%s err=%s", task,
                                        type(e).__name__)
                            gate_errors = ["validator-hatasi"]
                    if not gate_errors:
                        _store_cache(conn, key, validated.model_dump_json(), task)
                    else:
                        log.info("llm cache-skip task=%s (%d kural)",
                                 task, len(gate_errors))
                    db.finish_llm_slot(conn, slot, ok=True, ms=_now_ms() - t0,
                                       tokens=tokens, status=status or "ok",
                                       finish_reason=finish, is_paid=paid,
                                       reasoning_tokens=rtok, response_format=fmt,
                                       cost=cost)
                    _breaker_reset(model)
                    log.info("llm ok task=%s model=%s ms=%d", task, model, _now_ms() - t0)
                    return validated
                except (db.BudgetExceeded, CallCapExceeded):
                    raise
                except Exception as e:  # noqa: BLE001 - siradaki modele dus
                    if status is None:
                        status = _status_of(e)
                    db.finish_llm_slot(conn, slot, ok=False, ms=_now_ms() - t0,
                                       status=status, finish_reason=finish, is_paid=paid,
                                       reasoning_tokens=rtok, response_format=fmt,
                                       cost=cost)
                    _breaker_note(model, e)
                    errors.append(f"{model}: {type(e).__name__}")
                    log.warning("llm fail task=%s model=%s err=%s %.150s",
                                task, model, type(e).__name__, str(e))
            raise AllModelsFailed(f"{task}: {'; '.join(errors)}")
        finally:
            if own_client:
                try:
                    await client.close()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        if own_conn:
            conn.close()


async def _repair(conn, client, model: str, broken: str, error: str,
                  schema: type[BaseModel], temperature: float, max_tokens: int,
                  task: str, budget: int, limiter, cap: CallCap | None,
                  reasoning: dict | None = None,
                  max_repairs: int | None = None) -> BaseModel:
    """Bozuk ciktiyi repair_json promptuyla duzelt (en fazla 2 deneme).

    `max_repairs=0` ise hic deneme yapilmaz, ham hata yukari tasinir
    (secimli uretim: ret + yeniden uretim turu, onarim cagrisi yok).
    """
    tries = MAX_REPAIRS if max_repairs is None else max(0, max_repairs)
    template = load_prompt("repair_json_v1")
    last_error = error
    last_broken = broken
    paid = not model.endswith(":free")
    for i in range(tries):
        prompt = render_prompt(
            template, {"broken": str(last_broken or "")[:4000], "error": last_error})
        await limiter.wait()
        if cap:
            cap.charge()
        slot = db.reserve_llm_slot(conn, f"{task}:repair", model, budget)
        t0 = _now_ms()
        text, finish, rtok, fmt, cost = "", None, 0, "schema", None
        try:
            text, _, _, finish, rtok, fmt, cost = await _chat_with_retry(
                client, model, prompt, 0, max_tokens, None, reasoning)
            text = text or ""
            if not text.strip():
                raise ValueError(f"bos onarim yaniti (finish={finish})")
            try:
                payload = unwrap_envelope(extract_json(text))
                validated = schema.model_validate(payload)
            except json.JSONDecodeError as je:
                raise ValueError(f"onarim JSON hatasi: {je}") from je
            db.finish_llm_slot(conn, slot, ok=True, ms=_now_ms() - t0,
                               status="ok", finish_reason=finish, repair_attempt=i + 1,
                               is_paid=paid, reasoning_tokens=rtok, response_format=fmt,
                               cost=cost)
            return validated
        except (db.BudgetExceeded, CallCapExceeded):
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "bos onarim" in msg:
                status = "length" if finish == "length" else "empty_content"
            elif "onarim JSON" in msg:
                status = "length" if finish == "length" else "invalid_json"
            elif isinstance(e, ValidationError):
                status = "schema_error"
            else:
                status = _status_of(e)
            db.finish_llm_slot(conn, slot, ok=False, ms=_now_ms() - t0,
                               status=status, finish_reason=finish, repair_attempt=i + 1,
                               is_paid=paid, reasoning_tokens=rtok, response_format=fmt,
                               cost=cost)
            last_error = describe_error(e, text)
            last_broken = text
            log.warning("llm repair task=%s model=%s try=%d err=%.120s",
                        task, model, i + 1, last_error)
    raise ValueError(f"onarim basarisiz ({tries} deneme): {last_error[:200]}")


def _now_ms() -> int:
    import time

    return int(time.perf_counter() * 1000)
