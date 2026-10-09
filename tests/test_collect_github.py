"""GitHub releases toplayici: fixture + respx mock."""

from pathlib import Path

import httpx
import respx

from devbrief.collect import github_releases
from devbrief.collect.base import FetchError

FIXTURE = Path(__file__).parent / "fixtures" / "github_atom_sample.xml"


async def test_collect_parses_atom_and_filters_old():
    async with httpx.AsyncClient() as client:
        with respx.mock(using="httpx") as mock:
            route = mock.get("https://github.com/vitejs/vite/releases.atom").mock(
                return_value=httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))
            )
            got = await github_releases.collect({"repo": "vitejs/vite"}, client=client)
            assert len(got) == 1
            assert got[0].source == "github:vitejs/vite"
            assert got[0].url == "https://github.com/vitejs/vite/releases/tag/v7.0.0"
            # Token varsa Authorization gonderilir.
            got2 = await github_releases.collect(
                {"repo": "vitejs/vite"}, client=client, token="secret"
            )
            assert len(got2) == 1
            assert route.calls[-1].request.headers["Authorization"] == "Bearer secret"


async def test_collect_404_wraps():
    async with httpx.AsyncClient() as client:
        with respx.mock(using="httpx") as mock:
            mock.get("https://github.com/nope/nope/releases.atom").mock(
                return_value=httpx.Response(404)
            )
            try:
                await github_releases.collect({"repo": "nope/nope"}, client=client)
            except FetchError:
                return
            raise AssertionError("FetchError bekleniyordu")
