"""Medya deposu: bolum videosu/kapagi icin herkese acik URL (S3-uyumlu PUT).

Kapsam: Instagram/hub akislari yerel dosyayi cekemez; video+kapak S3-uyumlu
depoya (AWS S3 / Cloudflare R2 / MinIO) yuklenir, herkese acik URL dondurulur.
n8n bu URL'leri hub dugumlerine verir. DB'ye yazilmaz (yayin kaydi degil,
gecici girdi); PUT ayni anahtarla idempotenttir.

Bagimlilik yok: imza saf stdlib (hashlib/hmac) + tasma httpx ile yapilir
(boto3 bilerek eklenmedi). Yapilandirma eksikse modul devre disidir
(`storage_settings()` None doner); anahtarlar asla loglanmaz.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

import httpx

# Dosya yukleme zaman asimi (sn): video dosyalari MB'lerce olur; API/LLM
# 10-20 sn kuralindan ayrik tutulur (gerekce: tek seferlik bayt aktarimi).
UPLOAD_TIMEOUT_S = 120.0


def storage_settings() -> dict | None:
    """S3 ayari tamamsa sozluk, degilse None (modul devre disi)."""
    from devbrief.config import get_settings

    s = get_settings()
    endpoint = (s.media_s3_endpoint or "").rstrip("/")
    bucket = (s.media_s3_bucket or "").strip().strip("/")
    if not (endpoint and bucket and s.media_s3_access_key and s.media_s3_secret_key
            and s.media_s3_public_base):
        return None
    return {
        "endpoint": endpoint,
        "bucket": bucket,
        "region": (s.media_s3_region or "auto").strip() or "auto",
        "access_key": s.media_s3_access_key,
        "secret_key": s.media_s3_secret_key,
        "public_base": s.media_s3_public_base.rstrip("/"),
        "prefix": (s.media_s3_prefix or "devbrief").strip().strip("/") or "devbrief",
    }


def object_key(media_token: str, filename: str) -> str:
    """Nesne anahtari: <prefix>/<token>/<dosya> (tahmin edilemez, kararli).

    Slug/gun anahtarda gecmez; ayni token her yuklemede ayni anahtari
    verir (PUT idempotent). Token bolum kaydinda saklanir (`media_token`).
    """
    from devbrief.config import get_settings

    prefix = (get_settings().media_s3_prefix or "devbrief").strip().strip("/")
    return f"{prefix or 'devbrief'}/{media_token}/{Path(filename).name}"


def episode_media_keys(media_token: str) -> list[str]:
    """Bolumun tum nesne anahtarlari (video + kapak)."""
    return [object_key(media_token, "video.mp4"), object_key(media_token, "thumb.png")]


def ensure_media_token(conn, episode_id: int) -> str:
    """Bolumun medya tokenini dondur; yoksa uret (`secrets.token_hex(16)`)
    ve kayda yaz. Ayni token = ayni anahtar (yeniden yukleme idempotent)."""
    import secrets
    import sqlite3

    for _ in range(2):
        try:
            row = conn.execute("SELECT media_token FROM episodes WHERE id = ?",
                               (episode_id,)).fetchone()
            token = (row["media_token"] if row else None) or ""
            if token:
                return str(token)
            token = secrets.token_hex(16)  # 32 hex karakter
            with conn:
                conn.execute("UPDATE episodes SET media_token = ? WHERE id = ?",
                             (token, episode_id))
            return token
        except sqlite3.OperationalError:
            import devbrief.db as db

            db.migrate(conn)  # eski sema: kolonu ekle, bir kez tekrar dene
    raise RuntimeError("media_token yazilamadi")


def upload_gate(ep: dict, mode: str) -> tuple[bool, str]:
    """Yukleme kapisi: (izin, acik neden). Reddedilen her durum 409'luktur.

    Yalnizca onaylanmis, golge-disi, dusuk-kaynaksiz, demo-olmayan bolumler.
    `private` modda yukleme serbesttir (not ile belirtilir).
    """
    if (ep.get("status") or "") != "approved":
        return False, f"once onay gerekli (durum:{ep.get('status') or '?'})"
    if (mode or "") == "shadow":
        return False, "shadow modda yukleme kapali"
    try:
        if int(ep.get("low_source") or 0):
            return False, "low_source bolum yuklenemez"
    except (TypeError, ValueError):
        pass
    try:
        if int(ep.get("is_demo") or 0):
            return False, "demo bolum yuklenemez"
    except (TypeError, ValueError):
        pass
    return True, ""


def public_url(key: str) -> str:
    """Herkese acik URL. Yapilandirma eksikse RuntimeError."""
    cfg = storage_settings()
    if cfg is None:
        raise RuntimeError("medya depolama yapilandirilmamis (MEDIA_S3_*)")
    return f"{cfg['public_base']}/{key.lstrip('/')}"


#: Bos govde ozeti (imzasiz istekte bile kanonik istek bunu tasir).
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _aws_quote(s: str) -> str:
    return quote(str(s), safe="~")


def sigv4_headers(method: str, url: str, payload: bytes,
                  access_key: str, secret_key: str, region: str,
                  service: str = "s3", amz_date: str | None = None,
                  include_content_sha: bool = True) -> dict[str, str]:
    """AWS SigV4 imza basliklari (PUT dahil; R2 S3-uyumlu ucu da kabul eder).

    `include_content_sha=False` yalnizca bilinen-vektor dogrulama icindir;
    yuklemede her zaman govde ozeti imzalanir.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    amz_date = amz_date or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    date_stamp = amz_date[:8]
    headers = {"host": host, "x-amz-date": amz_date}
    if include_content_sha:
        headers["x-amz-content-sha256"] = hashlib.sha256(payload or b"").hexdigest()
    signed = sorted(headers)
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in signed)
    query = sorted(parse_qsl(parts.query, keep_blank_values=True))
    canonical_request = "\n".join([
        method.upper(),
        quote(parts.path or "/", safe="/~"),
        "&".join(f"{_aws_quote(k)}={_aws_quote(v)}" for k, v in query),
        canonical_headers,
        ";".join(signed),
        headers.get("x-amz-content-sha256", EMPTY_SHA256),
    ])
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    key = ("AWS4" + secret_key).encode()
    for part in (date_stamp, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    out = {"x-amz-date": amz_date,
           "Authorization": (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                             f"SignedHeaders={';'.join(signed)}, Signature={signature}")}
    if include_content_sha:
        out["x-amz-content-sha256"] = headers["x-amz-content-sha256"]
    return out


async def upload_file(local_path: str | Path, key: str, content_type: str,
                      client: httpx.AsyncClient | None = None) -> str:
    """Dosyayi depoya koy, herkese acik URL dondur. Hatada RuntimeError."""
    cfg = storage_settings()
    if cfg is None:
        raise RuntimeError("medya depolama yapilandirilmamis (MEDIA_S3_*)")
    # Windows ayraci Linux'ta tek dosya adi sayilir; duz ayrac her yerde gecer.
    src = Path(str(local_path).replace("\\", "/"))
    if not src.is_file():
        raise RuntimeError(f"yuklenecek dosya yok: {src}")
    data = src.read_bytes()
    url = f"{cfg['endpoint']}/{cfg['bucket']}/{key.lstrip('/')}"
    headers = {"Content-Type": content_type}
    headers.update(sigv4_headers("PUT", url, data, cfg["access_key"],
                                 cfg["secret_key"], cfg["region"]))
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_S)
    assert client is not None
    try:
        r = await client.put(url, content=data, headers=headers)
    except Exception as e:  # noqa: BLE001 - ag hatasi sarmalanir
        raise RuntimeError(f"medya yukleme ag hatasi: {type(e).__name__}") from e
    finally:
        if own_client:
            await client.aclose()
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"medya yukleme HTTP {r.status_code}")
    return public_url(key)


async def upload_episode_files(media_token: str, video_path: str | Path,
                               thumb_path: str | Path | None = None,
                               client: httpx.AsyncClient | None = None) -> dict:
    """Bolum medyasini token anahtariyla yukle: {"video_url", "thumb_url"|None}."""
    video_url = await upload_file(video_path, object_key(media_token, "video.mp4"),
                                  "video/mp4", client=client)
    thumb_url = None
    if thumb_path:
        thumb_url = await upload_file(thumb_path, object_key(media_token, "thumb.png"),
                                      "image/png", client=client)
    return {"video_url": video_url, "thumb_url": thumb_url}


async def delete_file(key: str, client: httpx.AsyncClient | None = None) -> bool:
    """Nesneyi sil; yoksa da basarili say (idempotent temizlik)."""
    cfg = storage_settings()
    if cfg is None:
        raise RuntimeError("medya depolama yapilandirilmamis (MEDIA_S3_*)")
    url = f"{cfg['endpoint']}/{cfg['bucket']}/{key.lstrip('/')}"
    headers = sigv4_headers("DELETE", url, b"", cfg["access_key"],
                            cfg["secret_key"], cfg["region"])
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_S)
    assert client is not None
    try:
        r = await client.request("DELETE", url, headers=headers)
    except Exception as e:  # noqa: BLE001 - ag hatasi sarmalanir
        raise RuntimeError(f"medya silme ag hatasi: {type(e).__name__}") from e
    finally:
        if own_client:
            await client.aclose()
    if r.status_code in (200, 201, 204, 404):
        return True
    raise RuntimeError(f"medya silme HTTP {r.status_code}")


def mark_uploaded(conn, episode_id: int, now_iso: str | None = None) -> None:
    """Yukleme zamanini kayda isle (TTL hesabinin tabani)."""
    import devbrief.db as db

    with conn:
        conn.execute("UPDATE episodes SET media_uploaded_at = ? WHERE id = ?",
                     (now_iso or db.now_iso(), episode_id))


def clear_uploaded(conn, episode_id: int) -> None:
    """Yukleme zamanini temizle (token korunur; anahtar kararli kalir)."""
    with conn:
        conn.execute("UPDATE episodes SET media_uploaded_at = NULL WHERE id = ?",
                     (episode_id,))


def prune_cutoff(now_iso: str, ttl_days: int) -> str:
    """TTL kesiti (ISO metin karsilastirmasi icin ayni bicimde dondurur)."""
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(now_iso) - timedelta(days=max(0, ttl_days))).isoformat()


async def prune_expired_media(conn, client: httpx.AsyncClient | None = None,
                              now_iso: str | None = None) -> dict:
    """Suresi dolan nesneleri sil: {"deleted": [anahtar], "episodes": [id],
    "errors": ["id: neden"]}. Token korunur, yukleme zamani temizlenir
    (sonraki yukleme ayni anahtari yeniden doldurur)."""
    from devbrief.config import get_settings

    if storage_settings() is None:
        raise RuntimeError("medya depolama yapilandirilmamis (MEDIA_S3_*)")
    import devbrief.db as db

    cutoff = prune_cutoff(now_iso or db.now_iso(),
                          int(get_settings().media_s3_ttl_days))
    rows = conn.execute(
        "SELECT id, media_token FROM episodes WHERE media_uploaded_at IS NOT NULL"
        " AND media_uploaded_at < ?", (cutoff,)).fetchall()
    out: dict = {"deleted": [], "episodes": [], "errors": []}
    for r in rows:
        epid, token = int(r["id"]), (r["media_token"] or "")
        if not token:
            continue
        try:
            for key in episode_media_keys(token):
                await delete_file(key, client=client)
        except RuntimeError as e:
            out["errors"].append(f"{epid}: {e}")
            continue
        clear_uploaded(conn, epid)
        out["episodes"].append(epid)
        out["deleted"].extend(episode_media_keys(token))
    return out
