from devbrief.config import Settings


def test_model_lists_parse():
    s = Settings(LLM_MODELS_FAST="a,b ,c", LLM_MODELS_SMART="x")
    assert s.fast_models == ["a", "b", "c"]
    assert s.smart_models == ["x"]
    assert s.all_models == ["a", "b", "c", "x"]


def test_empty_models():
    s = Settings(LLM_MODELS_FAST="", LLM_MODELS_SMART="")
    assert s.fast_models == []
    assert s.all_models == []


def test_task_limits_probe_and_timeouts():
    s = Settings(LLM_MODELS_FAST="f1", LLM_MODELS_SMART="s1", LLM_MODELS_VERIFY="v1")
    assert s.max_tokens_for("write_script") == 4096
    assert s.max_tokens_for("verify_script") == 4096  # birlesik claim listesi + akil yurutme payi
    assert s.max_tokens_for("bilinmeyen") == 1200
    assert s.verify_call_cap == 6
    assert s.timeout_for("fast") == 60.0 and s.timeout_for("smart") == 180.0
    assert s.probe_models == ["f1", "s1", "v1"]
    assert Settings(LLM_MODELS_FAST="f1", LLM_MODELS_SMART="",
                      LLM_MODELS_VERIFY="").probe_models == ["f1"]
