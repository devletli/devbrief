"""eval-models yardimcilari: gercek ag yok, post_fn mock'lanir."""

from devbrief.eval import evaluate_model, load_paragraphs, render_table, turkish_ratio


def test_load_paragraphs(tmp_path):
    f = tmp_path / "p.txt"
    f.write_text("a\n---\n\n---\nb", encoding="utf-8")
    assert load_paragraphs(f) == ["a", "b"]


def test_turkish_ratio():
    assert turkish_ratio("abc") == 0.0
    assert turkish_ratio("çğıöşü") > 0.9
    assert turkish_ratio("") == 0.0


def test_evaluate_model_counts_valid_json():
    paras = ["p1", "p2"]

    def fake_post(model, prompt):
        assert "p1" in prompt or "p2" in prompt
        if "p1" in prompt:
            return ('{"tr": "Türkçe özet çalışması"}', 100.0)
        return ("bozuk", 200.0)

    ev = evaluate_model("m", paras, fake_post, prompt_template="{{paragraph}}")
    assert ev.valid_json_rate == 0.5
    assert ev.avg_latency_ms == 150.0
    assert ev.avg_turkish_ratio > 0
    assert "model" in render_table([ev])
