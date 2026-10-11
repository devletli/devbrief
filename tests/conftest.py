import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def _clear_caches():
    """Sira bagimsizligi: cwd/terim onbellekleri her teste temiz baslar.

    `prefilter.load_terms` lru_cache'i cwd'ye bagli kirlenirdi (tmp chdir
    sonrasi 4 test kizarirdi); `get_settings` de env'ye bagli kirlenir.
    monkeypatch.chdir zaten geri alinabilir; burada ek garanti olarak
    cwd'yi de geri aliyoruz.
    """
    import os

    from devbrief.config import get_settings
    from devbrief.curate import prefilter

    prev_cwd = os.getcwd()
    prefilter.load_terms.cache_clear()
    try:
        get_settings.cache_clear()
    except AttributeError:
        pass
    try:
        from devbrief.script import metadata as _mm

        try:
            _mm.load_hashtag_vocab.cache_clear()
        except AttributeError:
            pass
    except ImportError:
        pass
    yield
    prefilter.load_terms.cache_clear()
    try:
        get_settings.cache_clear()
    except AttributeError:
        pass
    try:
        os.chdir(prev_cwd)
    except OSError:
        pass
