"""Kompozisyon: ffmpeg alt surecleri ile birlestirme (Windows uyumlu, shell yok).

Hatti: slayt PNG + sahne sesi -> zoompan'li segmentler -> concat ->
altyazi yakma + (opsiyonel) -22dB muzik -> H.264/AAC mp4.
Cikti: out/<tarih>/<slug>/video.mp4, thumb.png, meta.json
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

FPS = 30
#: TTS sonrasi gercek sure sigortasi: ustundeyse senaryo kisaltma turuna gider.
DURATION_LIMIT_S = 55.0


def over_limit(total_s: float, limit: float = DURATION_LIMIT_S) -> bool:
    return total_s > limit


class FfmpegMissing(RuntimeError):
    pass


def require_tools() -> tuple[str, str]:
    from devbrief.config import get_settings

    s = get_settings()
    ff = s.ffmpeg_path or shutil.which("ffmpeg") or "bin/ffmpeg.exe"
    fp = s.ffprobe_path or shutil.which("ffprobe") or "bin/ffprobe.exe"
    if not _exists(ff) or not _exists(fp):
        raise FfmpegMissing(
            "ffmpeg/ffprobe bulunamadi (FFMPEG_PATH/FFPROBE_PATH, PATH veya repo bin/)"
        )
    return ff, fp


def _exists(tool: str) -> bool:
    p = Path(tool)
    if p.is_file():
        return True
    return shutil.which(tool) is not None


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg hata ({p.returncode}): {(p.stderr or '')[-2000:]}")


def probe_duration(path: str | Path) -> float:
    _, fp = require_tools()
    p = subprocess.run(
        [fp, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe sure okuyamadi: {path}")
    return float(p.stdout.strip())


def probe_video(path: str | Path) -> dict:
    _, fp = require_tools()
    p = subprocess.run(
        [
            fp, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,codec_name",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe okuyamadi: {path}")
    data = json.loads(p.stdout)
    streams = data.get("streams", [{}])[0]
    return {
        "width": int(streams.get("width", 0)),
        "height": int(streams.get("height", 0)),
        "vcodec": streams.get("codec_name", ""),
        "duration": float(data.get("format", {}).get("duration", 0) or 0),
    }


def probe_streams(path: str | Path) -> list[dict]:
    _, fp = require_tools()
    p = subprocess.run(
        [fp, "-v", "error", "-show_entries", "stream=codec_type,codec_name",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe okuyamadi: {path}")
    return json.loads(p.stdout).get("streams", [])


def _ass_filter(path: Path) -> str:
    # ffmpeg filter icin Windows yolu kacar: \ -> /, : -> \:, ' -> '\''
    s = path.as_posix().replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
    return f"subtitles='{s}'"


def build(
    scenes: list[dict],
    audio: list[dict],
    slides: list[Path | str],
    *,
    slug: str,
    day: str,
    ass_path: Path | str,
    out_root: str | Path = "out",
    music: str | Path | None = None,
    source_url: str = "",
) -> dict:
    """Segmentleri birlestirir, meta.json yazar. Donus: meta dict."""
    ff, _ = require_tools()
    out_dir = Path(out_root) / day / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"
    work.mkdir(exist_ok=True)

    durations = [probe_duration(a["mp3"]) for a in audio]
    seg_files = []
    for i, (png, dur) in enumerate(zip(slides, durations, strict=True)):
        seg = work / f"seg_{i + 1}.mp4"
        frames = max(1, int(round(dur * FPS)))
        vf = (
            "scale=1080:1920,"
            f"zoompan=z='1+0.03*on/{FPS}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={FPS}"
        )
        _run([ff, "-y", "-v", "error", "-loop", "1", "-i", str(png),
              "-vf", vf, "-frames:v", str(frames), "-r", str(FPS),
              "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg)])
        seg_files.append(seg)

    concat_list = work / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{s.resolve().as_posix()}'\n" for s in seg_files), encoding="utf-8"
    )
    v_silent = work / "v.mp4"
    _run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list),
          "-c", "copy", str(v_silent)])

    audio_list = work / "audio.txt"
    audio_list.write_text(
        "".join(f"file '{Path(a['mp3']).resolve().as_posix()}'\n" for a in audio),
        encoding="utf-8",
    )
    a_joined = work / "a.mp3"
    _run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(audio_list),
          "-c", "copy", str(a_joined)])

    # Ses: muzik once karisir (0.08 ~= -22 dB), sonra tek adimda loudnorm.
    # Tum gunluk videolar ayni hedefte (I=-14) cikar, seviye farki kalmaz.
    loud = "loudnorm=I=-14:TP=-1.5:LRA=11"
    video = out_dir / "video.mp4"
    if music:
        cmd = [ff, "-y", "-v", "error", "-i", str(v_silent), "-i", str(a_joined),
               "-stream_loop", "-1", "-i", str(music),
               "-filter_complex",
               "[1:a]volume=1[a0];[2:a]volume=0.08[a1];"
               f"[a0][a1]amix=inputs=2:duration=shortest,{loud}[a];"
               f"[0:v]{_ass_filter(Path(ass_path))}[v]",
               "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-r", str(FPS), "-shortest", str(video)]
    else:
        cmd = [ff, "-y", "-v", "error", "-i", str(v_silent), "-i", str(a_joined),
               "-vf", _ass_filter(Path(ass_path)), "-af", loud,
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-r", str(FPS), "-shortest", str(video)]
    _run(cmd)

    total = probe_duration(video)
    meta = {
        "slug": slug, "day": day, "source_url": source_url,
        "scenes": [
            {"id": sc.get("id", i + 1), "duration_s": round(d, 2)}
            for i, (sc, d) in enumerate(zip(scenes, durations, strict=True))
        ],
        "total_s": round(total, 2), "resolution": "1080x1920", "fps": FPS,
        "duration_limit_s": DURATION_LIMIT_S, "over_limit": over_limit(total),
        "synthetic_voice": True,
        "created_at": datetime.now(UTC).isoformat(),
    }
    meta_path = out_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
