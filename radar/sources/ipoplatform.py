"""IPO Platform (Chittorgarh's IPO site) 'DRHP filed' lists for SME and mainboard.

Each row names the company, the exchange (BSE SME / NSE Emerge), the filing date, the status and a
link to the draft document hosted by the exchange itself. We read only those facts and build our own
alert text. Revenue and profit figures shown on the page are not copied."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..models import Record, SourceResult

NAME = "ipoplatform"
PAGES = [
    ("sme", "https://www.ipoplatform.com/ipo/drhp-filed-sme-ipos"),
    ("mainboard", "https://www.ipoplatform.com/ipo/drhp-filed-mainboard-ipos"),
]


def _field(item, name: str) -> str:
    for box in item.select("div.company-info"):
        span = box.find("span")
        if span and span.get_text(strip=True).rstrip(":").lower() == name.lower():
            b = box.find("b")
            return b.get_text(" ", strip=True) if b else ""
    return ""


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _details(item) -> dict:
    info = {}
    for p in item.select(".company-name p"):
        icon = p.find("i")
        classes = " ".join(icon.get("class", [])) if icon else ""
        text = _clean(p.get_text(" ", strip=True))
        if "fa-map-marker" in classes and text:
            info["city"] = text
        elif "fa-industry" in classes and text:
            info["sector"] = text
    for box in item.select("div.company-info"):
        if "merchant bankers" in box.get_text(" ", strip=True).lower():
            names = list(dict.fromkeys(_clean(x.get_text(" ", strip=True)) for x in box.find_all("strong")))
            names = [n for n in names if n]
            if names:
                info["bankers"] = ", ".join(names)
    return info


def _iso(text: str) -> str:
    try:
        return datetime.strptime(text.strip(), "%d %b %Y").date().isoformat()
    except ValueError:
        return ""


def _exchange(raw: str, doc_url: str) -> tuple:
    """(exchange label, is_sme). The mainboard page names no exchange, so the file's host tells us."""
    up = raw.strip().upper()
    if up == "BSE SME":
        return "BSE SME", True
    if up in ("NSE EMERGE", "NSE SME"):
        return "NSE Emerge", True
    host = urlparse(doc_url).netloc.lower()
    if "nse" in host:
        return "NSE", False
    if "bse" in host:
        return "BSE", False
    return "", False


def parse(html: str, page_segment: str, base: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select("div.item"):
        name_tag = item.select_one(".company-name a[title]")
        filed = _iso(_field(item, "Date of DRHP"))
        if not name_tag or not filed:
            continue  # advert cards and layout pieces have no company or date
        company = name_tag["title"].strip()
        detail = name_tag.get("href", "") or base
        doc_url = ""
        for a in item.find_all("a", href=True):
            if "read drhp" in a.get_text(" ", strip=True).lower() and a["href"].startswith("http"):
                doc_url = a["href"].strip()
                break
        exchange, is_sme = _exchange(_field(item, "Exchange"), doc_url)
        segment = "sme" if is_sme else page_segment
        status_btn = item.select_one("a.btn")
        status = status_btn.get_text(" ", strip=True).lower() if status_btn else ""
        common = dict(
            source=NAME, company=company, segment=segment, exchange=exchange,
            url=detail, filed_on=filed, doc_url=doc_url, info=_details(item),
        )
        out.append(Record(event="drhp_filed", title=f"{company} DRHP", **common))
        if "approved" in status:
            out.append(Record(event="approval", title=f"{company} DRHP approved", **common))
    return out


def fetch(f) -> SourceResult:
    records, notes, answered = [], [], 0
    for segment, url in PAGES:
        r = f.get(f"{NAME}_{segment}", url)
        if not r.ok:
            notes.append(f"{segment}: {r.error or 'no response'}")
            continue
        answered += 1
        items = parse(r.text, segment, url)
        if not items:
            notes.append(f"{segment}: answered, nothing parsed")
        records.extend(items)
    if not answered:
        return SourceResult(NAME, False, [], "; ".join(notes))
    return SourceResult(NAME, True, records, "; ".join(notes) or f"{len(records)} items")
