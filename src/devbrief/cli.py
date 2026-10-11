"""Typer CLI: doctor, init-secrets. Diger komutlar Faz 1+ iskeleti."""

from __future__ import annotations

import secrets
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - eski konsollarda sessiz gec
        pass

import typer
import yaml

app = typer.Typer(no_args_is_help=True)

REQUIRED_SECRETS = ("DEVBRIEF_API_TOKEN", "N8N_CALLBACK_SECRET", "N8N_ENCRYPTION_KEY")


def _load_env_file(path: Path = Path(".env")) -> dict[str, str]:
    vals: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def _tool_ok(name: str, configured: str) -> bool:
    if configured and Path(configured).is_file():
        return True
    if configured and shutil.which(configured):
        return True
    if shutil.which(name):
        return True
    return Path("bin", f"{name}.exe").is_file()


@app.command()
def doctor(
    check_sources: bool = typer.Option(False, "--check-sources", help="9 feed'e istek at"),
    ping_llm: bool = typer.Option(False, "--ping-llm", help="LLM modellerine 1 kucuk istek"),
    fix_sources: bool = typer.Option(
        False, "--fix-sources", help="Bozuk feed'i sources.yaml'da devre disi birak"
    ),
) -> None:
    """Faz 0 cikis kapisi: anahtarlar, ffmpeg, modeller, data/out yazilabilirligi."""
    from devbrief.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    ok = True

    def line(name: str, good: bool, hint: str = "") -> None:
        nonlocal ok
        mark = "OK  " if good else "FAIL"
        if not good:
            ok = False
        typer.echo(f"[{mark}] {name} {hint}")

    line("OPENROUTER_API_KEY", bool(s.openrouter_api_key), "" if s.openrouter_api_key else "(bos)")
    for key in REQUIRED_SECRETS:
        present = bool(getattr(s, key.lower(), ""))
        line(key, present, "" if present else "(bos: `devbrief init-secrets` calistir)")
    line(
        "LLM_MODELS_FAST/SMART",
        bool(s.fast_models and s.smart_models),
        f"fast={s.fast_models} smart={s.smart_models}",
    )
    line(
        "LLM_MODELS_VERIFY/VERIFY_CALL_CAP",
        bool(s.verify_models) and s.verify_call_cap > 0,
        f"verify={s.verify_models} cap={s.verify_call_cap}",
    )
    line("ffmpeg", _tool_ok("ffmpeg", s.ffmpeg_path), "(FFMPEG_PATH/PATH/bin)")
    line("ffprobe", _tool_ok("ffprobe", s.ffprobe_path), "")
    for d in ("data", "out"):
        p = Path(d)
        try:
            p.mkdir(exist_ok=True)
            writable = p.is_dir()
        except OSError:
            writable = False
        line(f"{d}/ yazilabilir", writable)

    if check_sources:
        results = check_feed_sources(fix=fix_sources)
        for name, good, hint in results:
            line(f"source:{name}", good, hint)

    if ping_llm:
        if not s.openrouter_api_key:
            line("ping-llm", False, "(anahtar yok)")
        elif not s.all_models:
            line("ping-llm", False, "(model listesi bos)")
        else:
            for m in s.all_models:
                good, hint = _ping_model(m, s.openrouter_api_key)
                line(f"ping-llm:{m}", good, hint)

    if s.openrouter_api_key:
        _echo_key_status(s.openrouter_api_key)

    if not ok:
        raise typer.Exit(code=1)
    typer.echo("doctor: hepsi yesil")


def _echo_key_status(api_key: str) -> None:
    """Anahtar kotasi: ag hatasi WARN, cikisi dusurmez. Kimlik loglanmaz."""
    from devbrief.llm.key import assess_key, fetch_key_status

    data = fetch_key_status(api_key)
    if data is None:
        typer.echo("[WARN] openrouter-key (erisilemedi)")
        return
    for level, text in assess_key(data):
        typer.echo(f"[{level:4s}] openrouter-key {text}")


def check_feed_sources(fix: bool = False) -> list[tuple[str, bool, str]]:
    """sources.yaml'daki 9 RSS + GitHub releases atom adreslerine istek at.

    fix=True ise ulasilamayan RSS feed'i `enabled: false` ile isaretler
    (orijinal URL yoruma alinir, veri kaybi olmaz).
    """
    import httpx

    src_path = Path("sources.yaml")
    data = yaml.safe_load(src_path.read_text(encoding="utf-8")) if src_path.exists() else {}
    urls: list[tuple[str, str]] = []
    for item in (data.get("rss") or []):
        if isinstance(item, dict) and item.get("url") and item.get("enabled", True) is not False:
            urls.append((str(item.get("name", item["url"])), str(item["url"])))
    for item in (data.get("github_releases") or []):
        if isinstance(item, dict) and item.get("repo"):
            repo = str(item["repo"])
            urls.append((repo, f"https://github.com/{repo}/releases.atom"))

    out: list[tuple[str, bool, str]] = []
    headers = {"User-Agent": "DevBrief-TR/0.1 (+https://github.com/devbrief)"}
    failed: set[str] = set()
    with httpx.Client(timeout=15, headers=headers, follow_redirects=True) as c:
        for name, url in urls:
            try:
                r = c.get(url)
                good = r.status_code < 400
                out.append((name, good, f"({r.status_code})"))
                if not good:
                    failed.add(url)
            except Exception as e:  # noqa: BLE001 - kaynak basina izole hata
                out.append((name, False, f"({type(e).__name__})"))
                failed.add(url)

    if fix and failed and src_path.exists():
        text = src_path.read_text(encoding="utf-8")
        for url in failed:
            if url in text:
                text = text.replace(url, f"{url}  # BOZUK (doctor --fix-sources)")
        src_path.write_text(text, encoding="utf-8")
    return out


def _ping_model(model: str, api_key: str) -> tuple[bool, str]:
    import httpx

    try:
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 5,
            },
            timeout=20,
        )
        return (r.status_code == 200, f"({r.status_code})")
    except Exception as e:  # noqa: BLE001
        return (False, f"({type(e).__name__})")


@app.command()
def probe() -> None:
    """2 saatlik canlilik yoklamasi: model basina plain + structured istek.

    Zamanlayici (DevBriefProbe) ile kosar; 3-4 gunde yogunluk profili cikar.
    Modeller LLM_PROBE_MODELS'ten (bos ise FAST+SMART+VERIFY); zincir disi
    yoklanmaz. Ucretli korumasi gecerli: allowlist disi model atlanir.
    """
    import time as _time

    import httpx

    import devbrief.db as db
    from devbrief.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    if not s.openrouter_api_key:
        typer.echo("OPENROUTER_API_KEY bos", err=True)
        raise typer.Exit(code=1)
    conn = db.connect()
    db.migrate(conn)
    typer.echo(f"{'model':50s} {'ms':>7s} sonuc")
    for m in s.probe_models:
        if not s.model_allowed(m):
            typer.echo(f"{m:50s} {'-':>7s} atlandi (ucretli korumasi)")
            continue
        paid = not m.endswith(":free")
        for variant, body in (
            ("plain", {"model": m, "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 5}),
            ("structured", {"model": m, "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 5,
                            "response_format": {"type": "json_object"},
                            "provider": {"require_parameters": True}}),
        ):
            try:
                slot = db.reserve_llm_slot(conn, f"probe:{variant}", m, s.llm_daily_budget)
            except db.BudgetExceeded:
                typer.echo("butce doldu, duruluyor")
                conn.close()
                return
            t0 = _time.perf_counter()
            status, ok, hint = "other", False, ""
            try:
                r = httpx.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {s.openrouter_api_key}"},
                    json=body,
                    timeout=25,
                )
                ok = r.status_code == 200
                if r.status_code == 429:
                    status = "http_429"
                elif r.status_code >= 500:
                    status = "http_5xx"
                elif r.status_code == 404:
                    status = "no_endpoint_for_params"
                elif ok:
                    status = "ok"
                hint = f"({r.status_code})"
            except (httpx.TimeoutException, httpx.ConnectError):
                status, hint = "timeout", "(timeout)"
            except Exception as e:  # noqa: BLE001
                hint = f"({type(e).__name__})"
            ms = int((_time.perf_counter() - t0) * 1000)
            db.finish_llm_slot(conn, slot, ok=ok, ms=ms, status=status, is_paid=paid,
                               response_format=("plain" if variant == "plain" else "schema"))
            typer.echo(f"{m:50s} {ms:7d} {variant:10s} {status} {hint}")
    conn.close()


@app.command("init-secrets")
def init_secrets() -> None:
    """DEVBRIEF_API_TOKEN, N8N_CALLBACK_SECRET, N8N_ENCRYPTION_KEY uret, .env'e yaz."""
    env_path = Path(".env")
    existing = _load_env_file(env_path)
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = dict(existing)
    for key in REQUIRED_SECRETS:
        if not updated.get(key):
            updated[key] = secrets.token_urlsafe(32)
    # .env.example'daki anahtar sirasi korunur, yoksa sona eklenir.
    for key, val in updated.items():
        found = False
        for i, ln in enumerate(lines):
            if ln.strip().startswith(key + "="):
                lines[i] = f"{key}={val}"
                found = True
                break
        if not found:
            lines.append(f"{key}={val}")
    for key in REQUIRED_SECRETS:
        if not any(ln.strip().startswith(key + "=") for ln in lines):
            lines.append(f"{key}={updated[key]}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    typer.echo("init-secrets: .env guncellendi (mevcut dolu degerler korunur)")


def _parse_only(spec: str) -> tuple[set[int], list[str]]:
    """'P9,P10' -> ({9, 10}, []). Bozuk jetonlar ikinci listede."""
    ids: set[int] = set()
    bad: list[str] = []
    for part in spec.split(","):
        raw = part.strip()
        num = raw.upper().lstrip("P")
        if num.isdigit():
            ids.add(int(num))
        elif raw:
            bad.append(raw)
    return ids, bad


def _para_selected(index: int, spec: str) -> bool:
    spec = (spec or "").strip()
    if not spec:
        return True
    want: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            want.update(range(int(a), int(b) + 1))
        elif part:
            want.add(int(part))
    return index in want


def _summarize_saved(out: Path, paras: list, tr_thr: float) -> None:
    import json as _json2

    from devbrief.eval import (
        LabeledPara as _LP,
    )
    from devbrief.eval import (
        render_gate_table as _table,
    )
    from devbrief.eval import (
        summarize_results as _summ,
    )
    from devbrief.eval import (
        write_summary_md as _wsm,
    )

    by_model: dict[str, list[dict]] = {}
    for f in sorted(out.glob("*/P*.json")):
        try:
            d = _json2.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        para = next((p for p in paras if p.index == d.get("para")), None)
        if para is None:
            para = _LP(index=int(d.get("para", 0)), kind=str(d.get("kind", "normal")), text="")
        by_model.setdefault(str(d.get("model", f.parent.name)), []).append(
            {"para": para, "ms": float(d.get("ms", 0)), "text": d.get("output") or "",
             "finish": d.get("finish"), "variant": d.get("variant", "ham"),
             "source": d.get("source")}
        )
    reports = [_summ(m, sorted(v, key=lambda r: r["para"].index), tr_thr)
               for m, v in sorted(by_model.items())]
    _wsm(reports, out / "summary.md")
    typer.echo(_table(reports))
    typer.echo("Aday: " + (", ".join(r.model for r in reports if r.qualifies) or "(yok)"))


@app.command("eval-models")
def eval_models(
    paragraphs: str = typer.Option(
        "tests/fixtures/tr_paragraphs.txt", help="10 paragraf (`---` ayracli)"
    ),
    out_dir: str = typer.Option("out/eval", help="ham cikti + summary.md dizini"),
    yes: bool = typer.Option(False, "--yes", help="butce onayini atla"),
    models: str = typer.Option("", "--models", help="virgullu alt-dize filtresi (bos=hepsi)"),
    paras: str = typer.Option("", "--paras", help="paragraf secimi: 7-10 veya 9,10 (bos=hepsi)"),
    only: str = typer.Option("", "--only", help="paragraf alt kumesi: P9,P10 (bos=hepsi)"),
    file: str = typer.Option("", "--file", help="paragraf dosyasi (bos=varsayilan)"),
    runs: int = typer.Option(1, "--runs", help="her (model, paragraf) kac kez"),
    summarize_only: bool = typer.Option(
        False, "--summarize-only", help="API'siz: kayitli P*.json'dan ozet kur"
    ),
) -> None:
    """Model kapisi: write_script_v1 ile senaryo urettir, otomatik kontroller.

    Gercek API'ye istek atan TEK komut. Fallback yok, onarim denemesi yok.
    Her istek gunluk butce sayacina islenir.
    """
    import asyncio
    import json as _json
    import time as _time

    import httpx

    import devbrief.db as db
    from devbrief.config import get_settings
    from devbrief.eval import (
        Bucket,
        load_labeled_paragraphs,
        render_gate_table,
        slot_status,
        summarize_results,
        write_summary_md,
    )
    from devbrief.llm.client import load_prompt

    get_settings.cache_clear()
    s = get_settings()
    if not s.openrouter_api_key:
        typer.echo("OPENROUTER_API_KEY bos", err=True)
        raise typer.Exit(code=1)
    if not s.all_models:
        typer.echo("LLM_MODELS_* bos", err=True)
        raise typer.Exit(code=1)
    para_spec = paras
    paras = load_labeled_paragraphs(file or paragraphs)
    if para_spec:
        paras = [p for p in paras if _para_selected(p.index, para_spec)]
    if only:
        wanted_idx, bad_tokens = _parse_only(only)
        known_idx = {p.index for p in paras}
        unknown = [str(i) for i in sorted(wanted_idx) if i not in known_idx] + bad_tokens
        if unknown:
            typer.echo(f"bilinmeyen paragraf: {', '.join(unknown)}; "
                       f"gecerli: {', '.join('P' + str(i) for i in sorted(known_idx))}",
                       err=True)
            raise typer.Exit(code=2)
        paras = [p for p in paras if p.index in wanted_idx]
    wanted = [m for m in s.all_models
              if not models or any(f.strip() in m for f in models.split(",") if f.strip())]
    for m in list(wanted):
        if not s.model_allowed(m):
            typer.echo(f"atlandi (ucretli korumasi): {m}", err=True)
            wanted.remove(m)
    if models and not wanted:
        typer.echo(f"bilinmeyen model filtresi: {models!r}; "
                   f"gecerli: {', '.join(s.all_models)}", err=True)
        raise typer.Exit(code=2)
    if models and not wanted:
        typer.echo(f"bilinmeyen model filtresi: {models!r}; "
                   f"gecerli: {', '.join(s.all_models)}", err=True)
        raise typer.Exit(code=2)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if summarize_only:
        _summarize_saved(out, paras, s.eval_tr_ratio)
        raise typer.Exit(code=0)
    planned = len(paras) * len(wanted) * max(1, runs)
    typer.echo(f"{len(paras)} paragraf x {len(wanted)} model x {max(1, runs)} tur = "
               f"{planned} istek")
    if not yes and not typer.confirm("Butceye islensin mi? Devam?"):
        raise typer.Exit(code=0)

    template = load_prompt("write_script_v1")
    bucket = Bucket(s.llm_rate_per_minute)
    conn = db.connect()
    db.migrate(conn)
    reports = []

    async def _post(model: str, prompt: str) -> tuple[str, float, str | None, str | None]:
        await bucket.wait()
        slot = db.reserve_llm_slot(conn, "eval-models", model, s.llm_daily_budget)
        tier = s.tier_of(model)
        t0 = _time.perf_counter()
        text, ok, finish, http_status, exc = "", False, None, None, None
        try:
            async with httpx.AsyncClient(timeout=s.timeout_for(tier)) as client:
                r = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {s.openrouter_api_key}"},
                    json={"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 4000, "temperature": 0.6},
                )
                http_status = r.status_code
                if r.status_code == 200:
                    try:
                        msg = r.json()["choices"][0]["message"]
                        content = msg.get("content")
                        finish = msg.get("finish_reason")
                        text = content if isinstance(content, str) else ""
                        ok = bool(text)
                    except (KeyError, IndexError, ValueError):
                        text, ok = r.text[:2000], False
                else:
                    text, ok = f"HTTP {r.status_code}: {r.text[:500]}", False
        except Exception as e:  # noqa: BLE001 - ag hatasi slot'a islenir
            text, ok, exc = f"{type(e).__name__}: {e}", False, type(e).__name__
        finally:
            status = slot_status(http_status, text, finish, exc)
            db.finish_llm_slot(conn, slot, ok=ok, ms=int((_time.perf_counter() - t0) * 1000),
                               status=status)
        ms = (_time.perf_counter() - t0) * 1000
        return text, ms, finish, status

    async def _run() -> None:
        from devbrief.script import injection as _inj

        for m in wanted:
            results = []
            mdir = out / m.replace("/", "_").replace(":", "_")
            mdir.mkdir(parents=True, exist_ok=True)
            for p in paras:
                variants = [("ham", p.text)]
                if p.kind == "injection":
                    clean, removed = _inj.scan(p.text)
                    if removed:
                        variants.append(("hat", clean))
                for variant, src_text in variants:
                    for trial in range(1, max(1, runs) + 1):
                        prompt = (template.replace("{{source_text}}", src_text)
                                  .replace("{{source_url}}", "https://eval.local/p")
                                  .replace("{{style_guide}}", "TR, sade, teknik terimler EN kalir")
                                  .replace("{{previous_errors}}", "yok")
                                  .replace("{{injection_note}}", ""))
                        text, ms, finish, status = await _post(m, prompt)
                        results.append({"para": p, "ms": ms, "text": text, "trial": trial,
                                        "finish": finish, "variant": variant, "source": src_text})
                        if max(1, runs) == 1 and variant == "ham":
                            fname = f"P{p.index}.json"
                        else:
                            fname = f"P{p.index}_{variant}_r{trial}.json"
                        (mdir / fname).write_text(
                        _json.dumps({"model": m, "para": p.index, "kind": p.kind,
                                     "variant": variant, "trial": trial,
                                     "ms": round(ms), "finish": finish,
                                     "status": status, "output": text,
                                     "source": src_text if variant == "hat" else None},
                                        ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        typer.echo(f"  {m} P{p.index}{variant}r{trial} ({p.kind}): {ms:.0f} ms")
            rep = summarize_results(m, results, tr_thr=s.eval_tr_ratio)
            reports.append(rep)

    try:
        asyncio.run(_run())
    except db.BudgetExceeded:
        typer.echo("UYARI: gunluk butce doldu; tamamlanan modellerin kismi ozeti yazildi.",
                   err=True)
    conn.close()
    write_summary_md(reports, out / "summary.md")
    typer.echo(render_gate_table(reports))
    typer.echo("Aday: " + (", ".join(r.model for r in reports if r.qualifies) or "(yok)"))
    typer.echo("Turkce dogalligi elle oku: out/eval/<model>/P<n>.json")


def _summarize_verify_saved(out: Path, all_cases: list) -> None:
    import json as _json3

    from devbrief.eval import assess_verdict
    from devbrief.eval import (
        render_verifier_table as _vtable,
    )
    from devbrief.eval import (
        summarize_verifier as _sver,
    )
    from devbrief.eval import (
        write_verifier_summary as _wvs,
    )

    by_case = {(c.get("para_id"), c.get("kind")): c for c in all_cases}
    by_model: dict[str, list[dict]] = {}
    for f in sorted(out.glob("*/*.json")):
        if f.name.startswith("summary"):
            continue
        try:
            d = _json3.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if "outputs" in d:  # pair kaydi: iki ham cikti birlikte degerlendirilir
            from devbrief.eval import assess_pair as _apair

            tag = str(d.get("case") or "")
            try:
                pid, _, kind = tag[1:].partition("_")
                case = by_case.get((int(pid), kind))
            except ValueError:
                case = None
            if case is None:
                continue
            a = _apair(dict(d["outputs"]), case, list(d.get("models") or []))
            if "index_first_ok" in d:
                a["index_first_ok"] = bool(d["index_first_ok"])
            if "retried" in d:
                a["retried"] = bool(d["retried"])
            by_model.setdefault(str(d.get("model", "pair")), []).append(
                {"case": case, "ms": float(d.get("ms", 0)),
                 "cost": d.get("cost"), "assessment": a}
            )
            continue
        tag = str(d.get("case") or f.stem.split("_r")[0])  # P<id>_<kind>[_rN]
        try:
            pid, _, kind = tag[1:].partition("_")
            case = by_case.get((int(pid), kind))
        except ValueError:
            case = None
        if case is None:
            continue
        a = assess_verdict(d.get("output") or "", case)
        # Kayitli retry bilgisi varsa ilk-deneme uyumunu geri yukle.
        if "index_first_ok" in d:
            a["index_first_ok"] = bool(d["index_first_ok"])
        if "retried" in d:
            a["retried"] = bool(d["retried"])
        by_model.setdefault(str(d.get("model", f.parent.name)), []).append(
            {"case": case, "ms": float(d.get("ms", 0)),
             "cost": d.get("cost"), "assessment": a}
        )
    reports = [_sver(m, v) for m, v in sorted(by_model.items())]
    _wvs(reports, out / "summary_verifier.md")
    typer.echo(_vtable(reports))
    typer.echo("VERIFY adayi: " + (", ".join(r.model for r in reports if r.qualifies) or "(yok)"))


@app.command("eval-verifier")
def eval_verifier(
    cases: str = typer.Option(
        "tests/fixtures/verifier_cases.json", help="40 vaka (temiz/bozuk cesitleri)"
    ),
    out_dir: str = typer.Option("out/eval-verify", help="ham cikti + summary_verifier.md"),
    yes: bool = typer.Option(False, "--yes", help="butce onayini atla"),
    models: str = typer.Option("", "--models", help="virgullu alt-dize filtresi (bos=hepsi)"),
    any_model: str = typer.Option("", "--any-model", help="listede olmayan tek model ID'si"),
    paras: str = typer.Option("", "--paras", help="vaka secimi: 1-5 veya 1,2 (bos=hepsi)"),
    runs: int = typer.Option(1, "--runs", help="her vaka kac kez (kararsizlik icin)"),
    resume: bool = typer.Option(
        False, "--resume", help="kesilen kosuya devam: var olan sonuc dosyalari atlanir"),
    summarize_only: bool = typer.Option(
        False, "--summarize-only", help="API'siz: kayitli vaka dosyalarindan ozet kur"
    ),
) -> None:
    """Dogrulayici kapisi: her modeli verify rolunde 40 vakayla dene.

    Gercek API'ye istek atan eval komutlarindan biri. Modeller LLM_MODELS_VERIFY
    (bos ise SMART) listesinden; writer'dan ayri secilebilir. Bicim hatasinda
    (eksik/tekrarli/aralik-disi indeks) AYNI modele bir kez somut hata listesiyle
    yeniden sorulur (butceye dahil); ikinci de bozuksa eksikler unsupported
    sayilir (kapi esikleri GEVSETILMEZ: recall >= %90, FP <= %10).

    `--models a,b` (virgulle iki model) pair mode: her vaka iki dogrulayiciya
    sorulur, claim yalnizca ikisi de entailed ise gecer; model bazinda
    sonuclar + "a+b" birlesik satiri raporlanir (kapi yalnizca birlesige).
    Onay YALNIZCA `--yes` bayragiyla verilir (otomatik `y` besleme yok).
    """
    import asyncio
    import json as _json
    import time as _time

    import httpx

    import devbrief.db as db
    from devbrief.config import get_settings
    from devbrief.eval import (
        Bucket,
        assess_pair,
        assess_verdict,
        load_verifier_cases,
        render_verifier_table,
        summarize_verifier,
        write_verifier_summary,
    )
    from devbrief.llm.client import load_prompt

    get_settings.cache_clear()
    s = get_settings()
    if not s.openrouter_api_key:
        typer.echo("OPENROUTER_API_KEY bos", err=True)
        raise typer.Exit(code=1)
    pool = list(dict.fromkeys(s.all_models + s.verify_models))
    pair: list[str] | None = None
    if any_model.strip():
        if not s.model_allowed(any_model.strip()):
            typer.echo(f"ucretli korumasi: {any_model.strip()} reddedildi "
                       "(ALLOW_PAID_MODELS + allowlist gerekli)", err=True)
            raise typer.Exit(code=2)
        wanted = [any_model.strip()]
    else:
        for m in pool:
            if not s.model_allowed(m):
                typer.echo(f"atlandi (ucretli korumasi): {m}", err=True)
        pool = [m for m in pool if s.model_allowed(m)]
        parts = [p.strip() for p in models.split(",") if p.strip()] if models else []
        if len(parts) == 2:
            # Pair mode: her parca exact, yoksa alt-dize ilk-eslesme.
            resolved = []
            for p in parts:
                exact = [m for m in pool if m == p]
                if exact:
                    resolved.append(exact[0])
                    continue
                sub = [m for m in pool if p in m]
                if not sub:
                    typer.echo(f"bilinmeyen model filtresi: {p!r}; "
                               f"gecerli: {', '.join(pool)}", err=True)
                    raise typer.Exit(code=2)
                resolved.append(sub[0])
            if resolved[0] == resolved[1]:
                wanted = [resolved[0]]
            else:
                pair = resolved
                wanted = resolved
        else:
            wanted = [m for m in pool
                      if not models or any(f.strip() in m for f in models.split(",")
                                           if f.strip())]
            if models and not wanted:
                typer.echo(f"bilinmeyen model filtresi: {models!r}; "
                           f"gecerli: {', '.join(pool)}", err=True)
                raise typer.Exit(code=2)
    if not wanted:
        typer.echo("VERIFY model listesi bos", err=True)
        raise typer.Exit(code=1)
    all_cases = load_verifier_cases(cases)
    if paras:
        all_cases = [c for c in all_cases if _para_selected(c.get("para_id", 0), paras)]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if summarize_only:
        _summarize_verify_saved(out, all_cases)
        raise typer.Exit(code=0)
    n_runs = max(1, runs)
    per_case = len(all_cases) * n_runs
    planned = per_case * len(wanted) if pair is None else per_case * 2
    paid_run = any(not m.endswith(":free") for m in wanted)
    if paid_run:
        typer.echo("UYARI: ucretli model kosusu (kredi harcar).", err=True)
    if pair is not None:
        typer.echo(f"{len(all_cases)} vaka x pair ({pair[0]} + {pair[1]}) x "
                   f"{n_runs} tur = {planned} istek (+gerekirse bicim-retry)")
    else:
        typer.echo(f"{len(all_cases)} vaka x {len(wanted)} model x {n_runs} tur = "
                   f"{planned} istek (+gerekirse bicim-retry)")
    if resume:
        typer.echo("--resume: var olan sonuc dosyalari atlanir")
    if not yes:
        typer.echo("--yes gerekli: eval butceye isler, onay yalnizca bayrakla verilir",
                   err=True)
        raise typer.Exit(code=2)

    template = load_prompt("verify_script_v1")
    bucket = Bucket(s.llm_rate_per_minute)
    conn = db.connect()
    db.migrate(conn)
    reports = []

    def _extract_cost(payload: object) -> float | None:
        try:
            if isinstance(payload, dict):
                usage = payload.get("usage") or {}
                cost = usage.get("cost") if isinstance(usage, dict) else None
                return float(cost) if cost is not None else None
        except (TypeError, ValueError):
            return None
        return None

    async def _post(model: str, prompt: str, task: str = "eval-verify"
                    ) -> tuple[str, float, float | None]:
        await bucket.wait()
        slot = db.reserve_llm_slot(conn, task, model, s.llm_daily_budget)
        t0 = _time.perf_counter()
        text, ok, finish, http_status, exc, cost = "", False, None, None, None, None
        try:
            async with httpx.AsyncClient(timeout=s.timeout_for(s.tier_of(model))) as client:
                r = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {s.openrouter_api_key}"},
                    json={"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 1200, "temperature": 0.0},
                )
                http_status = r.status_code
                if r.status_code == 200:
                    try:
                        body = r.json()
                        msg = body["choices"][0]["message"]
                        content = msg.get("content")
                        finish = msg.get("finish_reason")
                        text = content if isinstance(content, str) else ""
                        ok = bool(text)
                        cost = _extract_cost(body)
                    except (KeyError, IndexError, ValueError):
                        text, ok = r.text[:2000], False
                else:
                    text, ok = f"HTTP {r.status_code}: {r.text[:500]}", False
        except Exception as e:  # noqa: BLE001
            text, ok, exc = f"{type(e).__name__}: {e}", False, type(e).__name__
        finally:
            from devbrief.eval import slot_status as _slot_status

            db.finish_llm_slot(
                conn, slot, ok=ok, ms=int((_time.perf_counter() - t0) * 1000),
                status=_slot_status(http_status, text, finish, exc), cost=cost)
        return text, (_time.perf_counter() - t0) * 1000, cost

    def _claims_text(claims: list) -> str:
        return "\n".join(
            f"[{i}] {c.get('text', '')} || kanit: {c.get('evidence', '')}"
            for i, c in enumerate(claims))

    def _base_prompt(c: dict) -> str:
        claims = (c.get("script") or {}).get("claims") or []
        return (template.replace("{{source_text}}", c.get("para", ""))
                .replace("{{claims}}", _claims_text(claims))
                .replace("{{format_errors}}", "yok"))

    async def _run() -> None:
        import json as _js

        from devbrief.script.schemas import VerifyOut
        from devbrief.script.verifier import format_error_list

        async def _once(m: str, prompt: str, claims: list
                        ) -> tuple[str, str | None, bool, bool, float, float | None]:
            """Tek model tek tur (bicim-retry dahil).
            Donus: (final_metin, ilk_metin|None, ilk_ok, retry, ms, maliyet)."""
            text, ms, cost = await _post(m, prompt)
            try:
                raw = VerifyOut.model_validate(
                    _js.loads((text or "").strip().removeprefix("```json")
                              .removeprefix("```").removesuffix("```").strip()))
            except Exception:  # noqa: BLE001
                raw = None
            fmt_errs = format_error_list(
                raw, [{"text": k.get("text", ""),
                       "kind": k.get("kind", "fact")} for k in claims])
            first_ok = not fmt_errs and raw is not None and bool(raw.verdicts)
            if not fmt_errs:
                return text, None, first_ok, False, ms, cost
            retry_prompt = (prompt + "\nBicim hatasi: " + ", ".join(fmt_errs)
                            + ". Yalnizca duzeltilmis JSON dondur.")
            text2, ms2, cost2 = await _post(
                m, retry_prompt, task="eval-verify:format_retry")
            total_cost = ((cost or 0.0) if cost else 0.0) + (
                (cost2 or 0.0) if cost2 else 0.0)
            if cost is None and cost2 is None:
                total_cost = None
            return text2, text, first_ok, True, ms + ms2, total_cost

        def _save(mdir, fname: str, payload: dict) -> None:
            (mdir / fname).write_text(
                _json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        def _tag(c: dict) -> str:
            return f"P{c.get('para_id')}_{c.get('kind')}"

        def _fname(tag: str, trial: int) -> str:
            return f"{tag}.json" if n_runs == 1 else f"{tag}_r{trial}.json"

        if pair is not None:
            a, b = pair
            by_model: dict[str, list[dict]] = {a: [], b: []}
            pair_results = []
            pdir = out / f"pair_{a}+{b}".replace("/", "_").replace(":", "_")
            pdir.mkdir(parents=True, exist_ok=True)
            mdirs = {m: out / m.replace("/", "_").replace(":", "_") for m in (a, b)}
            for mdir in mdirs.values():
                mdir.mkdir(parents=True, exist_ok=True)
            for c in all_cases:
                claims = (c.get("script") or {}).get("claims") or []
                prompt = _base_prompt(c)
                tag = _tag(c)
                for trial in range(1, n_runs + 1):
                    pfname = _fname(f"pair_{tag}", trial)
                    if resume and (pdir / pfname).exists():
                        typer.echo(f"  atlandi (resume) pair {tag}r{trial}")
                        continue
                    texts, firsts, oks, rets = {}, {}, {}, {}
                    ms_tot, cost_tot, cost_any = 0.0, 0.0, False
                    for m in (a, b):
                        t, f1, ok1, rt, ms, co = await _once(m, prompt, claims)
                        texts[m], firsts[m] = t, f1
                        oks[m], rets[m] = ok1, rt
                        ms_tot += ms
                        if co is not None:
                            cost_tot += co
                            cost_any = True
                        ap = assess_verdict(t, c)
                        ap["index_first_ok"] = ok1
                        ap["retried"] = rt
                        by_model[m].append({"case": c, "ms": ms, "cost": co,
                                            "assessment": ap})
                        _save(mdirs[m], _fname(tag, trial),
                              {"model": m, "case": tag, "trial": trial,
                               "ms": round(ms), "output": t,
                               "first_output": f1, "index_first_ok": ok1,
                               "retried": rt, "cost": co})
                    ap = assess_pair(texts, c, [a, b])
                    ap["index_first_ok"] = all(oks.values())
                    ap["retried"] = any(rets.values())
                    pair_results.append({"case": c, "ms": ms_tot,
                                         "cost": cost_tot if cost_any else None,
                                         "assessment": ap})
                    _save(pdir, pfname,
                          {"model": f"{a}+{b}", "models": [a, b], "case": tag,
                           "trial": trial, "ms": round(ms_tot), "outputs": texts,
                           "first_outputs": {m: f for m, f in firsts.items() if f},
                           "index_first_ok": ap["index_first_ok"],
                           "retried": ap["retried"],
                           "cost": cost_tot if cost_any else None})
                    typer.echo(f"  pair {tag}r{trial}: {ms_tot:.0f} ms")
            for m in (a, b):
                reports.append(summarize_verifier(m, by_model[m]))
            reports.append(summarize_verifier(f"{a}+{b}", pair_results))
            return

        for m in wanted:
            results = []
            mdir = out / m.replace("/", "_").replace(":", "_")
            mdir.mkdir(parents=True, exist_ok=True)
            for c in all_cases:
                claims = (c.get("script") or {}).get("claims") or []
                prompt = _base_prompt(c)
                tag = _tag(c)
                for trial in range(1, n_runs + 1):
                    fname = _fname(tag, trial)
                    if resume and (mdir / fname).exists():
                        typer.echo(f"  atlandi (resume) {m} {tag}r{trial}")
                        continue
                    text, first_text, first_ok, retried, ms, cost = await _once(
                        m, prompt, claims)
                    a = assess_verdict(text, c)
                    a["index_first_ok"] = first_ok
                    a["retried"] = retried
                    results.append({"case": c, "ms": ms, "cost": cost,
                                    "assessment": a})
                    _save(mdir, fname,
                          {"model": m, "case": tag, "trial": trial,
                           "ms": round(ms), "output": text,
                           "first_output": first_text if retried else None,
                           "index_first_ok": first_ok, "retried": retried,
                           "cost": cost})
                    typer.echo(f"  {m} {tag}r{trial}: {ms:.0f} ms"
                               + (" (bicim-retry)" if retried else ""))
            reports.append(summarize_verifier(m, results))

    try:
        asyncio.run(_run())
    except db.BudgetExceeded:
        typer.echo("UYARI: gunluk butce doldu; tamamlanan modellerin kismi ozeti yazildi.",
                   err=True)
    conn.close()
    write_verifier_summary(reports, out / "summary_verifier.md")
    typer.echo(render_verifier_table(reports))
    typer.echo("VERIFY adayi: " + (", ".join(r.model for r in reports if r.qualifies) or "(yok)"))


def _summarize_metadata_saved(out: Path, all_cases: list) -> None:
    import json as _json4

    from devbrief.eval import assess_metadata_case
    from devbrief.eval import (
        render_metadata_table as _mtable,
    )
    from devbrief.eval import (
        summarize_metadata as _smeta,
    )
    from devbrief.eval import (
        write_metadata_summary as _wms,
    )

    by_id = {c.get("id"): c for c in all_cases}
    by_model: dict[str, list[dict]] = {}
    for f in sorted(out.glob("*/*.json")):
        if f.name.startswith("summary"):
            continue
        try:
            d = _json4.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if d.get("mode", "generative") != "generative":
            continue  # extractive dosyalari ayri ozetlenir
        case = by_id.get(d.get("case"))
        if case is None:
            continue
        by_model.setdefault(str(d.get("model", f.parent.name)), []).append(
            {"case": case, "ms": float(d.get("ms", 0)),
             "assessment": assess_metadata_case(case, d.get("output"))}
        )
    reports = [_smeta(m, v) for m, v in sorted(by_model.items())]
    _wms(reports, out / "summary_metadata.md")
    typer.echo(_mtable(reports))
    for r in reports:
        mark = "GECTI" if r.qualifies else "KALDI"
        typer.echo(f"METADATA kapisi {r.model}: {mark} "
                   f"(birlesik %{r.recall:.0%}, temiz FP %{r.fp_rate:.0%})")


def _summarize_extractive_saved(out: Path, only: set[str] | None = None) -> None:
    """Kayitli extractive vaka dosyalarindan ozet kur (API'siz)."""
    import json as _json5

    from devbrief.eval import summarize_extractive as _sext
    from devbrief.eval import write_extractive_summary as _wxs

    rej: list[dict] = []
    mem: list[dict] = []
    for f in sorted(out.glob("*/*.json")):
        if f.name.startswith("summary"):
            continue
        try:
            d = _json5.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if d.get("mode") != "extractive":
            continue
        r = d.get("result") or {}
        if only and r.get("id") not in only:
            continue
        if "verdict" in r:
            rej.append(r)
        elif "ok" in r:
            mem.append(r)
    rep = _sext(rej, mem)
    _wxs(rep, out / "summary_metadata.md")
    typer.echo(f"extractive: red {rep['reject']['rejected']}/{rep['reject']['total']},"
               f" uyelik {rep['membership']['passed']}/{rep['membership']['total']}")


def _run_eval_metadata_extractive(out: Path, ext_cases: list, all_cases: list) -> None:
    """Extractive degerlendirme kosusu (tamamen cevrimdisi, model yok).

    e/f/g: mock ham secim -> deterministik ret. 24 vaka: uyelik + mock
    secimle ornek FINAL. Sonuclar out/extractive/<id>.json + ozet md.
    """
    import json as _json6

    from devbrief.eval import run_extractive_reject_case, run_membership_case
    from devbrief.eval import summarize_extractive as _sext2
    from devbrief.eval import write_extractive_summary as _wxs2

    mdir = out / "extractive"
    mdir.mkdir(parents=True, exist_ok=True)
    rej: list[dict] = []
    for c in ext_cases:
        res = run_extractive_reject_case(c)
        rej.append(res)
        (mdir / f"{c.get('id')}.json").write_text(
            _json6.dumps({"mode": "extractive", "result": res},
                         ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        typer.echo(f"  {c.get('id')} ({c.get('kind')}): {res['verdict']}"
                   f" [{res['layer']}] {res['ms']:.0f} ms")
    mem: list[dict] = []
    for c in all_cases:
        res = run_membership_case(c)
        mem.append(res)
        (mdir / f"{c.get('id')}.json").write_text(
            _json6.dumps({"mode": "extractive", "result": res},
                         ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        mark = "disi" if res["ok"] else "ICTE"
        if res.get("kind") == "clean":
            mark = "temiz"
        typer.echo(f"  {c.get('id')} ({c.get('kind')}): uyelik {mark}"
                   f" {res['ms']:.0f} ms")
    rep = _sext2(rej, mem)
    _wxs2(rep, out / "summary_metadata.md")
    typer.echo(f"extractive: red {rep['reject']['rejected']}/{rep['reject']['total']},"
               f" uyelik {rep['membership']['passed']}/{rep['membership']['total']}")
    typer.echo("not: akicilik elle okunmali (out/eval/summary_metadata.md)")


@app.command("eval-metadata")
def eval_metadata(
    cases: str = typer.Option(
        "tests/fixtures/metadata_cases.json", help="24 vaka (6 cift x 4 varyant)"),
    out_dir: str = typer.Option("out/eval", help="ham cikti + summary_metadata.md"),
    yes: bool = typer.Option(False, "--yes", help="butce onayini atla"),
    models: str = typer.Option("", "--models", help="virgullu alt-dize filtresi (bos=hepsi)"),
    any_model: str = typer.Option("", "--any-model", help="listede olmayan tek model ID'si"),
    only: str = typer.Option("", "--only", help="vaka secimi: M1a,M2b (bos=hepsi)"),
    summarize_only: bool = typer.Option(
        False, "--summarize-only", help="API'siz: kayitli vaka dosyalarindan ozet kur"
    ),
    mode: str = typer.Option(
        "generative", "--mode", help="extractive|generative (degerlendirme hatti)"),
    extractive_cases: str = typer.Option(
        "tests/fixtures/metadata_extractive_cases.json",
        help="e/f/g secim-red vakalari (extractive mod)"),
) -> None:
    """Metadata denetim kapisi: her vaka icin det + tek LLM entailment istegi.

    Gercek API'ye istek atan eval komutlarindan biri (testler mock kullanir).
    Vaka basina: deterministik kapsama + degisim kurali, ardindan kalan
    cumleler icin TEK verify istegi. Rapor: katman yakalama, birlesik recall
    (>= %90), temiz yanlis-red (<= %10). Kapi yalnizca raporlanir.

    --mode extractive: secim-red (e/f/g) + 24 vaka uyelik, tamamen cevrimdisi
    (model secimi yok, kapi kaydi yazilmaz).
    """
    import asyncio
    import json as _json
    import time as _time

    import httpx

    import devbrief.db as db
    from devbrief.config import get_settings
    from devbrief.eval import (
        Bucket,
        load_metadata_cases,
        render_metadata_table,
        summarize_metadata,
        write_metadata_summary,
    )
    from devbrief.eval import (
        slot_status as _slot_status,
    )

    get_settings.cache_clear()
    s = get_settings()
    eval_mode = (mode or "generative").strip().lower()
    if eval_mode not in ("extractive", "generative"):
        typer.echo(f"bilinmeyen mod: {mode!r} (extractive|generative olmali)", err=True)
        raise typer.Exit(code=2)
    all_cases = load_metadata_cases(cases)
    ext_cases: list = []
    if eval_mode == "extractive":
        from devbrief.eval import load_extractive_cases as _load_ext

        ext_cases = _load_ext(extractive_cases)
    if only:
        picked = {p.strip() for p in only.split(",") if p.strip()}
        known = {c.get("id") for c in all_cases}
        if eval_mode == "extractive":
            known |= {c.get("id") for c in ext_cases}
        unknown = sorted(picked - known)
        if unknown:
            typer.echo(f"bilinmeyen vaka: {', '.join(unknown)}; "
                       f"gecerli: {', '.join(sorted(known))}", err=True)
            raise typer.Exit(code=2)
        all_cases = [c for c in all_cases if c.get("id") in picked]
        ext_cases = [c for c in ext_cases if c.get("id") in picked]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if summarize_only:
        if eval_mode == "extractive":
            _summarize_extractive_saved(out, {p.strip() for p in only.split(",")
                                              if p.strip()} if only else None)
        else:
            _summarize_metadata_saved(out, all_cases)
        raise typer.Exit(code=0)
    if eval_mode == "extractive":
        if models.strip() or any_model.strip():
            typer.echo("not: extractive modda model secimi yok (LLM cagrisi yok)")
        _run_eval_metadata_extractive(out, ext_cases, all_cases)
        return
    if not s.openrouter_api_key:
        typer.echo("OPENROUTER_API_KEY bos", err=True)
        raise typer.Exit(code=1)
    pool = list(dict.fromkeys(s.all_models + s.verify_models))
    if any_model.strip():
        if not s.model_allowed(any_model.strip()):
            typer.echo(f"ucretli korumasi: {any_model.strip()} reddedildi "
                       "(ALLOW_PAID_MODELS + allowlist gerekli)", err=True)
            raise typer.Exit(code=2)
        wanted = [any_model.strip()]
    else:
        for m in pool:
            if not s.model_allowed(m):
                typer.echo(f"atlandi (ucretli korumasi): {m}", err=True)
        pool = [m for m in pool if s.model_allowed(m)]
        wanted = [m for m in pool
                  if not models or any(f.strip() in m for f in models.split(",") if f.strip())]
        if models and not wanted:
            typer.echo(f"bilinmeyen model filtresi: {models!r}; "
                       f"gecerli: {', '.join(pool)}", err=True)
            raise typer.Exit(code=2)
    if not wanted:
        typer.echo("METADATA model listesi bos", err=True)
        raise typer.Exit(code=1)
    planned = len(all_cases) * len(wanted)
    typer.echo(f"{len(all_cases)} vaka x {len(wanted)} model = {planned} istek")
    if not yes and not typer.confirm("Butceye islensin mi? Devam?"):
        raise typer.Exit(code=0)

    bucket = Bucket(s.llm_rate_per_minute)
    conn = db.connect()
    db.migrate(conn)
    reports = []

    async def _post(model: str, prompt: str) -> tuple[str, float]:
        await bucket.wait()
        slot = db.reserve_llm_slot(conn, "eval-metadata", model, s.llm_daily_budget)
        t0 = _time.perf_counter()
        text, ok, finish, http_status, exc = "", False, None, None, None
        try:
            async with httpx.AsyncClient(timeout=s.timeout_for(s.tier_of(model))) as client:
                r = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {s.openrouter_api_key}"},
                    json={"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 1200, "temperature": 0.0},
                )
                http_status = r.status_code
                if r.status_code == 200:
                    try:
                        msg = r.json()["choices"][0]["message"]
                        content = msg.get("content")
                        finish = msg.get("finish_reason")
                        text = content if isinstance(content, str) else ""
                        ok = bool(text)
                    except (KeyError, IndexError, ValueError):
                        text, ok = r.text[:2000], False
                else:
                    text, ok = f"HTTP {r.status_code}: {r.text[:500]}", False
        except Exception as e:  # noqa: BLE001
            text, ok, exc = f"{type(e).__name__}: {e}", False, type(e).__name__
        finally:
            db.finish_llm_slot(
                conn, slot, ok=ok, ms=int((_time.perf_counter() - t0) * 1000),
                status=_slot_status(http_status, text, finish, exc))
        return text, (_time.perf_counter() - t0) * 1000

    async def _run() -> None:
        from devbrief.eval import run_metadata_case

        for m in wanted:
            results = []
            mdir = out / m.replace("/", "_").replace(":", "_")
            mdir.mkdir(parents=True, exist_ok=True)
            for c in all_cases:
                res = await run_metadata_case(c, m, _post)
                results.append(res)
                (mdir / f"{c.get('id')}.json").write_text(
                    _json.dumps({"model": m, "case": c.get("id"),
                                 "ms": round(res["ms"]),
                                 "output": res["output"]},
                                ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                n_bad = len(c.get("bad") or [])
                typer.echo(f"  {m} {c.get('id')} ({c.get('kind')}, kotu={n_bad}): "
                           f"{res['ms']:.0f} ms")
            reports.append(summarize_metadata(m, results))

    try:
        asyncio.run(_run())
    except db.BudgetExceeded:
        typer.echo("UYARI: gunluk butce doldu; tamamlanan modellerin kismi ozeti yazildi.",
                   err=True)
    if reports:
        passed = all(r.qualifies for r in reports)
        db.record_eval_gate(conn, "eval-metadata", passed,
                            min(r.recall for r in reports),
                            max(r.fp_rate for r in reports))
    conn.close()
    write_metadata_summary(reports, out / "summary_metadata.md")
    typer.echo(render_metadata_table(reports))
    for r in reports:
        mark = "GECTI" if r.qualifies else "KALDI"
        typer.echo(f"METADATA kapisi {r.model}: {mark} "
                   f"(birlesik %{r.recall:.0%}, temiz FP %{r.fp_rate:.0%})")


@app.command()
def collect(
    source: str = typer.Option("all", "--source", help="rss|hn|github|all"),
) -> None:
    """Faz 1: sources.yaml'daki kaynaklari cek, DB'ye yaz (kaynak hatasi izole)."""
    import asyncio

    import devbrief.db as db
    from devbrief.collect import github_releases, hn, rss
    from devbrief.collect.base import FetchError, RawItem
    from devbrief.config import get_settings, load_sources

    async def _store(conn, name: str, items: list[RawItem], totals: dict[str, int]) -> None:
        for it in items:
            db.upsert_item(
                conn,
                source=name,
                url=it.url,
                title=it.title,
                summary=it.summary,
                published_at=it.published_at,
                raw_json=it.raw_json,
            )
        db.log_source_run(conn, name, True, len(items))
        totals["items"] += len(items)
        totals["sources"] += 1

    async def _run() -> dict[str, int]:
        get_settings.cache_clear()
        token = get_settings().github_token or None
        cfg = load_sources()
        conn = db.connect()
        db.migrate(conn)
        totals = {"items": 0, "sources": 0, "failed": 0}
        if source in ("all", "rss"):
            for f in cfg.get("rss") or []:
                if not isinstance(f, dict) or f.get("enabled", True) is False:
                    continue
                name = str(f.get("name", f.get("url", "?")))
                try:
                    items = await rss.collect(
                        {"name": name, "url": f["url"], "max_age_hours": 30}
                    )
                    await _store(conn, name, items, totals)
                except (FetchError, KeyError) as e:  # noqa: BLE001 - kaynak izole
                    db.log_source_run(conn, name, False, 0, str(e))
                    totals["failed"] += 1
        if source in ("all", "hn"):
            hn_cfg = dict(cfg.get("hn") or {})
            try:
                await _store(conn, "hn", await hn.collect(hn_cfg), totals)
            except FetchError as e:  # noqa: BLE001
                db.log_source_run(conn, "hn", False, 0, str(e))
                totals["failed"] += 1
        if source in ("all", "github"):
            for g in cfg.get("github_releases") or []:
                if not isinstance(g, dict) or not g.get("repo"):
                    continue
                name = f"github:{g['repo']}"
                try:
                    items = await github_releases.collect(dict(g), token=token)
                    await _store(conn, name, items, totals)
                except (FetchError, KeyError) as e:  # noqa: BLE001
                    db.log_source_run(conn, name, False, 0, str(e))
                    totals["failed"] += 1
        conn.close()
        return totals

    totals = asyncio.run(_run())
    typer.echo(
        f"collect: {totals['sources']} kaynak,"
        f" {totals['items']} girdi ({totals['failed']} hata)"
    )


@app.command()
def curate(
    dry_run: bool = typer.Option(False, "--dry-run", help="DB'ye yazmadan tablo yazdir"),
    limit: int = typer.Option(40, "--limit", help="aday girdi sayisi"),
) -> None:
    """Faz 2: puanla -> siniflandir -> kumele -> sec; gerekceli tablo."""
    import asyncio
    import json as _json

    import devbrief.db as db
    from devbrief.config import get_settings, load_sources
    from devbrief.curate import prefilter as pf
    from devbrief.curate import rank as _rank
    from devbrief.curate import select as _select

    get_settings.cache_clear()
    s = get_settings()
    weights: dict[str, float] = {}
    for f in (load_sources().get("rss") or []):
        if isinstance(f, dict) and f.get("name"):
            weights[str(f["name"])] = float(f.get("weight", 0.5))
    for g in (load_sources().get("github_releases") or []):
        if isinstance(g, dict) and g.get("repo"):
            weights[f"github:{g['repo']}"] = float(g.get("weight", 0.5))
    weights.setdefault("hn", 0.7)

    conn = db.connect()
    db.migrate(conn)
    rows = db.get_new_items(conn, limit=limit)
    for r in rows:
        try:
            pts = int((_json.loads(r["raw_json"] or "{}")).get("points", 0))
        except (ValueError, AttributeError):
            pts = 0
        total, _ = pf.score(
            r["title"] or "", r["summary"] or "", source=str(r["source"] or ""),
            source_weight=weights.get(r["source"], 0.5), hn_points=pts,
            published_at=r["published_at"], w_source=s.prefilter_w_source,
            w_pop=s.prefilter_w_pop, w_fresh=s.prefilter_w_fresh, w_kw=s.prefilter_w_kw,
        )
        if not dry_run:
            db.update_prescore(conn, int(r["id"]), total)
    rows = db.get_new_items(conn, limit=limit)

    async def _run():
        classified = await _rank.run(conn, limit=limit, dry_run=dry_run)
        classes = {c.id: {"importance": c.importance, "category": c.category,
                          "reason": c.reason} for c in classified.results}
        infos = _select.make_clusters(
            rows, jaccard_thr=s.dedupe_jaccard, difflib_thr=s.dedupe_difflib,
            max_size=s.dedupe_max_size)
        if not dry_run:
            infos = _select.persist_clusters(conn, _select.today(), infos)
        out = await _select.select_day(infos, classes)
        if not dry_run:
            _select.apply_selection(conn, out)
        return classified, infos, out

    classified, infos, out = asyncio.run(_run())
    id2topic = {int(info.get("id", idx)): info["topic"] for idx, info in enumerate(infos, 1)}
    typer.echo(f"curate: {len(rows)} girdi, {len(classified.results)} sinif, "
               f"{len(infos)} kume{' (dry-run)' if dry_run else ''}")
    for p in out.picks:
        typer.echo(f"SECILDI K{p.cluster_id} (birlesen: {p.merged_cluster_ids or '-'}): "
                   f"{id2topic.get(p.cluster_id, '?')[:80]}")
        typer.echo(f"  neden: {p.why_today[:160]}")
    typer.echo(f"YEDEK K{out.backup.cluster_id}: {id2topic.get(out.backup.cluster_id, '?')[:80]}")
    typer.echo(f"  neden: {out.backup.why_today[:160]}")
    conn.close()


@app.command()
def prefilter(
    show: bool = typer.Option(False, "--show", help="ilk 40 + esik alti 20'yi tablo yazdir"),
    limit: int = typer.Option(500, "--limit", help="puanlanacak en fazla girdi"),
) -> None:
    """LLM'siz on puanlama: girdilere prescore yaz (katsayilar .env PREFILTER_W_*)."""
    import json as _json

    import devbrief.db as db
    from devbrief.config import get_settings, load_sources
    from devbrief.curate import dedupe as _dedupe
    from devbrief.curate import prefilter as pf

    get_settings.cache_clear()
    s = get_settings()
    weights: dict[str, float] = {}
    for f in (load_sources().get("rss") or []):
        if isinstance(f, dict) and f.get("name"):
            weights[str(f["name"])] = float(f.get("weight", 0.5))
    for g in (load_sources().get("github_releases") or []):
        if isinstance(g, dict) and g.get("repo"):
            weights[f"github:{g['repo']}"] = float(g.get("weight", 0.5))
    weights.setdefault("hn", 0.7)

    conn = db.connect()
    db.migrate(conn)
    rows = conn.execute(
        "SELECT * FROM items WHERE status = 'new' ORDER BY id ASC LIMIT ?", (limit,)
    ).fetchall()
    for r in rows:
        try:
            pts = int((_json.loads(r["raw_json"] or "{}")).get("points", 0))
        except (ValueError, AttributeError):
            pts = 0
        total, _ = pf.score(
            r["title"] or "",
            r["summary"] or "",
            source=str(r["source"] or ""),
            source_weight=weights.get(r["source"], 0.5),
            hn_points=pts,
            published_at=r["published_at"],
            w_source=s.prefilter_w_source,
            w_pop=s.prefilter_w_pop,
            w_fresh=s.prefilter_w_fresh,
            w_kw=s.prefilter_w_kw,
        )
        db.update_prescore(conn, int(r["id"]), total)
    typer.echo(f"prefilter: {len(rows)} girdi puanlandi")
    if show:
        scored = [
            {
                "id": str(r["id"]),
                "source": str(r["source"]),
                "title": r["title"] or "",
                "raw": float(r["prescore"] or 0.0),
                "canonical_url": r["canonical_url"] or "",
            }
            for r in conn.execute(
                "SELECT id, source, title, prescore, canonical_url FROM items"
                " WHERE status = 'new'"
            ).fetchall()
        ]
        normed = pf.normalize_within_source(scored)
        picked, reasons = pf.select(
            normed,
            per_source_cap=s.prefilter_per_source_cap,
            total=s.prefilter_total,
        )
        picked_ids = {p["id"] for p in picked}
        clusters = _dedupe.cluster(
            scored,
            jaccard_thr=s.dedupe_jaccard,
            difflib_thr=s.dedupe_difflib,
            max_size=s.dedupe_max_size,
        )
        cmap: dict[str, int] = {}
        quality: dict[int, float] = {}
        for ci, members in enumerate(clusters):
            quality[ci] = _dedupe.cluster_quality(members)
            for m in members:
                cmap[str(m["id"])] = ci

        def _clabel(ci: int) -> str:
            q = quality.get(ci, 1.0)
            flag = "?" if q < 0.5 else " "
            return f"K{ci}~{q:.2f}{flag}"
        seen_clusters: set[int] = set()
        ordered = sorted(normed, key=lambda d: d["norm"], reverse=True)
        typer.echo(f"--- secilen {len(picked)} (norm sira, kota={s.prefilter_per_source_cap}) ---")
        n = 0
        for d in ordered:
            if d["id"] not in picked_ids:
                continue
            n += 1
            ci = cmap.get(d["id"], -1)
            dup = f"tekrar->{_clabel(ci)}" if ci in seen_clusters else _clabel(ci)
            seen_clusters.add(ci)
            typer.echo(
                f"{n:2d} n={d['norm']:.2f} {reasons.get(d['id'], '?'):13s} "
                f"{dup:16s} [{d['source']}] {d['title'][:80]}"
            )
            if n >= s.prefilter_total:
                break
        typer.echo("--- elenen (ilk 20) ---")
        shown = 0
        for d in ordered:
            if d["id"] in picked_ids or shown >= 20:
                continue
            ci = cmap.get(d["id"], -1)
            typer.echo(
                f"     n={d['norm']:.2f} {reasons.get(d['id'], '?'):13s} "
                f"{_clabel(ci):16s} [{d['source']}] {d['title'][:80]}"
            )
            shown += 1
    conn.close()


@app.command()
def render(
    episode: int | None = typer.Option(None, "--episode", help="DB episode id"),
    script: str = typer.Option(None, "--script", help="senaryo JSON dosyasi (DB'siz)"),
    out: str = typer.Option("out/_sample", "--out", help="--script ile cikti dizini"),
    allow_no_metadata: bool = typer.Option(
        False, "--allow-no-metadata", help="yalnizca is_demo bolumlerde gecerli"),
) -> None:
    """Render hatti: TTS -> altyazi -> slayt -> ffmpeg (LLM yok)."""
    import asyncio
    import json as _json
    from datetime import UTC, datetime

    import devbrief.db as db
    from devbrief.config import get_settings
    from devbrief.render import compose as _compose
    from devbrief.render import slides as _slides
    from devbrief.render import subtitles as _subs
    from devbrief.render import tts as _tts

    if (episode is None) == (script is None):
        typer.echo("--episode veya --script'ten biri gerekli", err=True)
        raise typer.Exit(code=2)
    if episode is not None:
        conn = db.connect()
        db.migrate(conn)
        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode,)).fetchone()
        conn.close()
        if row is None:
            typer.echo(f"episode {episode} bulunamadi", err=True)
            raise typer.Exit(code=1)
        try:
            low = bool(row["low_source"])
        except (KeyError, IndexError):
            low = False
        if low:
            typer.echo(f"episode {episode} low_source: yetersiz kaynak, ilerlemez", err=True)
            raise typer.Exit(code=1)
        if not row["script_json"]:
            typer.echo(f"episode {episode} senaryosuz", err=True)
            raise typer.Exit(code=1)
        data = _json.loads(row["script_json"])
        if low or data.get("insufficient_source"):
            typer.echo(f"episode {episode} low_source: yetersiz kaynak, ilerlemez", err=True)
            raise typer.Exit(code=1)
        try:
            demo = bool(row["is_demo"])
        except (KeyError, IndexError):
            demo = False
        has_meta = bool(row["metadata_json"])
        failed_meta = (row["status"] == "metadata_failed")
        if (not has_meta or failed_meta) and not (allow_no_metadata and demo):
            why = "metadata_failed" if failed_meta else "metadatasiz"
            typer.echo(f"episode {episode} {why}: once `metadata`", err=True)
            raise typer.Exit(code=1)
        slug = row["slug"] or f"ep-{episode}"
        day = row["day"] or datetime.now(UTC).date().isoformat()
    else:
        assert script is not None
        data = _json.loads(_compose.Path(script).read_text(encoding="utf-8"))
        if data.get("insufficient_source"):
            typer.echo("senaryo insufficient_source: yetersiz kaynak, ilerlemez", err=True)
            raise typer.Exit(code=1)
        slug = _compose.Path(script).stem
        day = datetime.now(UTC).date().isoformat()

    get_settings.cache_clear()
    voice = get_settings().tts_voice
    scenes = list(data.get("scenes") or [])
    if not scenes:
        typer.echo("sahne yok", err=True)
        raise typer.Exit(code=1)
    out_root = "out" if episode is not None else out

    import time as _time

    t0 = _time.perf_counter()

    async def _run():
        audio_dir = _compose.Path(out_root) / day / slug / "_audio"
        audio = await _tts.synth_script(scenes, voice, audio_dir)
        return audio

    audio = asyncio.run(_run())
    t_tts = _time.perf_counter() - t0
    final_dir = _compose.Path(out_root) / day / slug
    final_dir.mkdir(parents=True, exist_ok=True)
    slide_paths = [
        _slides.render_scene(sc, hook=str(data.get("hook") or ""), index=i,
                             total=len(scenes), out=final_dir / "_slides" / f"scene_{i + 1}.png")
        for i, sc in enumerate(scenes)
    ]
    t_slides = _time.perf_counter() - t0 - t_tts
    # Altyazi: sahne kelimelerini kumulatif ses sureleriyle global zamana tasi.
    durs = [_compose.probe_duration(a["mp3"]) for a in audio]
    if _compose.over_limit(sum(durs)):
        typer.echo(
            f"UYARI: ses toplami {sum(durs):.1f} sn > "
            f"{_compose.DURATION_LIMIT_S:.0f} sn; kisaltma turu gerekli (writer kilitli)",
            err=True,
        )
    groups: list[dict] = []
    offset = 0.0
    for a, dur in zip(audio, durs, strict=True):
        for g in _subs.group_words(a["words"]):
            groups.append(
                {"start": g["start"] + offset, "end": g["end"] + offset, "text": g["text"]}
            )
        offset += dur
    _subs.write_ass(groups, final_dir / "subs.ass")
    _subs.write_srt(groups, final_dir / "subs.srt")
    _slides.render_thumb(str(data.get("hook") or slug), final_dir / "thumb.png")
    meta = _compose.build(
        scenes, audio, slide_paths, slug=slug, day=day,
        ass_path=final_dir / "subs.ass", out_root=out_root,
        source_url=str(data.get("source_url") or ""),
    )
    t_mux = _time.perf_counter() - t0 - t_tts - t_slides
    typer.echo(f"render: {final_dir}/video.mp4 ({meta['total_s']} sn)")
    typer.echo(
        f"sureler: tts={t_tts:.1f}sn slayt={t_slides:.1f}sn "
        f"ffmpeg={t_mux:.1f}sn toplam={_time.perf_counter() - t0:.1f}sn"
    )


@app.command()
def script(
    episode: int | None = typer.Option(None, "--episode", help="DB episode id"),
    init_day: str | None = typer.Option(
        None, "--init-day", help="secili kumelerden bolum ac (YYYY-MM-DD)"),
) -> None:
    """Faz 3: cek -> yaz -> kural+LLM dogrula (max 2 yeniden yazim) -> verified/failed."""
    import asyncio
    import json as _json
    import re as _re
    from pathlib import Path as _P

    import devbrief.db as db
    from devbrief.script import fetch as _fetch
    from devbrief.script import injection as _inj
    from devbrief.script import verifier as _verifier
    from devbrief.script import writer as _writer

    if (episode is None) == (init_day is None):
        typer.echo("--episode veya --init-day'den biri gerekli", err=True)
        raise typer.Exit(code=2)

    conn = db.connect()
    db.migrate(conn)
    if init_day is not None:
        rows = conn.execute(
            "SELECT id, topic FROM clusters WHERE day = ? AND selected = 1", (init_day,)
        ).fetchall()
        made = 0
        for r in rows:
            exists = conn.execute(
                "SELECT id FROM episodes WHERE cluster_id = ?", (r["id"],)).fetchone()
            if exists:
                continue
            slug = _re.sub(r"[^a-z0-9]+", "-", str(r["topic"] or "").lower()).strip("-")[:50]
            slug = f"{slug}-c{r['id']}" if slug else f"gun-{init_day}-c{r['id']}"
            with conn:
                conn.execute(
                    "INSERT INTO episodes (cluster_id, day, slug, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, 'draft', ?, ?)",
                    (r["id"], init_day, slug, db.now_iso(), db.now_iso()),
                )
            made += 1
        conn.close()
        typer.echo(f"script: {made} bolum acildi ({init_day})")
        return

    assert episode is not None
    row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode,)).fetchone()
    if row is None:
        typer.echo(f"episode {episode} yok", err=True)
        raise typer.Exit(code=1)
    day = row["day"] or "undated"
    slug = row["slug"] or f"ep-{episode}"
    target = _P("out") / day / slug / "fetch.json"

    source_text, source_url = "", ""
    if target.exists():
        try:
            saved = _json.loads(target.read_text(encoding="utf-8"))
            source_text, source_url = saved.get("text", ""), saved.get("url", "")
        except ValueError:
            pass
    if not source_text:
        items = conn.execute(
            "SELECT i.url, i.summary FROM cluster_items c JOIN items i ON i.id = c.item_id"
            " WHERE c.cluster_id = ? ORDER BY i.id ASC LIMIT 3",
            (row["cluster_id"],),
        ).fetchall()
        if not items or not items[0]["url"]:
            db.set_episode_status(conn, episode, "failed", reason="kaynak URL yok")
            conn.close()
            typer.echo("kaynak URL yok -> failed", err=True)
            raise typer.Exit(code=1)
        res = _fetch.fetch_article(items[0]["url"], items[0]["summary"] or "")
        source_text, source_url = res.text, res.url
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_json.dumps(
            {"url": res.url, "text": res.text, "source_hash": res.source_hash,
             "low_source": res.low_source, "reason": res.reason},
            ensure_ascii=False, indent=2), encoding="utf-8")
        if res.low_source:
            typer.echo(f"LOW_SOURCE: {res.reason}")
    if not source_text.strip():
        db.set_episode_status(conn, episode, "failed", reason="kaynak metin bos")
        conn.close()
        typer.echo("kaynak metin bos -> failed", err=True)
        raise typer.Exit(code=1)
    conn.execute(
        "UPDATE episodes SET source_text_hash = ?, updated_at = ? WHERE id = ?",
        (_fetch.text_hash(source_text), db.now_iso(), episode),
    )
    conn.commit()
    clean_text, removed = _inj.scan(source_text)
    if removed:
        _inj.record_flag(conn, episode, removed)
        typer.echo(f"INJECTION_SUSPECTED: {len(removed)} cumle cikarildi")

    async def _run():
        first = await _writer.write_script(clean_text, source_url, conn=conn, removed=removed)
        if first.insufficient_source:
            return "low_source", first.model_dump(), None, first.reason or "yetersiz kaynak"
        return await _verifier.verify_episode(
            first.model_dump(), clean_text, conn=conn, removed=removed)

    try:
        status, final, verdict, reason = asyncio.run(_run())
    except Exception as e:  # noqa: BLE001 - yazar coktuyse de bolum failed olur
        status, final, verdict = "failed", None, None
        reason = f"{type(e).__name__}: {e}"[:2000]
    with conn:
        if status == "verified" and final is not None:
            from devbrief.script.schemas import Script as _Script

            model = _Script.model_validate(final)
            warns = model.content_warnings()
            infs = [c.text[:100] for c in model.inferences()]
            conn.execute(
                "UPDATE episodes SET script_json = ?, verify_json = ?, status = ?,"
                " updated_at = ? WHERE id = ?",
                (_json.dumps(final, ensure_ascii=False),
                 verdict.model_dump_json() if verdict else "", status, db.now_iso(), episode),
            )
            if warns:
                typer.echo("uyarilar: " + "; ".join(warns))
            for t in infs:
                typer.echo(f"yorum: {t}")
            if removed:
                typer.echo("onay: INJECTION_SUSPECTED bayrakli")
        elif status == "low_source":
            conn.execute(
                "UPDATE episodes SET script_json = ?, low_source = 1, updated_at = ?"
                " WHERE id = ?",
                (_json.dumps(final, ensure_ascii=False), db.now_iso(), episode),
            )
        else:
            conn.execute(
                "UPDATE episodes SET status = 'failed', reject_reason = ?, updated_at = ?"
                " WHERE id = ?",
                (reason, db.now_iso(), episode),
            )
    conn.close()
    typer.echo(f"script: episode {episode} -> {status}" + (f" ({reason[:120]})" if reason else ""))


@app.command()
def metadata(
    episode: int = typer.Option(..., "--episode", help="DB episode id"),
    mode: str | None = typer.Option(
        None, "--mode", help="extractive|generative (bos: METADATA_MODE)"),
) -> None:
    """Senaryodan baslik/aciklama/gonderi uret: kural denetimli (max 2 tur)."""
    import asyncio
    import json as _json
    from pathlib import Path as _P

    import devbrief.db as db
    from devbrief.config import get_settings
    from devbrief.script import metadata as _meta

    get_settings.cache_clear()
    conn = db.connect()
    db.migrate(conn)
    row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode,)).fetchone()
    if row is None or not row["script_json"]:
        typer.echo(f"episode {episode} yok/senaryosuz", err=True)
        raise typer.Exit(code=1)
    try:
        script = _json.loads(row["script_json"])
    except ValueError:
        typer.echo("senaryo JSON bozuk", err=True)
        raise typer.Exit(code=1) from None
    source_text = ""
    day, slug = row["day"] or "undated", row["slug"] or f"ep-{episode}"
    fetch_file = _P("out") / day / slug / "fetch.json"
    if fetch_file.exists():
        try:
            source_text = _json.loads(fetch_file.read_text(encoding="utf-8")).get("text", "")
        except ValueError:
            pass

    from devbrief.llm.client import CallCap, CallCapExceeded

    try:
        resolved_mode = _meta.resolve_metadata_mode(mode)
    except ValueError as e:
        typer.echo(f"metadata: {e}", err=True)
        raise typer.Exit(code=2) from None

    async def _run():
        try:
            meta, warns, budget, support = await _meta.produce_auto(
                script, source_text, conn=conn,
                cap=CallCap(8 if resolved_mode == "generative" else 6),
                mode=resolved_mode)
            return meta, warns, budget, support, []
        except _meta.MetadataFailed as e:
            return None, [str(e)[:500]], None, [], list(e.last_errors)
        except CallCapExceeded as e:
            return None, [f"call_cap: {e}"[:200]], None, [], [f"call_cap: {e}"[:200]]
        except Exception as e:  # noqa: BLE001 - sızdırmazlık: tura dahil edilemeyen hata
            return None, [f"{type(e).__name__}: {e}"[:200]], None, [], [
                f"{type(e).__name__}: {e}"[:200]]

    try:
        meta, warns, budget, support, last_errors = asyncio.run(_run())
    except CallCapExceeded as e:
        meta, warns, budget, support, last_errors = (
            None, [f"call_cap: {e}"[:200]], None, [], [f"call_cap: {e}"[:200]])
    except Exception as e:  # noqa: BLE001 - hicbir istisna sizamaz
        try:
            _meta.fail_metadata(conn, episode, f"{type(e).__name__}: {e}"[:500])
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        typer.echo(f"metadata HATA: {type(e).__name__}", err=True)
        raise typer.Exit(code=1) from None
    if meta is None:
        _meta.fail_metadata(conn, episode, "; ".join(warns)[:2000])
        conn.close()
        typer.echo(f"metadata: episode {episode} -> metadata_failed", err=True)
        for e in (last_errors or warns)[:8]:
            typer.echo(f"ret: {e}"[:300], err=True)
        raise typer.Exit(code=1)
    _meta.save_metadata(conn, episode, meta, warns, budget, support)
    conn.close()
    typer.echo(f"baslik: {meta.title}")
    typer.echo(f"x: {meta.x_post}")
    typer.echo(f"linkedin: {meta.linkedin_post[:200]}")
    for w in warns:
        typer.echo(f"uyari: {w}")
    typer.echo(f"metadata: episode {episode} -> ok")


@app.command()
def backup() -> None:
    """Guvenli DB yedegi (backup API + integrity + rotasyon)."""
    from devbrief.publish import backup as _backup

    try:
        dest = _backup.take_backup()
    except RuntimeError as e:
        typer.echo(f"backup HATA: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"backup: {dest}")


@app.command()
def restore(
    dosya: str = typer.Argument(..., help="geri yuklenecek yedek dosyasi"),
    yes: bool = typer.Option(False, "--yes", help="onayi atla"),
) -> None:
    """Yedekten geri yukle (canli DB once .bak alinir)."""
    from devbrief.publish import backup as _backup

    if not yes and not typer.confirm(f"{dosya} geri yuklensin mi? Canli DB .bak alinir."):
        raise typer.Exit(code=0)
    try:
        bak = _backup.restore(dosya)
    except RuntimeError as e:
        typer.echo(f"restore HATA: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"restore: tamam (.bak: {bak})")


@app.command("seed-demo-episode")
def seed_demo_episode(
    fixture: str = typer.Option(
        "tests/fixtures/sample_script.json", help="ornek senaryo dosyasi"),
) -> None:
    """Demo bolum: LLM'siz render edip `rendered` yapar (n8n akis testi icin).

    Baslik "[TEST] ..." olur, is_demo=1 isaretlenir; hicbir modda public olamaz.
    """
    import json as _json
    from datetime import UTC, datetime

    import devbrief.db as db
    from devbrief.config import get_settings
    from devbrief.render import compose as _compose
    from devbrief.render import slides as _slides
    from devbrief.render import subtitles as _subs
    from devbrief.render import tts as _tts

    data = _json.loads(_compose.Path(fixture).read_text(encoding="utf-8"))
    day = datetime.now(UTC).date().isoformat()
    slug = f"demo-{day}"
    get_settings.cache_clear()
    voice = get_settings().tts_voice
    scenes = list(data.get("scenes") or [])

    async def _run():
        return await _tts.synth_script(scenes, voice, _compose.Path("out") / day / slug / "_audio")

    import asyncio

    audio = asyncio.run(_run())
    final_dir = _compose.Path("out") / day / slug
    final_dir.mkdir(parents=True, exist_ok=True)
    slide_paths = [
        _slides.render_scene(sc, hook=str(data.get("hook") or ""), index=i,
                             total=len(scenes), out=final_dir / "_slides" / f"scene_{i + 1}.png")
        for i, sc in enumerate(scenes)]
    durs = [_compose.probe_duration(a["mp3"]) for a in audio]
    groups, offset = [], 0.0
    for a, dur in zip(audio, durs, strict=True):
        for g in _subs.group_words(a["words"]):
            groups.append({"start": g["start"] + offset, "end": g["end"] + offset,
                           "text": g["text"]})
        offset += dur
    _subs.write_ass(groups, final_dir / "subs.ass")
    _slides.render_thumb(str(data.get("hook") or slug), final_dir / "thumb.png")
    meta = _compose.build(scenes, audio, slide_paths, slug=slug, day=day,
                          ass_path=final_dir / "subs.ass", out_root="out",
                          source_url=str(data.get("source_url") or ""))
    conn = db.connect()
    db.migrate(conn)
    with conn:
        cur = conn.execute(
            "INSERT INTO episodes (cluster_id, day, slug, status, script_json,"
            " video_path, thumb_path, title, is_demo, created_at, updated_at)"
            " VALUES (NULL, ?, ?, 'rendered', ?, ?, ?, ?, 1, ?, ?)",
            (day, slug, _json.dumps(data, ensure_ascii=False),
             str(final_dir / "video.mp4"), str(final_dir / "thumb.png"),
             f"[TEST] {data.get('hook', slug)}"[:120], db.now_iso(), db.now_iso()))
        epid = int(cur.lastrowid)
    conn.close()
    typer.echo(f"demo: episode {epid} rendered ({meta['total_s']} sn)")


@app.command("deploy-check")
def deploy_check(
    api_url: str = typer.Option("", "--api-url", help="calisan API (or. http://127.0.0.1:8000)"),
) -> None:
    """Devreye alma kontrolleri. FAIL varsa exit 1."""
    from devbrief import deploy as _deploy

    findings = []
    findings += _deploy.check_compose()
    findings += _deploy.check_git()
    findings += _deploy.check_env()
    findings += _deploy.check_n8n()
    findings += _deploy.check_key()
    findings += _deploy.check_backup_battery()
    if api_url:
        findings += _deploy.check_api_auth(api_url)
    worst = "PASS"
    for f in findings:
        mark = {"PASS": "ok ", "OK": "ok ", "WARN": "WARN", "FAIL": "FAIL"}[f.level]
        typer.echo(f"[{mark}] {f.name} {f.detail} {('-> ' + f.fix) if f.fix else ''}".rstrip())
        if f.level == "FAIL":
            worst = "FAIL"
        elif f.level == "WARN" and worst == "PASS":
            worst = "WARN"
    typer.echo(f"deploy-check: {worst}")
    if worst == "FAIL":
        raise typer.Exit(code=1)


def _parse_json(text) -> dict:
    import json as _json2

    try:
        out = _json2.loads(text or "{}")
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}


@app.command("audit-pack")
def audit_pack(
    episode: int | None = typer.Option(None, "--episode", help="DB episode id"),
    day: str | None = typer.Option(None, "--day", help="gunun tum bolumleri (YYYY-MM-DD)"),
) -> None:
    """Elle denetim paketi: out/<gun>/<slug>/audit.md (senaryo, kurallar, metadata)."""
    from pathlib import Path as _P

    import devbrief.db as db

    if (episode is None) == (day is None):
        typer.echo("--episode veya --day'den biri gerekli", err=True)
        raise typer.Exit(code=2)
    conn = db.connect()
    db.migrate(conn)
    if day is not None:
        rows = conn.execute("SELECT * FROM episodes WHERE day = ? ORDER BY id", (day,)).fetchall()
        conn.close()
        if not rows:
            typer.echo(f"{day}: bolum yok", err=True)
            raise typer.Exit(code=1)
        for r in rows:
            slug, lines = _build_audit(dict(r))
            target = _P("out") / (r["day"] or "undated") / slug / "audit.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            typer.echo(f"audit: {target}")
        return
    assert episode is not None
    row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode,)).fetchone()
    conn.close()
    if row is None:
        typer.echo(f"episode {episode} yok", err=True)
        raise typer.Exit(code=1)
    slug, lines = _build_audit(dict(row))
    target = _P("out") / (row["day"] or "undated") / slug / "audit.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    typer.echo(f"audit: {target}")


def _build_audit(row: dict) -> tuple[str, list[str]]:
    """Tek bolum denetim satirlari. Saf: DB/file okur, yazmaz (hedef haric)."""
    import json as _json
    from pathlib import Path as _P

    from devbrief.script import metadata as _meta
    from devbrief.script import verifier as _verifier

    episode = row.get("id", "?")
    day, slug = row.get("day") or "undated", row.get("slug") or f"ep-{episode}"
    script = _parse_json(row.get("script_json"))
    verify = _parse_json(row.get("verify_json"))
    meta = _parse_json(row.get("metadata_json"))
    try:
        meta_warns = _json.loads(row.get("metadata_warnings") or "[]")
    except ValueError:
        meta_warns = []
    source_text = ""
    fetch_file = _P("out") / day / slug / "fetch.json"
    if fetch_file.exists():
        try:
            source_text = _json.loads(fetch_file.read_text(encoding="utf-8")).get("text", "")
        except ValueError:
            pass
    lines = [f"# Denetim: {slug} (episode {episode})", "",
             f"- durum: {row.get('status')}", f"- low_source: {row.get('low_source') or 0}",
             f"- injection_suspected: {row.get('injection_suspected') or 0}",
             f"- is_demo: {row.get('is_demo') or 0}",
             f"- red_nedeni: {row.get('reject_reason') or '-'}", ""]
    if script:
        from devbrief.script.schemas import Script as _S

        try:
            model = _S.model_validate(script)
            lines += ["## Senaryo", f"- kelime: {model.voice_word_count()}",
                      f"- sahne: {len(model.scenes)}",
                      f"- uyarilar: {model.content_warnings() or '-'}",
                      f"- yorumlar: {[c.text[:80] for c in model.inferences()] or '-'}", ""]
        except Exception as e:  # noqa: BLE001
            lines += ["## Senaryo", f"- SEMA HATASI: {e}"[:300], ""]
        if source_text:
            lines.append("## Kural denetimi")
            for i in _verifier.check_grounding(script, source_text) or ["(temiz)"]:
                lines.append(f"- {i}" if isinstance(i, str) else
                             f"- [{i.kind}] {i.field}: {i.value!r}")
            lines.append("")
        else:
            lines += ["## Kural denetimi", "- kaynak metin yok (fetch.json), atlandi", ""]
    else:
        lines += ["## Senaryo", "- yok", ""]
    lines.append("## Dogrulayici karari")
    for v in (verify.get("verdicts") or ["(yok)"]):
        lines.append(f"- {v}")
    lines += [f"- overall: {verify.get('overall', '-')}", ""]
    lines.append("## Metadata")
    if meta:
        try:
            from devbrief.script.metadata import Metadata as _Meta

            checked = _Meta.model_validate(meta)
        except Exception:  # noqa: BLE001 - bozuk kayit ham gosterilir
            checked = None
        lines += [f"- baslik: {meta.get('title', '')}",
                  f"- x: {meta.get('x_post', '')}",
                  f"- linkedin: {str(meta.get('linkedin_post', ''))[:200]}",
                  f"- hashtag: {meta.get('hashtags', [])}",
                  f"- uyarilar: {meta_warns or '-'}"]
        budget = meta.get("budget") or {}
        plats = budget.get("platforms") or {} if isinstance(budget, dict) else {}
        if plats:
            lines.append("## Metadata butcesi")
            if budget.get("source_url"):
                lines.append(f"- kaynak: {budget.get('source_url')}"
                             f" (x_url_len={budget.get('x_url_len')})")
            for field in ("title", "description", "x_post", "linkedin_post"):
                b = plats.get(field) or {}
                if not isinstance(b, dict):
                    continue
                extra = (f", link sayimi {b['link_counted']}"
                         if "link_counted" in b else "")
                lines.append(f"- {field}: govde<={b.get('body_budget')} "
                             f"(limit {b.get('limit')}, link payi "
                             f"{b.get('link_share')}{extra}, beyan payi "
                             f"{b.get('note_share')})")
        elif checked is None:
            lines.append("- butce: (yok; kayit bozuk olabilir)")
        if source_text:
            lines.append("## Metadata kural denetimi")
            if checked is None:
                lines.append("- SEMA HATASI: metadata_json dogrulanamadi")
            else:
                for e in _meta.check_metadata(checked, script, source_text) or ["(temiz)"]:
                    lines.append(f"- {e}")
                for w in _meta.check_hype(checked, script, source_text):
                    lines.append(f"- uyari: {w}")
        sup = meta.get("sentence_support") or [] if isinstance(meta, dict) else []
        if not sup and checked is not None and source_text:
            sup = _meta.sentence_support_map(checked, script, source_text)
        if sup:
            lines.append("## Metadata cumle dayanaklari")
            for s in sup:
                if not isinstance(s, dict):
                    continue
                lines.append(f"- [{s.get('field', '?')}]"
                             f" {str(s.get('sentence', ''))[:120]}"
                             f" -> {s.get('basis', '')}")
    else:
        lines.append("- uretilmemis")
    return slug, lines


@app.command("audit-log")
def audit_log() -> None:
    """Denetim ilerlemesi: out/audit/summary.md (durum sayilari + kritik hatalar)."""
    from pathlib import Path as _P

    import devbrief.db as db

    conn = db.connect()
    db.migrate(conn)
    rows = conn.execute(
        "SELECT id, day, slug, status, reject_reason FROM episodes ORDER BY day, id").fetchall()
    conn.close()
    by_status: dict[str, int] = {}
    failed: list[str] = []
    with_audit = 0
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        slug = r["slug"] or f"ep-{r['id']}"
        if (_P("out") / (r["day"] or "undated") / slug / "audit.md").exists():
            with_audit += 1
        if r["status"] in ("failed", "metadata_failed"):
            why = (r["reject_reason"] or "")[:120]
            failed.append(f"- #{r['id']} {slug} ({r['status']}): {why}")
    lines = ["# Denetim ilerlemesi", "",
             f"- toplam bolum: {len(rows)}", f"- audit.md olan: {with_audit}"]
    for st in ("draft", "verified", "rendered", "approved", "published",
               "rejected", "failed", "metadata_failed", "low_source"):
        if by_status.get(st):
            label = "failed (metadata)" if st == "metadata_failed" else st
            lines.append(f"- {label}: {by_status[st]}")
    lines += ["", "## Kritik hatalar (failed)"]
    lines += failed or ["- yok"]
    target = _P("out") / "audit" / "summary.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    typer.echo(f"audit-log: {target} ({len(rows)} bolum, {len(failed)} kritik)")


@app.command()
def report(
    llm: bool = typer.Option(False, "--llm", help="bugunku LLM kullanim ozeti"),
) -> None:
    """Gunluk rapor: model+saat bazinda istek, durum dagilimi, kalan butce."""
    import devbrief.db as db
    from devbrief.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    conn = db.connect()
    db.migrate(conn)
    if not llm:
        typer.echo("--llm disinda rapor henuz yok")
        conn.close()
        return
    rows = db.llm_stats_today(conn)
    spent = db.count_llm_calls_today(conn)
    typer.echo(f"butce: {spent}/{s.llm_daily_budget} (kalan {s.llm_daily_budget - spent})")
    if s.openrouter_api_key:
        _echo_key_status(s.openrouter_api_key)
    if not rows:
        typer.echo("bugun kayit yok")
    for r in rows:
        paid = " (ucretli)" if r["paid"] else ""
        fmt = r["fmt"] or "?"
        rtok = f" akil={r['rtok'] or 0}" if r["rtok"] else ""
        typer.echo(f"[{r['hour']}:00] {r['model']}{paid}: n={r['n']} ok={r['ok_n'] or 0} "
                   f"durum={r['status'] or '?'}{rtok} bicim={fmt}")
    typer.echo("--- maliyet (model bazinda) ---")
    try:
        costs = db.llm_cost_by_model_today(conn)
    except Exception:  # noqa: BLE001 - eski sema
        costs = []
    if not costs or all((c["cost_total"] is None) for c in costs):
        typer.echo("maliyet: bilinmiyor (usage.cost yok)")
    else:
        for c in costs:
            if c["cost_total"] is None or not c["cost_n"]:
                typer.echo(f"{c['model']}: maliyet bilinmiyor (n={c['n']})")
            else:
                avg = float(c["cost_total"]) / int(c["cost_n"] or 1)
                typer.echo(f"{c['model']}: toplam {float(c['cost_total']):.5f} kredi, "
                           f"istek basina ort {avg:.5f} (n={c['n']})")
    typer.echo("--- son 7 gun ---")
    week = db.llm_stats_7d(conn)
    if not week:
        typer.echo("kayit yok")
    for r in week:
        typer.echo(f"{r['day']} {r['model']}: n={r['n']} ok={r['ok_n'] or 0} "
                   f"ucretli={r['paid_n'] or 0}")
    conn.close()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="dinlenecek adres"),
    port: int = typer.Option(8000, "--port", help="dinlenecek port"),
) -> None:
    """FastAPI servisi (n8n orkestrasyonu icin)."""
    import uvicorn

    uvicorn.run("devbrief.api.routes:app", host=host, port=port)


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())
