"""NSE offer-documents page. EXPERIMENTAL.

Developers widely report that NSE refuses cloud-server IPs. This adapter makes one polite request and
reports what came back. If NSE answers, it picks out links that look like draft offer documents. If
NSE blocks us, the health report says so and NSE coverage comes from the other sources.
It does not try to get around a block."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..classify import classify_segment
from ..models import Record, SourceResult

NAME = "nse_emerge"
URL = "https://www.nseindia.com/companies-listing/corporate-filings-offer-documents"
_HINT = re.compile(r"drhp|draft|offer document|\.pdf", re.I)


def parse(html: str, base: str = URL) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if len(text) < 6 or not (_HINT.search(href) or _HINT.search(text)):
            continue
        company = re.sub(r"\b(drhp|draft (offer document|prospectus))\b", "", text, flags=re.I).strip(" -:,")
        if len(company) < 4 or company.lower() in seen:
            continue
        seen.add(company.lower())
        segment, exchange = classify_segment(text)
        out.append(
            Record(
                source=NAME,
                company=company,
                event="drhp_filed",
                segment=segment,
                exchange=exchange or "NSE",
                url=urljoin(base, href),
                title=text,
            )
        )
    return out


def fetch(f) -> SourceResult:
    r = f.get(NAME, URL)
    if not r.ok:
        return SourceResult(NAME, False, [], r.error or "no response")
    records = parse(r.text)
    return SourceResult(NAME, True, records, f"{len(records)} links matched")
