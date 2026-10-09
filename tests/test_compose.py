"""Uctan uca render: sahte senaryo -> 5sn video, ffprobe dogrulama.

ffmpeg/ffprobe yoksa atlanir (doctor'da FAIL gorunur).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from devbrief.render import compose, slides, subtitles

NEEDS_FFMPEG = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None

FIXTURE = Path(__file__).parent / "fixtures" / "sample_script.json"


def _silent_mp3(path: Path, seconds: float) -> None:
    ff = shutil.which("ffmpeg")
    assert ff is not None
    p = subprocess.run(
        [ff, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", str(seconds), "-c:a", "libmp3lame", str(path)],
        capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stderr[-500:]


@pytest.mark.skipif(NEEDS_FFMPEG, reason="ffmpeg/ffprobe yok")
def test_sample_video_5s(tmp_path):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    scenes = data["scenes"]
    work = tmp_path / "w"
    work.mkdir()

    audio = []
    per = 5.0 / len(scenes)  # toplam ~5 sn
    durs = [round(per, 2)] * len(scenes)
    for i, dur in enumerate(durs):
        mp3 = work / f"s{i}.mp3"
        _silent_mp3(mp3, dur)
        n = max(3, int(dur / 0.3))
        words = [{"word": f"kelime{j}", "start": j * 0.3, "end": (j + 1) * 0.3} for j in range(n)]
        audio.append({"mp3": str(mp3), "words": words})

    pngs = [
        slides.render_scene(sc, hook=data["hook"], index=i, total=len(scenes),
                            out=work / f"s{i}.png")
        for i, sc in enumerate(scenes)
    ]
    groups, off = [], 0.0
    for a, dur in zip(audio, durs, strict=True):
        for g in subtitles.group_words(a["words"]):
            groups.append({"start": g["start"] + off, "end": g["end"] + off, "text": g["text"]})
        off += dur
    ass = work / "subs.ass"
    subtitles.write_ass(groups, ass)
    thumb = work / "thumb.png"
    slides.render_thumb(data["hook"], thumb)

    meta = compose.build(
        scenes, audio, pngs, slug="sample", day="2026-10-09",
        ass_path=ass, out_root=tmp_path / "out", source_url=data["source_url"],
    )
    video = tmp_path / "out" / "2026-10-09" / "sample" / "video.mp4"
    assert video.exists()
    assert (tmp_path / "out" / "2026-10-09" / "sample" / "meta.json").exists()

    info = compose.probe_video(video)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["vcodec"] == "h264"
    assert 4.5 <= info["duration"] <= 6.5
    assert 4.5 <= meta["total_s"] <= 6.5
    kinds = {s["codec_type"] for s in compose.probe_streams(video)}
    assert {"video", "audio"} <= kinds


def test_over_limit_flag():
    assert not compose.over_limit(48.7)
    assert not compose.over_limit(55.0)
    assert compose.over_limit(55.1)
