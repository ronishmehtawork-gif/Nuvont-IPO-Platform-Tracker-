from datetime import datetime

from radar import run as radar_run
from radar import state as st
from radar.documents import find_pdf_links
from radar.http import FetchResult
from radar.models import Record, SourceResult
from radar.telegram import format_alert

BASE = "https://www.sebi.gov.in/filings/public-issues/oct-2026/gamp-services-limited-drhp_105424.html"
PAGE = """
<html><body>
<a href="https://www.sebi.gov.in/sebi_data/commondocs/oct-2026/GAMP%20Services%20Limited%20-%20Draft%20Abridged%20Prospectus_p.pdf">Draft Abridged Prospectus</a>
<a href="../../../web/?file=https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1792995489638.pdf">GAMP Services Limited - DRHP</a>
<a href="/about-sebi.html">About SEBI</a>
<a href="https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1792995489638.pdf">same file again</a>
</body></html>
"""


def test_finds_direct_pdfs_and_unwraps_the_viewer_link():
    docs = find_pdf_links(PAGE, BASE)
    urls = [d["url"] for d in docs]
    assert urls == [
        "https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1792995489638.pdf",
        "https://www.sebi.gov.in/sebi_data/commondocs/oct-2026/GAMP%20Services%20Limited%20-%20Draft%20Abridged%20Prospectus_p.pdf",
    ]
    assert docs[0]["label"] == "Draft offer document (PDF)"
    assert docs[1]["label"] == "Abridged prospectus (PDF)"


def test_page_without_pdfs_gives_nothing():
    assert find_pdf_links("<a href='/x.html'>x</a>", BASE) == []


def test_alert_shows_download_links_then_filing_page():
    entry = {
        "company": "GAMP Services Limited", "event": "drhp_filed", "segment": "mainboard",
        "exchange": "SEBI", "filed_on": "2026-10-21", "sources": {"sebi": BASE}, "official_url": BASE,
        "documents": find_pdf_links(PAGE, BASE),
    }
    text = format_alert(entry, datetime(2026, 11, 23, 12, 0, tzinfo=radar_run.IST))
    assert "Download: Draft offer document (PDF)" in text
    assert "1792995489638.pdf" in text
    assert "SEBI filing page" in text


def test_alert_without_documents_keeps_the_old_link():
    entry = {
        "company": "X Ltd", "event": "drhp_filed", "segment": "mainboard", "exchange": "SEBI",
        "filed_on": "", "sources": {"sebi": BASE}, "official_url": BASE,
    }
    text = format_alert(entry, datetime(2026, 11, 23, 12, 0, tzinfo=radar_run.IST))
    assert "Open the official document" in text and "Download:" not in text


class FakeTG:
    enabled = True

    def __init__(self):
        self.channel = []

    def send_channel(self, text, reply_to=None):
        self.channel.append(text)
        return len(self.channel)

    def send_admin(self, text):
        pass


class FakeFetcher:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def get(self, name, url):
        self.calls.append(url)
        return FetchResult(self.ok, 200 if self.ok else None, PAGE if self.ok else "", "" if self.ok else "boom")


def _run(fetcher):
    state = st.empty_state()
    state["started"] = True
    state["health"]["sebi"] = {"failures": 0, "down_alerted": False, "empty_streak": 0,
                               "ever_parsed": True, "last_ok": "", "last_note": ""}
    rec = Record("sebi", "GAMP Services Limited", "drhp_filed", "mainboard", "SEBI", BASE, filed_on="2026-10-21")
    tg = FakeTG()
    now = datetime(2026, 11, 23, 12, 0, tzinfo=radar_run.IST)
    radar_run.run_once([("sebi", lambda f: SourceResult("sebi", True, [rec], ""))], fetcher, tg, state, now,
                       {"sme", "unknown", "mainboard"})
    return state, tg


def test_run_fetches_the_filing_page_once_and_adds_links():
    f = FakeFetcher()
    state, tg = _run(f)
    assert f.calls == [BASE]
    assert "Download: Draft offer document (PDF)" in tg.channel[0]
    assert len(state["seen"]["gamp services|drhp_filed"]["documents"]) == 2


def test_run_still_alerts_when_the_filing_page_cannot_be_read():
    state, tg = _run(FakeFetcher(ok=False))
    assert len(tg.channel) == 1 and "Open the official document" in tg.channel[0]
