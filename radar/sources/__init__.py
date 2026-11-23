"""Each source is a (name, callable) pair. The callable takes a Fetcher and returns a SourceResult."""
from __future__ import annotations

import os

from . import bse_sme, chittorgarh, nse_emerge, rss, sebi

DEFAULT_FEEDS = {"rss_ipocentral": "https://ipocentral.in/category/ipo-news/feed/"}


def feeds_from_env() -> dict:
    raw = os.getenv("RADAR_FEEDS", "").strip()
    if not raw:
        return dict(DEFAULT_FEEDS)
    feeds = {}
    for part in raw.split(","):
        if "=" in part:
            name, url = part.split("=", 1)
            feeds[name.strip()] = url.strip()
    return feeds


def all_sources() -> list:
    sources = [
        (bse_sme.NAME, bse_sme.fetch),
        (sebi.NAME, sebi.fetch),
        (chittorgarh.NAME, chittorgarh.fetch),
    ]
    if os.getenv("RADAR_ENABLE_NSE") == "1":
        # Experimental: the NSE page is script-driven and blocks most cloud servers, so it is off by default.
        sources.append((nse_emerge.NAME, nse_emerge.fetch))
    for name, url in feeds_from_env().items():
        sources.append((name, rss.make_fetch(name, url)))
    return sources
