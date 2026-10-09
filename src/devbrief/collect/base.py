"""Toplayici tabani: RawItem + User-Agent/timeout sabitleri."""

from __future__ import annotations

from dataclasses import dataclass

USER_AGENT = "DevBriefTR/0.1 (+https://github.com/devbrief)"
TIMEOUT_S = 15.0


@dataclass
class RawItem:
    source: str
    url: str
    title: str = ""
    summary: str = ""
    published_at: str | None = None
    raw_json: str = "{}"


class FetchError(RuntimeError):
    pass
