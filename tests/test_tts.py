"""TTS: sahte communicator ile ag'siz test (WordBoundary -> saniye)."""

from devbrief.render import tts


class FakeComm:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream(self):
        for c in self._chunks:
            yield c


def _chunks():
    return [
        {"type": "audio", "data": b"\xff\xfbFakeMP3"},
        {"type": "WordBoundary", "text": "Merhaba", "offset": 0, "duration": 3_000_000},
        {"type": "WordBoundary", "text": "dunya", "offset": 3_000_000, "duration": 4_000_000},
    ]


async def test_synth_scene_writes_audio_and_words(tmp_path):
    mp3 = tmp_path / "s1.mp3"
    words = await tts.synth_scene("Merhaba dunya", "tr-TR-AhmetNeural", mp3,
                                  communicator=FakeComm(_chunks()), cache_dir=tmp_path / "c")
    assert mp3.read_bytes().startswith(b"\xff\xfbFakeMP3")
    assert words == [
        {"word": "Merhaba", "start": 0.0, "end": 0.3},
        {"word": "dunya", "start": 0.3, "end": 0.7},
    ]


async def test_synth_script_keeps_order(tmp_path):
    scenes = [{"voice": "birinci"}, {"voice": "ikinci"}]
    order = []

    def factory(i, sc):
        order.append(i)
        return FakeComm(_chunks())

    res = await tts.synth_script(scenes, "v", tmp_path, communicator_factory=factory,
                                 cache_dir=tmp_path / "c")
    assert order == [0, 1]
    assert [r["mp3"] for r in res] == [str(tmp_path / "scene_1.mp3"), str(tmp_path / "scene_2.mp3")]
    assert all(len(r["words"]) == 2 for r in res)


async def test_synth_scene_cache_hit_avoids_backend(tmp_path):
    mp3 = tmp_path / "s1.mp3"
    cache = tmp_path / "c"
    await tts.synth_scene("Merhaba", "v", mp3, communicator=FakeComm(_chunks()), cache_dir=cache)

    class Boom:
        def make(self, text, voice):
            raise AssertionError("cache varken backend cagrilmamali")

    mp3.unlink()
    words = await tts.synth_scene("Merhaba", "v", mp3, backend=Boom(), cache_dir=cache)
    assert mp3.exists()
    assert [w["word"] for w in words] == ["Merhaba", "dunya"]
