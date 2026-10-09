"""RSS toplayici: fixture + respx mock, hata izolasyonu."""

from pathlib import Path

import httpx
import respx

from devbrief.collect import rss
from devbrief.collect.base import FetchError

FIXTURE = Path(__file__).parent / "fixtures" / "rss_sample.xml"


def test_parse_feed_filters_old():
    xml = FIXTURE.read_text(encoding="utf-8")
    all_items = rss.parse_feed(xml, source="Sample", max_age_hours=24 * 365 * 10)
    assert len(all_items) == 2
    assert all_items[0].title == "Python 3.14 released"
    recent = rss.parse_feed(xml, source="Sample", max_age_hours=48)
    assert len(recent) == 1
    assert recent[0].url.startswith("https://example.com/python-314")


def test_parse_feed_skips_entry_without_link():
    out = rss.parse_feed("<rss version='2.0'><channel></channel></rss>", source="s")
    assert out == []


async def test_collect_uses_etag_304():
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="https://example.com", using="httpx") as mock:
            mock.get("/feed").mock(side_effect=[
                httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8")),
                httpx.Response(304),
            ])
            cfg = {
                "name": "Sample",
                "url": "https://example.com/feed",
                "max_age_hours": 24 * 365 * 10,
            }
            first = await rss.collect(cfg, client=client)
            assert len(first) == 2
            second = await rss.collect(cfg, client=client, etag='"abc"')
            assert second == []


async def test_collect_http_error_wraps():
    async with httpx.AsyncClient() as client:
        with respx.mock(using="httpx") as mock:
            mock.get("https://example.com/bad").mock(return_value=httpx.Response(500))
            try:
                await rss.collect({"name": "B", "url": "https://example.com/bad"}, client=client)
            except FetchError:
                return
            raise AssertionError("FetchError bekleniyordu")


def _cloudflare_bytes() -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version='2.0'><channel>"
        "<item><title>Bridging technical depth and usability: Radar\u2019s redesign</title>"
        "<link>https://example.com/radar</link>"
        "<pubDate>Wed, 08 Oct 2026 07:00:00 GMT</pubDate>"
        "<description>x</description></item>"
        "</channel></rss>"
    )
    return xml.encode("utf-8")


async def test_collect_bytes_no_mojibake():
    """Sunucu charset'i yanlis bildirirse response.text bozar; content bozmaz."""
    body = _cloudflare_bytes()
    lying = httpx.Response(
        200, content=body, headers={"content-type": "text/xml; charset=windows-1252"}
    )
    assert "Radar\u2019s" not in lying.text  # fixture gercekten bug'u uretiyor
    async with httpx.AsyncClient() as client:
        with respx.mock(using="httpx") as mock:
            mock.get("https://example.com/cf").mock(return_value=lying)
            got = await rss.collect(
                {
                    "name": "CF",
                    "url": "https://example.com/cf",
                    "max_age_hours": 24 * 365 * 10,
                },
                client=client,
            )
            assert len(got) == 1
            assert got[0].title == "Bridging technical depth and usability: Radar\u2019s redesign"
            assert "\ufffd" not in got[0].title
