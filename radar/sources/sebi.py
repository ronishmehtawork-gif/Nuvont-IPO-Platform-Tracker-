"""SEBI 'Draft Offer Documents filed with SEBI' listing. It carries mainboard issues and some SME IPOs.

Two steps: open the Public Issues index, follow the link named 'Draft Offer Documents filed with
SEBI', then read the entries (each links to /filings/public-issues/<month-year>/<slug>_<id>.html)."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..classify import parse_date
from ..models import Record, SourceResult

NAME = "sebi"
INDEX_URL = "https://www.sebi.gov.in/filings/public-issues.html"
_LINK_TEXT = "draft offer documents filed with sebi"


def find_listing_url(index_html: str, base: str = INDEX_URL) -> str | None:
    soup = BeautifulSoup(index_html, "html.parser")
    for a in soup.find_all("a", href=True):
        if _LINK_TEXT in a.get_text(" ", strip=True).lower():
            return urljoin(base, a["href"])
    return None


def parse_listing(html: str, base: str = INDEX_URL) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/filings/public-issues/" not in href or href.rstrip("/").endswith("public-issues.html"):
            continue
        title = a.get_text(" ", strip=True)
        if len(title) < 4 or title.lower() in seen:
            continue
        seen.add(title.lower())
        sme = bool(re.search(r"\bsme\b", title, re.I))
        company = re.split(r"\s+[-–—]\s+SME IPO", title, flags=re.I)[0].strip()
        row = a.find_parent(["tr", "li"])
        out.append(
            Record(
                source=NAME,
                company=company,
                event="drhp_filed",
                segment="sme" if sme else "mainboard",
                exchange="SEBI",
                url=urljoin(base, href),
                title=title,
                filed_on=parse_date(row.get_text(" ", strip=True)) if row else "",
            )
        )
    return out


def fetch(f) -> SourceResult:
    idx = f.get(NAME + "_index", INDEX_URL)
    if not idx.ok:
        return SourceResult(NAME, False, [], idx.error or "no response")
    listing = find_listing_url(idx.text)
    if not listing:
        return SourceResult(NAME, False, [], "listing link not found on index page")
    r = f.get(NAME, listing)
    if not r.ok:
        return SourceResult(NAME, False, [], r.error or "no response")
    records = parse_listing(r.text, listing)
    return SourceResult(NAME, True, records, f"{len(records)} entries parsed")
