"""HN toplayici: fixture + respx mock."""

import json
from pathlib import Path

import httpx
import respx

from devbrief.collect import hn
from devbrief.collect.base import FetchError

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "hn_sample.json").read_text())


def test_parse_hits_filters_points_and_fills_url():
    out = hn.parse_hits(FIXTURE, min_points=150, cutoff=0)
    assert len(out) == 2
    assert out[0].url == "https://example.com/python-314"
    assert "420 points" in out[0].summary
    # URL'suz kayda HN tartisma linki verilir.
    assert out[1].url == "https://news.ycombinator.com/item?id=125"


def test_parse_hits_respects_cutoff():
    out = hn.parse_hits(FIXTURE, min_points=0, cutoff=1789822801)
    assert out == []


async def test_collect_ok_and_error():
    async with httpx.AsyncClient() as client:
        with respx.mock(using="httpx") as mock:
            mock.get("https://hn.algolia.com/api/v1/search").mock(
                return_value=httpx.Response(200, json=FIXTURE)
            )
            got = await hn.collect(
                {"min_points": 150, "max_age_hours": 24 * 365 * 10}, client=client
            )
            assert len(got) == 2
        with respx.mock(using="httpx") as mock:
            mock.get("https://hn.algolia.com/api/v1/search").mock(
                return_value=httpx.Response(500)
            )
            try:
                await hn.collect({}, client=client)
            except FetchError:
                return
            raise AssertionError("FetchError bekleniyordu")
