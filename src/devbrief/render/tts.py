"""TTS: edge-tts ile sahne bazinda ses + WordBoundary kelime zamanlari.

Zaman birimi: saniye (float). KIRILGANLIK NOTU: edge-tts resmi bir servis
degil, Edge okuma ucunu kullanan bir kutuphanedir (kota/blok/ses degisimi
olabilir). Bu yuzden:
- `TTSBackend` arayuzu arkasinda durur; yedek motor (Piper, yerel) sonradan
  takilir (`EdgeTTSBackend` yerine baska sinif vermek yeterli).
- Uretilen ses `data/tts_cache/` altinda metin+ses hash'iyle saklanir;
  ayni sahne tekrar ucretsiz gelir.
- Cikti `meta.json`'a `synthetic_voice: true` yazar; yayin aciklamasina
  "yapay zekâ destekli + sentetik ses" notu konur (platform beyan alanlari
  yayin oncesi kontrol edilir).

Ses formati: mp3 (edge-tts varsayilani).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Protocol


class TTSBackend(Protocol):
    """Yedek motor sozlesmesi: metin -> ses baytlari + kelime zamanlari."""

    def make(self, text: str, voice: str):
        """`.stream()` async iterator donduren communicator verir."""
        ...


class EdgeTTSBackend:
    """Varsayilan motor (ag gerekli). Piper gibi yerel motor buraya takilir."""

    def make(self, text: str, voice: str):
        import edge_tts

        # boundary verilmezse yalnizca SentenceBoundary gelir, kelime zamani yok.
        return edge_tts.Communicate(text, voice, boundary="WordBoundary")


DEFAULT_CACHE_DIR = Path("data/tts_cache")


def cache_key(text: str, voice: str) -> str:
    return hashlib.sha256(f"{voice}\x00{text}".encode("utf-8", "replace")).hexdigest()


async def synth_scene(
    text: str,
    voice: str,
    out: str | Path,
    backend: TTSBackend | None = None,
    communicator=None,
    cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
) -> list[dict]:
    """Metni sese cevir, mp3'e yaz, kelime listesi dondur [{word,start,end}].

    `communicator` verilirse dogrudan kullanilir (testler); yoksa `backend`
    (varsayilan EdgeTTS) uzerinden uretilir. Ayni metin+ses cache'ten gelir.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    key = cache_key(text, voice)
    cached_mp3 = Path(cache_dir) / f"{key}.mp3" if cache_dir else None
    cached_words = Path(cache_dir) / f"{key}.json" if cache_dir else None
    if cached_mp3 is not None and cached_mp3.exists() and cached_words is not None \
            and cached_words.exists():
        shutil.copyfile(cached_mp3, out)
        return json.loads(cached_words.read_text(encoding="utf-8"))
    comm = communicator or (backend or EdgeTTSBackend()).make(text, voice)
    words: list[dict] = []
    with open(out, "wb") as f:
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                data = chunk.get("data")
                if data:
                    f.write(data)
            elif chunk.get("type") == "WordBoundary":
                start = float(chunk.get("offset", 0)) / 10_000_000
                dur = float(chunk.get("duration", 0)) / 10_000_000
                words.append({"word": str(chunk.get("text", "")),
                              "start": start, "end": start + dur})
    if cache_dir is not None:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        assert cached_mp3 is not None and cached_words is not None
        shutil.copyfile(out, cached_mp3)
        cached_words.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    return words


async def synth_script(
    scenes: list[dict],
    voice: str,
    out_dir: str | Path,
    backend: TTSBackend | None = None,
    communicator_factory=None,
    cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
) -> list[dict]:
    """Tum sahneler: [{"mp3": str, "words": [...]}]. Sira korunur."""
    out_dir = Path(out_dir)
    results = []
    for i, sc in enumerate(scenes):
        mp3 = out_dir / f"scene_{i + 1}.mp3"
        comm = communicator_factory(i, sc) if communicator_factory else None
        words = await synth_scene(
            str(sc.get("voice") or ""), voice, mp3,
            backend=backend, communicator=comm, cache_dir=cache_dir,
        )
        results.append({"mp3": str(mp3), "words": words})
    return results
