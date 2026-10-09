---
description: Faz 0 - proje iskeleti ve doctor komutu
---
AGENTS.md ve docs/02-ARCHITECTURE.md'ye uygun şekilde:
1. `uv init` ile proje oluştur (paket adı `devbrief`, src layout), Python 3.12.
2. Bağımlılıklar: httpx, feedparser, trafilatura, pydantic, pydantic-settings, openai, typer, pyyaml, edge-tts, pillow, pygments, structlog; dev: pytest, pytest-asyncio, respx, ruff, mypy.
3. AGENTS.md'deki dizin yapısını boş modüllerle kur; `config.py` (.env + sources.yaml okur).
4. `cli.py` içinde typer uygulaması ve `doctor` komutu: OPENROUTER_API_KEY var mı, ffmpeg/ffprobe PATH'te mi, yapılandırılmış modellerden birine 1 küçük istek (isteğe bağlı `--ping-llm` bayrağıyla), data/ ve out/ yazılabilir mi.
5. `.gitignore` (.env, secrets/, data/, out/, .venv), `ruff` ve `pytest` yapılandırması.
6. `uv run devbrief doctor` ve `uv run pytest -q` çalıştır, sonucu göster.
$ARGUMENTS
