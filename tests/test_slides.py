"""Slaytlar: her gorsel turu 1080x1920 PNG uretir (ffmpeg gerektirmez)."""

from PIL import Image

from devbrief.render import slides


def _scene(visual: str) -> dict:
    return {"id": 1, "voice": "x", "on_screen": "Deneme Basligi", "visual": visual,
            "code": "npm i -D vite@latest", "evidence": []}


def test_all_visuals_render(tmp_path):
    for visual in ("title", "bullets", "code", "diff", "source", "bilinmeyen"):
        out = slides.render_scene(_scene(visual), hook="Kanca metni", index=0, total=1,
                                  out=tmp_path / f"{visual}.png")
        with Image.open(out) as im:
            assert im.size == (1080, 1920), visual


def test_thumb_renders(tmp_path):
    out = slides.render_thumb("Kanca metni buraya", tmp_path / "thumb.png")
    with Image.open(out) as im:
        assert im.size == (1080, 1920)
