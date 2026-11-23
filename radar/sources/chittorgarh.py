"""Chittorgarh 'Upcoming IPO' news list, used only as an early signal.

We read each headline to get the company and the kind of event, then build our own alert text.
Nothing is copied from their articles, and the alert links back to them."""
from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..classify import classify_event, classify_segment, extract_company
from ..models import Record, SourceResult

NAME = "chittorgarh"
URL = "https://www.chittorgarh.com/news/category/ipo-news/upcoming-ipo/"
KEEP = {"drhp_filed", "drhp_refiled", "approval"}


def parse(html: str, base: str = URL) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        if "/news-detail/" not in a["href"]:
            continue
        # Each card's link carries the plain headline in its title attribute; the visible text is
        # a blob of number + category + timestamp + headline + teaser.
        title = (a.get("title") or "").strip() or a.get_text(" ", strip=True)
        event = classify_event(title)
        company = extract_company(title)
        if event not in KEEP or not company:
            continue
        url = urljoin(base, a["href"])
        if url in seen:
            continue
        seen.add(url)
        segment, exchange = classify_segment(title)
        out.append(
            Record(
                source=NAME,
                company=company,
                event=event,
                segment=segment,
                exchange=exchange,
                url=url,
                title=title,
            )
        )
    return out


def fetch(f) -> SourceResult:
    r = f.get(NAME, URL)
    if not r.ok:
        return SourceResult(NAME, False, [], r.error or "no response")
    records = parse(r.text)
    return SourceResult(NAME, True, records, f"{len(records)} headlines matched")
