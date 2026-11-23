"""Find the direct PDF links on a SEBI filing page, so an alert can offer a one-tap download."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

MAX_DOCS = 3


def _pdf_target(href: str, base: str) -> str:
    """The PDF behind a link: either the link itself, or the file= address of SEBI's viewer wrapper."""
    full = urljoin(base, href)
    query = parse_qs(urlparse(full).query)
    wrapped = query.get("file", [""])[0]
    if wrapped:
        full = urljoin(base, unquote(wrapped))
    path = urlparse(full).path.lower()
    return full if path.endswith(".pdf") else ""


def _doc_label(url: str, text: str) -> str:
    """Name a SEBI attachment from its link text. SEBI's own markup can leave raw HTML inside the text,
    so only the part before the first '<' is read, and only the last ' - ' segment is used."""
    head = text.split("<", 1)[0].strip()
    name = re.split(r"\s+[-–—]\s*", head)[-1].strip() if head else ""
    name = re.sub(r"\s*[-–—]\s*", "-", name)  # 'UDRHP- I' -> 'UDRHP-I'
    if not name or len(name) > 50:
        name = "Draft offer document" if "/attachdocs/" in url else "Document"
    return f"{name} (PDF)"


def find_pdf_links(html: str, base: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    found, seen = [], set()
    for tag in soup.find_all(["a", "iframe"]):
        href = tag.get("href") or tag.get("src") or ""
        url = _pdf_target(href, base) if href else ""
        if not url or url in seen:
            continue
        seen.add(url)
        found.append({"label": _doc_label(url, tag.get_text(" ", strip=True)), "url": url})
    # the filing's main attachment first, supporting files (abridged prospectus) after it
    found.sort(key=lambda d: "/attachdocs/" not in d["url"])
    return found[:MAX_DOCS]


def exchange_doc_label(url: str) -> str:
    """Name a document link by who hosts it, for the alert text."""
    host = urlparse(url).netloc.lower()
    kind = "ZIP" if urlparse(url).path.lower().endswith(".zip") else "file"
    if "bsesme" in host or "bseindia" in host:
        return f"DRHP {kind} on BSE"
    if "nse" in host:
        return f"DRHP {kind} on NSE"
    return f"DRHP {kind}"
