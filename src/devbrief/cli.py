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
    """2 saatlik canlilik yoklamasi: model basina 1 ucuz istek, llm_calls'a yaz.

    Zamanlayici (DevBriefProbe) ile kosar; 3-4 gunde yogunluk profili cikar.
    Ucretli korumasi gecerli: allowlist disi model atlanir.
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
    for m in s.all_models:
        if not s.model_allowed(m):
            typer.echo(f"{m:50s} {'-':>7s} atlandi (ucretli korumasi)")
            continue
        paid = not m.endswith(":free")
        try:
            slot = db.reserve_llm_slot(conn, "probe", m, s.llm_daily_budget)
        except db.BudgetExceeded:
            typer.echo("butce doldu, duruluyor")
            break
        t0 = _time.perf_counter()
        status, ok, hint = "other", False, ""
        try:
            r = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {s.openrouter_api_key}"},
                json={"model": m, "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 5},
                timeout=25,
            )
            ok = r.status_code == 200
            if r.status_code == 429:
                status = "http_429"
            elif r.status_code >= 500:
                status = "http_5xx"
            elif ok:
                status = "ok"
            hint = f"({r.status_code})"
        except (httpx.TimeoutException, httpx.ConnectError):
            status, hint = "timeout", "(timeout)"
        except Exception as e:  # noqa: BLE001
            hint = f"({type(e).__name__})"
        ms = int((_time.perf_counter() - t0) * 1000)
        db.finish_llm_slot(conn, slot, ok=ok, ms=ms, status=status, is_paid=paid)
        typer.echo(f"{m:50s} {ms:7d} {status} {hint}")
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
        if f.name == "summary_verifier.md":
            continue
        try:
            d = _json3.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        tag = f.stem  # P<id>_<kind>
        try:
            pid, _, kind = tag[1:].partition("_")
            case = by_case.get((int(pid), kind))
        except ValueError:
            case = None
        if case is None:
            continue
        by_model.setdefault(str(d.get("model", f.parent.name)), []).append(
            {"case": case, "ms": float(d.get("ms", 0)),
             "assessment": assess_verdict(d.get("output") or "", case)}
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
    summarize_only: bool = typer.Option(
        False, "--summarize-only", help="API'siz: kayitli vaka dosyalarindan ozet kur"
    ),
) -> None:
    """Dogrulayici kapisi: her modeli verify rolunde 40 vakayla dene.

    Gercek API'ye istek atan eval komutlarindan biri. Modeller LLM_MODELS_VERIFY
    (bos ise SMART) listesinden; writer'dan ayri secilebilir.
    """
    import asyncio
    import json as _json
    import time as _time

    import httpx

    import devbrief.db as db
    from devbrief.config import get_settings
    from devbrief.eval import (
        Bucket,
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
    planned = len(all_cases) * len(wanted)
    typer.echo(f"{len(all_cases)} vaka x {len(wanted)} model = {planned} istek")
    if not yes and not typer.confirm("Butceye islensin mi? Devam?"):
        raise typer.Exit(code=0)

    template = load_prompt("verify_script_v1")
    bucket = Bucket(s.llm_rate_per_minute)
    conn = db.connect()
    db.migrate(conn)
    reports = []

    async def _post(model: str, prompt: str) -> tuple[str, float]:
        await bucket.wait()
        slot = db.reserve_llm_slot(conn, "eval-verify", model, s.llm_daily_budget)
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
            from devbrief.eval import slot_status as _slot_status

            db.finish_llm_slot(
                conn, slot, ok=ok, ms=int((_time.perf_counter() - t0) * 1000),
                status=_slot_status(http_status, text, finish, exc))
        return text, (_time.perf_counter() - t0) * 1000

    def _claims_text(claims: list) -> str:
        return "\n".join(
            f"[{i}] {c.get('text', '')} || kanit: {c.get('evidence', '')}"
            for i, c in enumerate(claims))

    async def _run() -> None:
        for m in wanted:
            results = []
            mdir = out / m.replace("/", "_").replace(":", "_")
            mdir.mkdir(parents=True, exist_ok=True)
            for c in all_cases:
                claims = (c.get("script") or {}).get("claims") or []
                prompt = (template.replace("{{source_text}}", c.get("para", ""))
                          .replace("{{claims}}", _claims_text(claims)))
                text, ms = await _post(m, prompt)
                results.append({"case": c, "ms": ms,
                                "assessment": assess_verdict(text, c)})
                tag = f"P{c.get('para_id')}_{c.get('kind')}"
                (mdir / f"{tag}.json").write_text(
                    _json.dumps({"model": m, "case": tag, "ms": round(ms), "output": text},
                                ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                typer.echo(f"  {m} {tag}: {ms:.0f} ms")
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
    script: str | None = typer.Option(None, "--script", help="senaryo JSON dosyasi (DB'siz)"),
    out: str = typer.Option("out/_sample", "--out", help="--script ile cikti dizini"),
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
        typer.echo(f"[{r['hour']}:00] {r['model']}{paid}: n={r['n']} ok={r['ok_n'] or 0} "
                   f"durum={r['status'] or '?'}")
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
