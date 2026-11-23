"""BSE SME public-issues page that hosts SME draft offer documents.

The live page is a server-rendered table (id ContentPlaceHolder1_gvData), newest first, back to 2018.
Each row: company name in <a class="tablebluelink"> (no href), then the DRHP date as a link to a .zip
file. We read only the newest MAX_ROWS rows. A generic row parser is kept as a fallback in case BSE
changes the markup."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..classify import parse_date
from ..models import Record, SourceResult

NAME = "bse_sme"
MAX_ROWS = 200
URL = "https://www.bsesme.com/PublicIssues/SMEIPODRHP.aspx"

_NOISE = re.compile(r"^(download|view|pdf|drhp|draft|click|link|open|sr\.?\s*no)\b", re.I)


def _looks_like_company(text: str) -> bool:
    t = text.strip()
    if len(t) < 4 or t.isdigit() or _NOISE.match(t):
        return False
    if parse_date(t) and len(t) <= 14:
        return False
    return bool(re.search(r"[A-Za-z]{3}", t))


def _parse_grid(soup, base: str) -> list:
    table = soup.find(id="ContentPlaceHolder1_gvData")
    if table is None:
        return []
    out, seen = [], set()
    for tr in table.select("tr.TTRow"):
        name = tr.select_one("a.tablebluelink")
        company = name.get_text(" ", strip=True) if name else ""
        if not company or company.lower() in seen:
            continue
        link = tr.find("a", href=True)
        seen.add(company.lower())
        out.append(
            Record(
                source=NAME,
                company=company,
                event="drhp_filed",
                segment="sme",
                exchange="BSE SME",
                url=urljoin(base, link["href"]) if link else base,
                title=company,
                filed_on=parse_date(link.get_text(" ", strip=True)) if link else "",
            )
        )
        if len(out) >= MAX_ROWS:
            break
    return out


def parse(html: str, base: str = URL) -> list:
    soup = BeautifulSoup(html, "html.parser")
    grid = _parse_grid(soup, base)
    if grid:
        return grid
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
