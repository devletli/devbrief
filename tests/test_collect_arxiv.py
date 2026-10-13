"""arXiv toplayici: inline Atom fixture + respx mock, hata izolasyonu."""

from datetime import UTC, datetime, timedelta

import httpx
import respx

from devbrief.collect import arxiv
from devbrief.collect.base import FetchError


def _atom(fresh_dt: datetime, old_dt: datetime) -> str:
    def entry(i: int, dt: datetime, title: str) -> str:
        ts = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            "<entry>"
            f"<id>http://arxiv.org/abs/2601.0000{i}v1</id>"
            f"<updated>{ts}</updated><published>{ts}</published>"
            f"<title>  {title}\n  devam</title>"
            f"<summary>Ozet {i}.</summary>"
            "<author><name>Jane Doe</name></author>"
            f"<link href=\"http://arxiv.org/abs/2601.0000{i}v1\" rel=\"alternate\"/>"
            f"<link href=\"http://arxiv.org/pdf/2601.0000{i}v1\" rel=\"related\"/>"
            "<arxiv:comment>5 pages</arxiv:comment>"
            "<category term=\"cs.SE\" scheme=\"http://arxiv.org/schemas/atom\"/>"
            "</entry>"
        )

    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:arxiv="http://arxiv.org/schemas/atom">'
        f"{entry(1, fresh_dt, 'Taze Calisma')}{entry(2, old_dt, 'Eski Calisma')}"
        "</feed>"
    )


def _times():
    fresh = datetime.now(UTC) - timedelta(hours=5)
    old = datetime.now(UTC) - timedelta(days=400)
    return fresh, old


def test_parse_arxiv_basic_and_age_filter():
    fresh, old = _times()
    xml = _atom(fresh, old)
    all_items = arxiv.parse_arxiv(xml, category="cs.SE", max_age_hours=24 * 365 * 10)
    assert len(all_items) == 2
    recent = arxiv.parse_arxiv(xml, category="cs.SE", max_age_hours=48)
    assert len(recent) == 1
    it = recent[0]
    assert it.source == "arxiv:cs.SE"
    assert it.url == "https://arxiv.org/abs/2601.00001v1"
    assert it.title == "Taze Calisma devam"
    assert it.published_at is not None and it.published_at.startswith("202")
    import json as _json

    raw = _json.loads(it.raw_json)
    assert raw["arxiv_id"] == "2601.00001v1"
    assert raw["categories"] == ["cs.SE"]
    assert raw["authors"] == ["Jane Doe"]
    assert raw["comment"] == "5 pages"


def test_parse_arxiv_skips_entry_without_link():
    xml = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><title>Baglantisiz</title><summary>x</summary></entry>"
        "</feed>"
    )
    assert arxiv.parse_arxiv(xml, category="cs.LG") == []


async def test_collect_queries_each_category():
    fresh, old = _times()
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="https://export.arxiv.org", using="httpx") as mock:
            route = mock.get("/api/query").mock(
                return_value=httpx.Response(200, text=_atom(fresh, old))
            )
            items = await arxiv.collect(
                {"categories": ["cs.SE", "cs.CR"], "max_age_hours": 48, "delay_s": 0},
                client=client,
            )
            assert route.call_count == 2
            queries = [str(c.request.url) for c in route.calls]
            assert any("cat%3Acs.SE" in q or "cat:cs.SE" in q for q in queries)
            assert any("cat%3Acs.CR" in q or "cat:cs.CR" in q for q in queries)
            assert any("max_results" in q for q in queries)
    assert len(items) == 2
    assert {it.source for it in items} == {"arxiv:cs.SE", "arxiv:cs.CR"}
    assert all(it.url.startswith("https://arxiv.org/abs/") for it in items)


async def test_collect_disabled_makes_no_request():
    async with httpx.AsyncClient() as client:
        with respx.mock(using="httpx", assert_all_called=False) as mock:
            route = mock.get("https://export.arxiv.org/api/query").mock(
                return_value=httpx.Response(200, text="<feed/>")
            )
            got = await arxiv.collect(
                {"categories": ["cs.SE"], "enabled": False}, client=client
            )
            assert got == []
            assert route.call_count == 0


async def test_collect_http_error_wraps():
    async with httpx.AsyncClient() as client:
        with respx.mock(using="httpx") as mock:
            mock.get("https://export.arxiv.org/api/query").mock(
                return_value=httpx.Response(500)
            )
            try:
                await arxiv.collect({"categories": ["cs.SE"]}, client=client)
            except FetchError:
                return
            raise AssertionError("FetchError bekleniyordu")


def test_source_weights_includes_arxiv(monkeypatch):
    import devbrief.pipeline as pl

    monkeypatch.setattr(
        pl,
        "load_sources",
        lambda *a, **k: {"arxiv": {"categories": ["cs.SE"], "weight": 0.5}},
    )
    assert pl.source_weights()["arxiv:cs.SE"] == 0.5
