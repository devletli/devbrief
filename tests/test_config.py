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
