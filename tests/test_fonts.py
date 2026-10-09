from devbrief.render.fonts import FONT_DIR, bundled_font_path, load_font


def test_font_dir_points_into_repo():
    assert FONT_DIR.name == "fonts"
    assert "assets" in FONT_DIR.parts


def test_bundled_fonts_exist():
    from devbrief.render.fonts import list_bundled_fonts

    status = list_bundled_fonts()
    assert status, "font listesi bos"
    missing = [k for k, v in status.items() if not v]
    assert not missing, f"eksik font dosyasi: {missing}"


def test_bold_differs_from_regular():
    text = "Agile Release 123"
    regular = load_font("inter", 48)
    bold = load_font("inter-bold", 48)
    assert regular is not None and bold is not None
    try:
        w_reg = regular.getlength(text)
        w_bold = bold.getlength(text)
    except AttributeError:
        import pytest

        pytest.skip("Pillow surumu getlength desteklemiyor")
    assert w_bold != w_reg, f"bold ({w_bold}) regular ({w_reg}) ile ayni"


def test_load_font_never_raises():
    # Font dosyalari henuz inmemiş olabilir; varsayilana dusmeli, patlamamali.
    f = load_font("inter", 32)
    assert f is not None
    assert bundled_font_path("nope").name == "Inter-Regular.ttf"
