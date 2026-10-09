"""Senaryo dongusu: sahte writer/verifier ile (ag yok)."""

from devbrief.script import verifier
from devbrief.script import writer as writer_mod
from devbrief.script.schemas import IndexVerdict, VerifyOut

SRC = "MiniDB 2.1 cikti, hiz %40 artti."


class _S:
    def __init__(self, d):
        self._d = d

    def model_dump(self):
        return dict(self._d)


def _good():
    return {
        "hook": "MiniDB kisa kanca", "source_url": "https://x",
        "scenes": [{"id": 1, "voice": "MiniDB 2.1 cikti ve hizlandi.",
                    "on_screen": "Etiket", "visual": "title", "code": None,
                    "evidence": ["e"]}],
        "impact": "Etki.", "action": "Aksiyon.",
        "claims": [{"text": "2.1 yayinda.", "evidence": "e"}],
        "duration_estimate_s": 40,
    }


def _bad():
    d = _good()
    d["scenes"][0]["voice"] = "MiniDB 9.9 cikti ve hizlandi."
    d["claims"] = [{"text": "9.9 yayinda.", "evidence": "e"}]
    return d


def _pass_verdict(n_claims: int = 1):
    return VerifyOut(
        verdicts=[IndexVerdict(i=i, verdict="supported", note="ok") for i in range(n_claims)],
        overall="pass",
    )


async def test_verified_first_try(monkeypatch):
    async def fake_verify(source_text, claims, **kw):
        return _pass_verdict()

    async def no_rewrite(*a, **k):
        raise AssertionError("yeniden yazim olmamali")

    monkeypatch.setattr(verifier, "verify_llm", fake_verify)
    monkeypatch.setattr(writer_mod, "write_script", no_rewrite)
    status, final, verdict, reason = await verifier.verify_episode(_good(), SRC)
    assert status == "verified" and final is not None and reason == ""


async def test_rewrite_then_verified(monkeypatch):
    calls = []

    async def fake_verify(source_text, claims, **kw):
        return _pass_verdict()

    async def fake_write(source_text, source_url, previous_errors="", **kw):
        calls.append(previous_errors)
        return _S(_good())

    monkeypatch.setattr(verifier, "verify_llm", fake_verify)
    monkeypatch.setattr(writer_mod, "write_script", fake_write)
    status, final, _, _ = await verifier.verify_episode(_bad(), SRC)
    assert status == "verified"
    assert len(calls) == 1 and "9.9" in calls[0]


async def test_failed_after_turns(monkeypatch):
    async def fake_verify(source_text, claims, **kw):
        return _pass_verdict()

    async def always_bad(source_text, source_url, previous_errors="", **kw):
        return _S(_bad())

    monkeypatch.setattr(verifier, "verify_llm", fake_verify)
    monkeypatch.setattr(writer_mod, "write_script", always_bad)
    status, final, _, reason = await verifier.verify_episode(
        _bad(), SRC, max_turns=1)
    assert status == "failed" and final is None and "9.9" in reason


def test_normalize_missing_index_unsupported():
    v = VerifyOut(verdicts=[IndexVerdict(i=1, verdict="supported")], overall="pass")
    out = verifier.normalize_verdicts(v, [{"text": "a"}, {"text": "b"}])
    assert out.overall == "fail"
    assert out.verdicts[0].verdict == "unsupported"
    assert out.verdicts[1].verdict == "supported"


async def test_call_cap_failed(monkeypatch):
    async def fake_verify(source_text, claims, **kw):
        return _pass_verdict()

    async def charging_write(source_text, source_url, previous_errors="", cap=None, **kw):
        if cap is not None:
            cap.charge()  # gercek writer gibi kota harcar
        return _S(_bad())

    monkeypatch.setattr(verifier, "verify_llm", fake_verify)
    monkeypatch.setattr(writer_mod, "write_script", charging_write)
    status, _, _, reason = await verifier.verify_episode(_bad(), SRC, max_calls=0)
    assert status == "failed" and reason == "call_cap"
