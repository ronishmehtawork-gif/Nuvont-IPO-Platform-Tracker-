from __future__ import annotations

import re
from dataclasses import dataclass

OFFICIAL_SOURCES = {"bse_sme", "sebi", "nse_emerge"}

SOURCE_LABELS = {
    "bse_sme": "BSE SME",
    "sebi": "SEBI",
    "nse_emerge": "NSE",
    "chittorgarh": "Chittorgarh",
    "rss_ipocentral": "IPOCentral",
}

EVENT_LABELS = {
    "drhp_filed": "New DRHP detected",
    "drhp_refiled": "DRHP refiled",
    "approval": "Approval update",
    "issue": "Issue update",
    "addendum": "DRHP addendum",
}

# Recorded and shown on the site, but not pushed to the channel.
ALERT_EVENTS = {"drhp_filed", "drhp_refiled", "approval", "issue"}

SEGMENT_RANK = {"unknown": 0, "mainboard": 1, "sme": 2}


def label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def normalize_company(name: str) -> str:
    """Make 'Example Industries Ltd.' and 'EXAMPLE INDUSTRIES LIMITED' compare equal."""
    n = name.lower().replace("&", " and ")
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\b(private|pvt|limited|ltd)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


@dataclass
class Record:
    source: str
    company: str
    event: str  # drhp_filed | drhp_refiled | approval | issue
    segment: str  # sme | mainboard | unknown
    exchange: str  # "BSE SME" | "NSE Emerge" | "SEBI" | ""
    url: str
    title: str = ""
    filed_on: str = ""  # ISO date when the source shows one

    @property
    def official(self) -> bool:
        return self.source in OFFICIAL_SOURCES

    @property
    def key(self) -> str:
        return f"{normalize_company(self.company)}|{self.event}"


@dataclass
class SourceResult:
    name: str
    ok: bool
    records: list
    note: str = ""
