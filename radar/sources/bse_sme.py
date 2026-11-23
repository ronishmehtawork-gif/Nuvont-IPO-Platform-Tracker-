"""BSE SME public-issues page that hosts SME draft offer documents.

NOTE: written without sight of the live page. The first run saves a snapshot so the parser can be
tightened against the real HTML. The row-based approach below works for any table of
'company ... link' rows."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..classify import parse_date
from ..models import Record, SourceResult

NAME = "bse_sme"
URL = "https://www.bsesme.com/PublicIssues/SMEIPODRHP.aspx"

_NOISE = re.compile(r"^(download|view|pdf|drhp|draft|click|link|open|sr\.?\s*no)\b", re.I)


def _looks_like_company(text: str) -> bool:
    t = text.strip()
    if len(t) < 4 or t.isdigit() or _NOISE.match(t):
        return False
    if parse_date(t) and len(t) <= 14:
        return False
    return bool(re.search(r"[A-Za-z]{3}", t))


def parse(html: str, base: str = URL) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=True)
        if not a:
            continue
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        company = next(
            (c for c in cells + [a.get_text(" ", strip=True)] if _looks_like_company(c)), None
        )
        if not company or company.lower() in seen:
            continue
        seen.add(company.lower())
        out.append(
            Record(
                source=NAME,
                company=company,
                event="drhp_filed",
                segment="sme",
                exchange="BSE SME",
                url=urljoin(base, a["href"]),
                title=company,
                filed_on=parse_date(" ".join(cells)),
            )
        )
    return out


def fetch(f) -> SourceResult:
    r = f.get(NAME, URL)
    if not r.ok:
        return SourceResult(NAME, False, [], r.error or "no response")
    records = parse(r.text)
    return SourceResult(NAME, True, records, f"{len(records)} rows parsed")
