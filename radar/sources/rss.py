"""Generic RSS/Atom adapter. Keeps only items whose headline is about a DRHP or an approval."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..classify import classify_event, classify_segment, extract_company
from ..models import Record, SourceResult

ATOM = "{http://www.w3.org/2005/Atom}"
KEEP = {"drhp_filed", "drhp_refiled", "approval"}


def parse_feed(xml_text: str, source: str) -> list:
    root = ET.fromstring(xml_text.strip())
    items = []
    for item in root.iter("item"):
        items.append((item.findtext("title") or "", (item.findtext("link") or "").strip()))
    for entry in root.iter(ATOM + "entry"):
        link = entry.find(ATOM + "link")
        items.append(
            (entry.findtext(ATOM + "title") or "", link.get("href", "") if link is not None else "")
        )
    out = []
    for title, link in items:
        title = title.strip()
        event = classify_event(title)
        company = extract_company(title)
        if event not in KEEP or not company or not link:
            continue
        segment, exchange = classify_segment(title)
        out.append(
            Record(
                source=source,
                company=company,
                event=event,
                segment=segment,
                exchange=exchange,
                url=link,
                title=title,
            )
        )
    return out


def make_fetch(name: str, url: str):
    def fetch(f) -> SourceResult:
        r = f.get(name, url)
        if not r.ok:
            return SourceResult(name, False, [], r.error or "no response")
        try:
            records = parse_feed(r.text, name)
        except ET.ParseError:
            return SourceResult(name, False, [], "response was not a valid feed")
        return SourceResult(name, True, records, f"{len(records)} items matched")

    return fetch
