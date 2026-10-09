"""Altyazi: gruplama + ASS/SRT bicimi (ag/ffmpeg gerektirmez)."""

from devbrief.render import subtitles as subs


def _w(text: str, start: float, dur: float = 0.3) -> dict:
    return {"word": text, "start": start, "end": start + dur}


def test_group_words_three_to_five():
    words = [_w(f"w{i}", i * 0.3) for i in range(10)]
    groups = subs.group_words(words)
    assert all(1 <= len(g["text"].split()) <= 5 for g in groups)
    flat = "".join(g["text"] for g in groups).replace(" ", "")
    assert flat == "".join(f"w{i}" for i in range(10))
    assert groups[0]["start"] == 0.0


def test_group_words_sentence_break():
    words = [_w(w, i * 0.3) for i, w in enumerate(
        ["Bir", "iki", "uc.", "Dort", "bes", "alti", "yedi"])]
    groups = subs.group_words(words)
    assert groups[0]["text"] == "Bir iki uc."
    assert groups[1]["text"] == "Dort bes alti yedi"


def test_group_words_short_tail_merges():
    eight = subs.group_words([_w(f"w{i}", i * 0.3) for i in range(8)])
    assert [len(g["text"].split()) for g in eight] == [5, 3]
    six = subs.group_words([_w(f"w{i}", i * 0.3) for i in range(6)])
    # Kuyruk kisa kalir ama max asilmaz: [5][1].
    assert [len(g["text"].split()) for g in six] == [5, 1]
    assert all(len(g["text"].split()) <= 5 for g in six)


def test_ass_format_and_file(tmp_path):
    assert subs.fmt_ass(61.5) == "0:01:01.50"
    assert subs.fmt_srt(61.5) == "00:01:01,500"
    groups = [{"start": 0.0, "end": 1.2, "text": "Merhaba dunya testi"}]
    ass = subs.write_ass(groups, tmp_path / "s.ass")
    text = ass.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in text and "Dialogue:" in text and "Merhaba" in text
    srt = subs.write_srt(groups, tmp_path / "s.srt")
    assert "00:00:00,000 --> 00:00:01,200" in srt.read_text(encoding="utf-8")
