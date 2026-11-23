from __future__ import annotations

import re
from datetime import date

_REFILE = re.compile(r"\brefil", re.I)
_DRHP = re.compile(
    r"\bdrhp\b|draft (red herring )?prospectus|draft papers|draft offer", re.I
)
_APPROVAL = re.compile(
    r"in[- ]principle|green signal|\bnod\b|observations?\b|\bapproval\b", re.I
)
_ISSUE = re.compile(r"\brhp\b|price band|\banchor\b|ipo opens", re.I)

_LEAD = re.compile(r"^(?:upcoming ipo|new ipo|ipo alert|breaking)\s*[!:\-–]*\s*", re.I)
_COMPANY = re.compile(r"^(?P<c>.+?\b(?:Ltd\.?|Limited|LLP|Trust))(?=[\s:,;]|$)", re.I)
_VERB = re.compile(
    r"^(?P<c>.+?)\s+(?:files|filed|refiles|submits|submitted|advances|opts|gets|"
    r"receives|secures|bags|clears)\b",
    re.I,
)

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


def classify_event(text: str) -> str | None:
    if _REFILE.search(text):
        return "drhp_refiled"
    if _DRHP.search(text):
        return "drhp_filed"
    if _APPROVAL.search(text):
        return "approval"
    if _ISSUE.search(text):
        return "issue"
    return None


def classify_segment(text: str) -> tuple[str, str]:
    """Return (segment, exchange) from free text."""
    t = text.lower()
    if "bse sme" in t:
        return "sme", "BSE SME"
    if "nse emerge" in t or re.search(r"\bemerge\b", t):
        return "sme", "NSE Emerge"
    if re.search(r"\bsme\b", t):
        return "sme", ""
    if "sebi" in t:
        return "mainboard", "SEBI"
    return "unknown", ""


def extract_company(title: str) -> str | None:
    t = _LEAD.sub("", title.strip())
    mc = _COMPANY.match(t)
    mv = _VERB.match(t)
    if mc and (not mv or len(mc.group("c")) <= len(mv.group("c"))):
        c = mc.group("c")
    elif mv:
        c = mv.group("c")
    else:
        return None
    c = c.strip(" ,;:")
    return c if 2 < len(c) <= 80 else None


def parse_date(text: str) -> str:
    """Best-effort date from text. Returns ISO yyyy-mm-dd or ''."""
    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b", text)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        return _iso(y, mo, d)
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3})[a-z]*[\s,\-]+(\d{4})\b", text)
    if m and m.group(2).lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    m = re.search(r"\b([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b", text)
    if m and m.group(1).lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
    return ""


def _iso(y: int, mo: int, d: int) -> str:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return ""
