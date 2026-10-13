"""FastAPI: n8n sozlesmesi (docs/05). `/health` acik, gerisi Bearer korumali."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response

from devbrief.api import jobs as _jobs
from devbrief.api.auth import require_token
from devbrief.publish import platforms as _plat

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class RangedFileResponse(Response):
    """Tek aralikli Range destegi (206) + normal 200."""

    def __init__(self, path: str | Path, media_type: str, range_header: str | None = None):
        self.path = Path(path)
        size = self.path.stat().st_size
        headers = {"Accept-Ranges": "bytes"}
        if range_header:
            m = _RANGE.fullmatch((range_header or "").strip())
            if m:
                start = int(m.group(1) or 0)
                end = int(m.group(2) or size - 1)
                if start >= size:
                    super().__init__(status_code=416, headers=headers)
                    return
                end = min(end, size - 1)
                with open(self.path, "rb") as f:
                    f.seek(start)
                    body = f.read(end - start + 1)
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
                super().__init__(content=body, status_code=206, headers=headers,
                                 media_type=media_type)
                return
        super().__init__(content=self.path.read_bytes(), status_code=200,
                         headers=headers, media_type=media_type)


def _db_path(request: Request) -> str:
    return str(request.app.state.db_path)


def _row_episode(conn, episode_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
    return dict(row) if row else None


def _claim_summary(ep: dict) -> tuple[str, int, int]:
    try:
        script = json.loads(ep.get("script_json") or "{}")
    except ValueError:
        return "", 0, 0
    claims = script.get("claims") or []
    first = str((claims[0].get("text") or "")[:160]) if claims else ""
    inf = sum(1 for c in claims if isinstance(c, dict) and c.get("kind") == "inference")
    return first, len(claims), inf


def _source_url(ep: dict) -> str:
    try:
        return str(json.loads(ep.get("script_json") or "{}").get("source_url") or "")
    except ValueError:
        return ""


def create_app(db_path: str | Path = "data/devbrief.db") -> FastAPI:
    from contextlib import asynccontextmanager

    import devbrief.db as db

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _jobs.recover(str(app.state.db_path))
        yield

    app = FastAPI(title="devbrief", lifespan=lifespan)
    app.state.db_path = str(db_path)

    @app.get("/health")
    async def health(request: Request):
        import logging as _logging

        from devbrief.config import get_settings as _gs

        conn = db.connect(_db_path(request))
        try:
            mode, warn = _gs().effective_mode()
            if warn:
                _logging.getLogger("devbrief.api").warning("publish mode: %s", warn)
            return {"ok": True, "llm_today": db.count_llm_calls_today(conn),
                    "publish_mode": mode}
        finally:
            conn.close()

    @app.post("/run", status_code=202)
    async def run(request: Request, body: dict, _t: None = Depends(require_token)):
        import asyncio

        from devbrief import pipeline as _pipeline

        job_id = _jobs.acquire(_db_path(request))
        if job_id is None:
            raise HTTPException(status_code=409, detail="zaten is var")
        asyncio.create_task(_jobs.run_job(
            _db_path(request), job_id, _pipeline.make_stages(),
            str(body.get("callback_url") or ""), int(body.get("max_episodes") or 3)))
        return {"job_id": job_id}

    @app.get("/jobs/{job_id}")
    async def job(request: Request, job_id: int, _t: None = Depends(require_token)):
        row = _jobs.get(_db_path(request), job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="job yok")
        return {"status": row["status"], "stage": row["stage"], "error": row["error"],
                "episode_ids": json.loads(row.get("episode_ids") or "[]")}

    @app.get("/episodes")
    async def episodes(request: Request, status: str = Query("rendered"),
                       _t: None = Depends(require_token)):
        from devbrief.config import get_settings as _gs

        mode, _ = _gs().effective_mode()
        conn = db.connect(_db_path(request))
        try:
            rows = conn.execute(
                "SELECT * FROM episodes WHERE status = ? ORDER BY id DESC LIMIT 50",
                (status,)).fetchall()
            out = []
            for r in rows:
                ep = dict(r)
                first, n_claims, n_inf = _claim_summary(ep)
                out.append({"id": ep["id"], "slug": ep["slug"], "title": ep["title"],
                            "source_url": _source_url(ep),
                            "claim_ozeti": first, "claim_sayisi": n_claims,
                            "yorum_sayisi": n_inf,
                            "injection_suspected": ep.get("injection_suspected", 0) or 0,
                            "low_source": ep.get("low_source", 0) or 0,
                            "is_demo": ep.get("is_demo", 0) or 0,
                            "publish_mode": mode})
            return out
        finally:
            conn.close()

    @app.get("/episodes/{episode_id}")
    async def episode(request: Request, episode_id: int, _t: None = Depends(require_token)):
        from devbrief.config import get_settings as _gs

        mode, _ = _gs().effective_mode()
        conn = db.connect(_db_path(request))
        try:
            ep = _row_episode(conn, episode_id)
            if ep is None:
                raise HTTPException(status_code=404, detail="episode yok")
            script, verify = _parse_json(ep.get("script_json")), _parse_json(ep.get("verify_json"))
            meta = _parse_json(ep.get("metadata_json"))
            try:
                meta_warns = json.loads(ep.get("metadata_warnings") or "[]")
            except ValueError:
                meta_warns = []
            sup = meta.get("sentence_support") or [] if isinstance(meta, dict) else []
            sup_lines = []
            for s in sup[:12]:
                if isinstance(s, dict):
                    sup_lines.append(
                        f"[{s.get('field', '?')}] {(s.get('sentence') or '')[:100]}"
                        f" -> {s.get('basis', '')}")
            warns, infs = _approval_info(script, verify)
            return {**ep, "script": script, "verify": verify, "warnings": warns,
                    "yorumlar": infs, "metadata": meta, "metadata_warnings": meta_warns,
                    "metadata_support_text": "\n".join(sup_lines),
                    "publish_mode": mode}
        finally:
            conn.close()

    @app.get("/episodes/{episode_id}/video")
    async def video(request: Request, episode_id: int, _t: None = Depends(require_token)):
        return _media(request, episode_id, "video_path", "video/mp4")

    @app.get("/episodes/{episode_id}/thumb")
    async def thumb(request: Request, episode_id: int, _t: None = Depends(require_token)):
        return _media(request, episode_id, "thumb_path", "image/png")

    @app.post("/episodes/{episode_id}/approve")
    async def approve(request: Request, episode_id: int, _t: None = Depends(require_token)):
        conn = db.connect(_db_path(request))
        try:
            ep = _row_episode(conn, episode_id)
            if ep is None:
                raise HTTPException(status_code=404, detail="episode yok")
            if ep["status"] != "rendered":
                raise HTTPException(status_code=422, detail="yalnizca rendered onaylanir")
            try:
                demo = bool(ep["is_demo"])
            except (KeyError, IndexError):
                demo = False
            if not demo and not ep.get("metadata_json"):
                raise HTTPException(status_code=409, detail="once metadata gerekli")
            db.set_episode_status(conn, episode_id, "approved")
            with conn:
                conn.execute(
                    "UPDATE episodes SET ab_group = ? WHERE id = ? AND ab_group IS NULL",
                    ("A" if episode_id % 2 == 0 else "B", episode_id),
                )
            return {"ok": True}
        finally:
            conn.close()

    @app.post("/episodes/{episode_id}/reject")
    async def reject(request: Request, episode_id: int, body: dict,
                     _t: None = Depends(require_token)):
        conn = db.connect(_db_path(request))
        try:
            if _row_episode(conn, episode_id) is None:
                raise HTTPException(status_code=404, detail="episode yok")
            db.set_episode_status(conn, episode_id, "rejected",
                                  reason=str(body.get("reason", ""))[:500])
            return {"ok": True}
        finally:
            conn.close()

    @app.get("/episodes/{episode_id}/package")
    async def package(request: Request, episode_id: int, _t: None = Depends(require_token)):
        import logging as _logging

        from devbrief.config import get_settings as _gs

        conn = db.connect(_db_path(request))
        try:
            ep = _row_episode(conn, episode_id)
            if ep is None:
                raise HTTPException(status_code=404, detail="episode yok")
            if ep["status"] != "approved":
                raise HTTPException(status_code=422, detail="once onay gerekli")
            corrections = [r["note"] for r in db.list_corrections(conn, episode_id)]
        finally:
            conn.close()
        mode, warn = _gs().effective_mode()
        if warn:
            _logging.getLogger("devbrief.api").warning("publish mode: %s", warn)
        base = str(request.base_url).rstrip("/")
        pkg = _plat.build_package(ep, f"{base}/episodes/{episode_id}/video", mode=mode,
                                  corrections=corrections)
        pkg["publish_mode"] = mode
        return pkg

    @app.post("/episodes/{episode_id}/publish-media")
    async def publish_media(request: Request, episode_id: int,
                            _t: None = Depends(require_token)):
        """Video+kapagi S3-uyumlu depoya koy, herkese acik URL dondur (n8n hub icin).

        Kapili: yalnizca onayli/golge-disi/demo-olmayan bolum (red 409).
        Token anahtari bolum kaydinda saklanir; yeniden yukleme ayni
        anahtari kullanir (idempotent).
        """
        from devbrief.config import get_settings as _gs
        from devbrief.publish import storage as _storage

        conn = db.connect(_db_path(request))
        try:
            ep = _row_episode(conn, episode_id)
            if ep is None:
                raise HTTPException(status_code=404, detail="episode yok")
            mode, _ = _gs().effective_mode()
            allowed, reason = _storage.upload_gate(ep, mode)
            if not allowed:
                raise HTTPException(status_code=409, detail=reason)
            video, thumb = ep.get("video_path") or "", ep.get("thumb_path") or ""
            token = _storage.ensure_media_token(conn, episode_id)
        finally:
            conn.close()
        if _storage.storage_settings() is None:
            raise HTTPException(status_code=409, detail="medya depolama yapilandirilmamis")
        video_p, thumb_p = _local_path(video) if video else None, None
        if thumb and _local_path(thumb).is_file():
            thumb_p = _local_path(thumb)
        if video_p is None or not video_p.is_file():
            raise HTTPException(status_code=404, detail="dosya yok")
        try:
            urls = await _storage.upload_episode_files(token, video_p, thumb_p)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=f"medya yukleme basarisiz: {e}") from e
        conn = db.connect(_db_path(request))
        try:
            _storage.mark_uploaded(conn, episode_id)
        finally:
            conn.close()
        out = {"ok": True, "episode_id": episode_id, **urls}
        if mode == "private":
            out["note"] = "yalnizca private modda"
        return out

    @app.delete("/episodes/{episode_id}/publish-media")
    async def delete_media(request: Request, episode_id: int,
                           _t: None = Depends(require_token)):
        """Bolum nesnelerini depodan sil (video + kapak; yoksa da basarili)."""
        from devbrief.publish import storage as _storage

        conn = db.connect(_db_path(request))
        try:
            ep = _row_episode(conn, episode_id)
            if ep is None:
                raise HTTPException(status_code=404, detail="episode yok")
            token = (ep.get("media_token") or "")
        finally:
            conn.close()
        if _storage.storage_settings() is None:
            raise HTTPException(status_code=409, detail="medya depolama yapilandirilmamis")
        if not token:
            return {"ok": True, "episode_id": episode_id, "deleted": []}
        try:
            for key in _storage.episode_media_keys(token):
                await _storage.delete_file(key)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=f"medya silme basarisiz: {e}") from e
        conn = db.connect(_db_path(request))
        try:
            _storage.clear_uploaded(conn, episode_id)
        finally:
            conn.close()
        return {"ok": True, "episode_id": episode_id,
                "deleted": _storage.episode_media_keys(token)}

    @app.post("/episodes/{episode_id}/published")
    async def published(request: Request, episode_id: int, body: dict,
                        _t: None = Depends(require_token)):
        from devbrief.config import get_settings as _gs

        mode, _ = _gs().effective_mode()
        if mode == "shadow":
            raise HTTPException(status_code=409, detail="shadow modda yayin kaydi yok")
        conn = db.connect(_db_path(request))
        try:
            ep = _row_episode(conn, episode_id)
            if ep is None:
                raise HTTPException(status_code=404, detail="episode yok")
            if ep["status"] != "approved":
                raise HTTPException(status_code=422, detail="once onay gerekli")
            results = body.get("results") or []
            with conn:
                for r in results:
                    conn.execute(
                        "INSERT INTO publications (episode_id, platform, external_id, url,"
                        " published_at) VALUES (?, ?, ?, ?, ?)",
                        (episode_id, r.get("platform"), r.get("external_id"), r.get("url"),
                         db.now_iso()))
            if results and all(r.get("ok") for r in results):
                db.set_episode_status(conn, episode_id, "published")
            return {"ok": True, "saved": len(results)}
        finally:
            conn.close()

    @app.post("/backup")
    async def backup(request: Request, _t: None = Depends(require_token)):
        from devbrief.publish import backup as _backup

        try:
            dest = await asyncio.to_thread(_backup.take_backup)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e)[:500]) from e
        return {"ok": True, "file": dest.name}

    @app.post("/suggest")
    async def suggest(request: Request, body: dict):
        """Herkese acik oneri formu (auth yok). Baslik zorunlu, sinirlar siki."""
        title = str(body.get("title") or "").strip()
        url = str(body.get("url") or "").strip()
        note = str(body.get("note") or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="baslik gerekli")
        if len(title) > 200:
            raise HTTPException(status_code=422, detail="baslik en fazla 200 karakter")
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="baglanti http(s) olmali")
        if len(note) > 2000:
            raise HTTPException(status_code=422, detail="not en fazla 2000 karakter")
        conn = db.connect(_db_path(request))
        try:
            sid = db.add_suggestion(conn, title[:200], url[:500], note[:2000])
            return {"ok": True, "id": sid}
        finally:
            conn.close()

    @app.get("/suggestions")
    async def suggestions(request: Request, status: str = Query(""),
                          _t: None = Depends(require_token)):
        conn = db.connect(_db_path(request))
        try:
            rows = db.list_suggestions(conn, status or None)
            return [{"id": r["id"], "title": r["title"], "url": r["url"],
                     "note": r["note"], "status": r["status"],
                     "created_at": r["created_at"]} for r in rows]
        finally:
            conn.close()

    @app.post("/suggestions/{sid}/review")
    async def review_suggestion(request: Request, sid: int, body: dict,
                                _t: None = Depends(require_token)):
        status = str(body.get("status") or "")
        if status not in ("reviewed", "dismissed"):
            raise HTTPException(status_code=422, detail="durum reviewed/dismissed olmali")
        conn = db.connect(_db_path(request))
        try:
            if not db.set_suggestion_status(conn, sid, status):
                raise HTTPException(status_code=404, detail="oneri yok")
            return {"ok": True, "id": sid, "status": status}
        finally:
            conn.close()

    @app.post("/episodes/{episode_id}/correction")
    async def add_correction(request: Request, episode_id: int, body: dict,
                             _t: None = Depends(require_token)):
        """Yayinlanmis icerige duzeltme notu (paket aciklamasina islenir)."""
        note = str(body.get("note") or "").strip()
        if not note:
            raise HTTPException(status_code=422, detail="not gerekli")
        if len(note) > 2000:
            raise HTTPException(status_code=422, detail="not en fazla 2000 karakter")
        conn = db.connect(_db_path(request))
        try:
            ep = _row_episode(conn, episode_id)
            if ep is None:
                raise HTTPException(status_code=404, detail="episode yok")
            if ep["status"] not in ("approved", "published"):
                raise HTTPException(status_code=422, detail="duzeltme yayin akisina yazilir")
            cid = db.add_correction(conn, episode_id, note[:2000])
            return {"ok": True, "id": cid}
        finally:
            conn.close()

    @app.get("/episodes/{episode_id}/corrections")
    async def get_corrections(request: Request, episode_id: int,
                              _t: None = Depends(require_token)):
        conn = db.connect(_db_path(request))
        try:
            if _row_episode(conn, episode_id) is None:
                raise HTTPException(status_code=404, detail="episode yok")
            rows = db.list_corrections(conn, episode_id)
            return [{"id": r["id"], "note": r["note"],
                     "created_at": r["created_at"]} for r in rows]
        finally:
            conn.close()

    @app.post("/metrics")
    async def metrics(request: Request, body: dict, _t: None = Depends(require_token)):
        conn = db.connect(_db_path(request))
        try:
            row = conn.execute(
                "SELECT id FROM publications WHERE platform = ? AND external_id = ?",
                (body.get("platform"), body.get("external_id"))).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="yayin yok")
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO metrics (publication_id, day, views, avg_view_pct,"
                    " likes) VALUES (?, ?, ?, ?, ?)",
                    (row["id"], body.get("day", ""), body.get("views", 0),
                     body.get("avg_view_pct", 0), body.get("likes", 0)))
            return {"ok": True}
        finally:
            conn.close()

    return app


def _parse_json(text: str | None) -> dict:
    try:
        out = json.loads(text or "{}")
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}


def _approval_info(script: dict, verify: dict | None = None) -> tuple[list[str], list[str]]:
    try:
        from devbrief.script.schemas import Script as _S

        model = _S.model_validate(script, context={"legacy": True})
        infs = [c.text[:120] for c in model.inferences()]
        # Impact/action olgu degil yorumdur: ayri listelenir.
        for key in ("impact", "action"):
            text = str(getattr(model, key, "") or "").strip()
            if text:
                infs.append(f"[{key}] {text[:120]}")
        warns = list(model.content_warnings())
    except Exception:  # noqa: BLE001 - senaryosuz/bozuk bolum
        return [], []
    # Voice-turetilmis yorum cumleleri onayda "yorum" olarak ayri gorunur.
    try:
        for d in (verify or {}).get("voice_derived") or []:
            if isinstance(d, dict) and d.get("status") == "yorum" and d.get("text"):
                infs.append(f"[{d.get('field', '?')}] {str(d['text'])[:120]}")
    except Exception:  # noqa: BLE001 - bozuk verify kaydi onayi engellemez
        pass
    return warns, infs


def _local_path(value: str | Path) -> Path:
    """DB'deki goreceli medya yolunu platform-bagimsiz coz.

    Kayitlar Windows ayraci (`out\\gun\\slug\\video.mp4`) tasiyabilir;
    Linux container'da ayni metin tek dosya adi sayilir ve bulunamaz.
    Duz ayrac her iki platformda da gecerlidir.
    """
    return Path(str(value).replace("\\", "/"))


def _media(request: Request, episode_id: int, kind: str, media_type: str):
    import devbrief.db as db

    conn = db.connect(_db_path(request))
    try:
        ep = _row_episode(conn, episode_id)
    finally:
        conn.close()
    if ep is None or not ep.get(kind):
        raise HTTPException(status_code=404, detail="dosya yok")
    path = _local_path(ep[kind])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="dosya yok")
    return RangedFileResponse(path, media_type, request.headers.get("range"))


app = create_app(os.environ.get("DEVBRIEF_DB", "data/devbrief.db"))
