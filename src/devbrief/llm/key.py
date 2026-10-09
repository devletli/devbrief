"""OpenRouter anahtar durumu: GET /api/v1/key (olcülmeyen uc).

Anahtar, label, user/workspace kimlikleri ASLA loglanmaz ve
dondurulen sozlukten cikarilir. Ag hatasi None dondurur (cagiran WARN verir).
"""

from __future__ import annotations

from datetime import UTC, datetime


def fetch_key_status(api_key: str, timeout: float = 15.0) -> dict | None:
    """Ham `data` sozlugu (hassas alanlar cikarilmis) veya None (ag hatasi)."""
    import httpx

    try:
        r = httpx.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json().get("data") or {}
    except Exception:  # noqa: BLE001 - ag hatasi sessiz None
        return None
    keep = ("limit", "limit_remaining", "usage", "usage_daily", "usage_weekly",
            "expires_at", "free_model_daily_requests")
    out = {k: data.get(k) for k in keep}
    fm = data.get("free_model_daily_requests") or {}
    out["free_model_daily_requests"] = {
        k: fm.get(k) for k in ("used", "limit", "remaining")}
    return out


def assess_key(data: dict, now: datetime | None = None) -> list[tuple[str, str]]:
    """[(OK|WARN|FAIL, metin)]. Esikler: vade 14g WARN / 3g FAIL,
    limit_remaining < 0.25 WARN, free remaining < 100 WARN."""
    now = now or datetime.now(UTC)
    lines: list[tuple[str, str]] = []
    fm = data.get("free_model_daily_requests") or {}
    lines.append(("OK",
                  f"kredi limit={data.get('limit')} kalan={data.get('limit_remaining')} "
                  f"kullanim(gun/hafta)={data.get('usage_daily')}/{data.get('usage_weekly')}"))
    lines.append(("OK",
                  f"free gunluk: {fm.get('used')}/{fm.get('limit')} "
                  f"(kalan {fm.get('remaining')})"))
    exp = _parse_time(data.get("expires_at"))
    if exp is not None:
        days = (exp - now).total_seconds() / 86400
        if days < 0:
            lines.append(("FAIL", f"anahtar suresi dolmus ({data.get('expires_at')})"))
        elif days < 3:
            lines.append(("FAIL", f"anahtar {days:.1f} gun sonra bitiyor"))
        elif days < 14:
            lines.append(("WARN", f"anahtar {days:.1f} gun sonra bitiyor"))
        else:
            lines.append(("OK", f"anahtar vadesi: {days:.0f} gun"))
    try:
        if data.get("limit_remaining") is not None and float(data["limit_remaining"]) < 0.25:
            lines.append(("WARN", f"kredi 0.25 USD altinda ({data.get('limit_remaining')})"))
    except (TypeError, ValueError):
        pass
    try:
        if fm.get("remaining") is not None and int(fm["remaining"]) < 100:
            lines.append(("WARN", f"free gunluk kota 100 altinda ({fm.get('remaining')})"))
    except (TypeError, ValueError):
        pass
    return lines


def _parse_time(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None
