"""Devreye alma kontrolleri: PASS/WARN/FAIL + duzeltme onerisi.

Saf fonksiyonlar (`checks.py` mantigi burada, dosya yolu parametreli) test
edilebilir; CLI yalnizca yazdirir ve FAIL varsa exit 1 doner.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

TELEGRAM_TOKEN = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}")


@dataclass
class Finding:
    level: str  # PASS | WARN | FAIL
    name: str
    detail: str = ""
    fix: str = ""


def check_compose(path: str | Path = "docker-compose.yml") -> list[Finding]:
    import yaml

    out: list[Finding] = []
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as e:
        return [Finding("FAIL", "compose-yaml", str(e)[:200], "YAML'i duzelt")]
    services = doc.get("services") or {}
    n8n = services.get("n8n") or {}
    img = str(n8n.get("image", ""))
    if ":latest" in img or not img:
        out.append(Finding("FAIL", "n8n-imaji", img, "surumu sabitle (or. 1.123.84)"))
    else:
        out.append(Finding("PASS", "n8n-imaji", img))
    api = services.get("devbrief-api") or {}
    if api.get("ports"):
        out.append(Finding("FAIL", "api-port", str(api["ports"]), "ports'u kaldir, expose yeterli"))
    else:
        out.append(Finding("PASS", "api-port", "disari kapali"))
    n8n_ports = str(n8n.get("ports", ""))
    if "127.0.0.1" not in n8n_ports and n8n_ports:
        out.append(Finding("WARN", "n8n-port", n8n_ports[:80], "127.0.0.1'e bagla"))
    else:
        out.append(Finding("PASS", "n8n-port", n8n_ports or "yok"))
    vols = json.dumps((services.get("devbrief-api") or {}).get("volumes", []) + list(
        n8n.get("volumes", []) or []))
    for need in ("data", "out", "backup", "n8n_data"):
        if need in vols:
            out.append(Finding("PASS", f"volume-{need}"))
        else:
            out.append(Finding("FAIL", f"volume-{need}", "yok", "compose'a kalici volume ekle"))
    return out


def check_git(files: list[str] | None = None, gitignore_text: str | None = None) -> list[Finding]:
    import subprocess

    out: list[Finding] = []
    if files is None:
        try:
            p = subprocess.run(["git", "ls-files"], capture_output=True, text=True, timeout=15)
            files = p.stdout.splitlines()
        except Exception:  # noqa: BLE001
            return [Finding("WARN", "git", "okunamadi", "git init + commit yap")]
    if gitignore_text is None:
        try:
            gitignore_text = Path(".gitignore").read_text(encoding="utf-8")
        except OSError:
            gitignore_text = ""
    bad = [f for f in files if f == ".env" or f.split("/")[0] in
           ("secrets", "data", "out", "backups", "bin")]
    if bad:
        out.append(Finding("FAIL", "git-sir", ", ".join(bad[:5]), "dosyalari takipten cikar"))
    else:
        out.append(Finding("PASS", "git-sir"))
    for entry in (".env", "secrets/", "data/", "out/", "backups/", "bin/"):
        if entry in (gitignore_text or ""):
            out.append(Finding("PASS", f"ignore-{entry}"))
        else:
            out.append(Finding("FAIL", f"ignore-{entry}", "yok", ".gitignore'a ekle"))
    return out


def check_env(env: dict | None = None) -> list[Finding]:
    out: list[Finding] = []
    if env is None:
        env = {}
        try:
            for line in Path(".env").read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        except OSError:
            pass
    for key in ("DEVBRIEF_API_TOKEN", "N8N_CALLBACK_SECRET", "N8N_ENCRYPTION_KEY"):
        val = (env or {}).get(key, "")
        if len(val) >= 32:
            out.append(Finding("PASS", f"env-{key}", f"{len(val)} karakter"))
        else:
            out.append(Finding("FAIL", f"env-{key}", "bos/kisa", "`devbrief init-secrets`"))
    allow = (env or {}).get("ALLOW_PAID_MODELS", "false").lower() == "true"
    out.append(Finding("WARN" if allow else "PASS", "paid-korumasi",
                       "ACIK" if allow else "kapali",
                       "allowlist'i daralt" if allow else ""))
    return out


def check_n8n(directory: str | Path = "n8n") -> list[Finding]:
    out: list[Finding] = []
    files = sorted(Path(directory).glob("*.json")) if Path(directory).is_dir() else []
    if not files:
        return [Finding("FAIL", "n8n-dosya", "yok", "akislari ice aktar")]
    for f in files:
        try:
            flow = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            out.append(Finding("FAIL", f"n8n-json:{f.name}", str(e)[:150], "JSON'i duzelt"))
            continue
        blob = json.dumps(flow)
        if "sk-or-" in blob:
            out.append(Finding("FAIL", f"n8n-sir:{f.name}", "OpenRouter anahtari",
                               "credential'a tasi, dosyadan sil"))
        if TELEGRAM_TOKEN.search(blob):
            out.append(Finding("FAIL", f"n8n-sir:{f.name}", "Telegram token",
                               "credential'a tasi, dosyadan sil"))
        if "DEGISTIR" in blob:
            out.append(Finding("WARN", f"n8n-yertutucu:{f.name}", "DEGISTIR var",
                               "Config/credential degerlerini doldur"))
        if re.search(r"privacyStatus.{0,10}public", blob):
            out.append(Finding("FAIL", f"n8n-public:{f.name}", "sabit public",
                               "private yap, live geciste ac"))
        else:
            out.append(Finding("PASS", f"n8n-privacy:{f.name}"))
        if f.name == "02-approve-and-publish.json":
            if "httpHeaderAuth" in blob and "devbrief-run-finished" in blob:
                out.append(Finding("PASS", "n8n-webhook-auth"))
            else:
                out.append(Finding("FAIL", "n8n-webhook-auth", "yok",
                                   "Header Auth credential bagla"))
            if "approved" in blob:
                out.append(Finding("PASS", "n8n-onay-dugumu"))
            else:
                out.append(Finding("FAIL", "n8n-onay-dugumu", "yok", "onay IF ekle"))
    return out


def check_key(fetch=None) -> list[Finding]:
    from devbrief.config import get_settings
    from devbrief.llm.key import assess_key, fetch_key_status

    get_settings.cache_clear()
    api_key = get_settings().openrouter_api_key
    if not api_key:
        return [Finding("FAIL", "key-yok", "", ".env'ye OPENROUTER_API_KEY yaz")]
    data = (fetch or fetch_key_status)(api_key)
    if data is None:
        return [Finding("WARN", "key-erisim", "yok", "agi kontrol et")]
    return [Finding(level, f"key-{text[:60]}", "",
                    "" if level in ("PASS", "OK") else "kotayi/vadeyi yenile")
            for level, text in assess_key(data)]


def check_backup_battery(backup_dir: str | Path = "backups") -> list[Finding]:
    from devbrief.publish import backup as _backup

    out: list[Finding] = []
    latest = _backup.latest(backup_dir)
    if latest is None:
        return [Finding("WARN", "yedek-yok", "", "`devbrief backup` calistir")]
    import time

    age_h = (time.time() - latest.stat().st_mtime) / 3600
    if age_h > 26:
        out.append(Finding("WARN", "yedek-eski", f"{age_h:.0f} saat", "yedek al"))
    else:
        out.append(Finding("PASS", "yedek-taze", latest.name))
    if _backup.check_integrity(latest):
        out.append(Finding("PASS", "yedek-integrity"))
    else:
        out.append(Finding("FAIL", "yedek-integrity", "bozuk", "yedegi sil, yeniden al"))
    return out


def check_api_auth(base_url: str, get=None) -> list[Finding]:
    import httpx

    get = get or httpx.get
    try:
        r = get(f"{base_url.rstrip('/')}/episodes", timeout=10)
    except Exception as e:  # noqa: BLE001 - servis kapaliysa bilgi ver
        return [Finding("WARN", "api-erisim", f"{type(e).__name__}", "servisi baslat")]
    if r.status_code == 401:
        return [Finding("PASS", "api-auth")]
    return [Finding("FAIL", "api-auth", f"HTTP {r.status_code}", "Bearer korumayi ac")]
