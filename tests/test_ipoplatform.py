from datetime import datetime

from radar import run as radar_run
from radar import state as st
from radar.http import FetchResult
from radar.models import SourceResult
from radar.sources import ipoplatform

SME_URL = "https://www.ipoplatform.com/ipo/drhp-filed-sme-ipos"


def card(name, date, exchange, doc, status, detail="https://www.ipoplatform.com/ipo/x/1"):
    ex = f'<div class="company-info"><span>Exchange:</span><b>{exchange}</b></div>' if exchange else ""
    read = f'<a href="{doc}" target="_blank">Read DRHP <i></i></a>' if doc else ""
    return f"""
<div class="item ml10px">
  <div class="company-name"><a href="{detail}" title="{name}"><h4>{name}</h4></a>
    <p><a href="/geo/noida">Noida</a></p></div>
  <div class="company-info"><span>Date of DRHP:</span><b>{date}</b></div>
  <div class="company-info"><span>Revenue*:</span><b>47.08</b></div>
  {ex}
  <div class="company-info text-center">Merchant Bankers: <a href="/mb/1"><strong>Some Bank</strong></a></div>
  <div class="company-info text-center">{read}
    <a class="nav-link btn btn-primary Under_Process text-white" href="{detail}">DRHP {status}</a></div>
</div>"""


ADVERT = '<div class="item"><a class="btn" href="/calc">Calculate Now!</a></div>'
BSE_ZIP = "https://www.bsesme.com/download/323118/SME_IPO%20InPrinciple/DRHP_Zadley_16112026_20261116190532.zip"
NSE_ZIP = "https://nsearchives.nseindia.com/corporate/AD_IPO_7991_07112026140313.zip"

SME_HTML = "<html><body>" + ADVERT + card("Zadley India Limited", "16 Nov 2026", "BSE SME", BSE_ZIP, "Under Process") \
    + card("Datasoft Computer Services Limited", "06 Nov 2026", "NSE EMERGE", NSE_ZIP, "Approved") \
    + card("No Link Limited", "01 Nov 2026", "BSE SME", "", "Under Process") + "</body></html>"

MAIN_HTML = "<html><body>" + card("Hindustan Coca Cola Holdings Limited", "06 Nov 2026", "", NSE_ZIP, "Under Process") \
    + "</body></html>"


def test_sme_page_gives_exchange_date_and_direct_file():
    recs = ipoplatform.parse(SME_HTML, "sme", SME_URL)
    z = [r for r in recs if r.company == "Zadley India Limited"]
    assert len(z) == 1  # under process: a filing, no approval
    assert (z[0].event, z[0].segment, z[0].exchange) == ("drhp_filed", "sme", "BSE SME")
    assert z[0].filed_on == "2026-11-16" and z[0].doc_url == BSE_ZIP
    assert all(r.company != "Calculate Now!" for r in recs)


def test_nse_emerge_and_approved_status_make_two_events():
    recs = [r for r in ipoplatform.parse(SME_HTML, "sme", SME_URL) if "Datasoft" in r.company]
    assert {r.event for r in recs} == {"drhp_filed", "approval"}
    assert all(r.exchange == "NSE Emerge" and r.segment == "sme" for r in recs)


def test_row_without_a_file_link_is_kept():
    recs = [r for r in ipoplatform.parse(SME_HTML, "sme", SME_URL) if r.company == "No Link Limited"]
    assert recs and recs[0].doc_url == ""


def test_mainboard_page_takes_the_exchange_from_the_file_host():
    recs = ipoplatform.parse(MAIN_HTML, "mainboard", "x")
    assert recs[0].segment == "mainboard" and recs[0].exchange == "NSE"


class Fetch:
    def __init__(self, pages):
        self.pages = pages

    def get(self, name, url):
        html = self.pages.get(url)
        return FetchResult(html is not None, 200 if html else None, html or "", "" if html else "boom")


def test_fetch_survives_one_page_failing():
    res = ipoplatform.fetch(Fetch({SME_URL: SME_HTML}))
    assert res.ok and res.records and "mainboard" in res.note
    assert not ipoplatform.fetch(Fetch({})).ok


# --- end to end ---------------------------------------------------------------------------------
IST = radar_run.IST


class FakeTG:
    enabled = True

    def __init__(self):
        self.channel, self.admin = [], []

    def send_channel(self, text, reply_to=None):
        self.channel.append((text, reply_to))
        return len(self.channel)

    def send_admin(self, text):
        self.admin.append(text)


def run(state, tg, html_sme, html_main="<html></html>", hour=11):
    fetcher = Fetch({SME_URL: html_sme, "https://www.ipoplatform.com/ipo/drhp-filed-mainboard-ipos": html_main})
    now = datetime(2026, 11, 23, hour, 0, tzinfo=IST)
    radar_run.run_once([(ipoplatform.NAME, ipoplatform.fetch)], fetcher, tg, state, now,
                       {"sme", "unknown", "mainboard"})


def test_first_pass_is_silent_then_a_new_filing_alerts_with_download_and_approval_alerts():
    state, tg = st.empty_state(), FakeTG()
    state["started"] = True
    run(state, tg, SME_HTML, MAIN_HTML)
    assert tg.channel == []  # everything on the page at first sight is baseline
    assert len(state["seen"]) >= 4

    newer = SME_HTML.replace("</body>", card("Fresh Filer Limited", "23 Nov 2026", "NSE EMERGE", NSE_ZIP, "Under Process") + "</body>")
    run(state, tg, newer, MAIN_HTML)
    assert len(tg.channel) == 1
    text = tg.channel[0][0]
    assert "New DRHP detected" in text and "Fresh Filer Limited" in text and "NSE Emerge" in text
    assert f'href="{NSE_ZIP}"' in text and "Download: DRHP ZIP on NSE" in text
    assert "will follow" not in text

    approved = newer.replace('DRHP Under Process</a>\n</div>', 'DRHP Under Process</a>\n</div>', 1)
    approved = approved.replace("DRHP Under Process", "DRHP Approved")  # every pending row flips to approved
    run(state, tg, approved, MAIN_HTML)
    assert any("Approval update" in t for t, _ in tg.channel)


def test_download_link_found_later_is_sent_as_a_reply():
    state, tg = st.empty_state(), FakeTG()
    state["started"] = True
    state["health"]["ipoplatform"] = {"failures": 0, "down_alerted": False, "empty_streak": 0,
                                      "ever_parsed": True, "last_ok": "", "last_note": ""}
    key = "zadley india|drhp_filed"
    state["seen"][key] = {"company": "Zadley India Limited", "event": "drhp_filed", "segment": "sme",
                          "exchange": "BSE SME", "filed_on": "", "first_seen": "2026-11-23T10:00:00+05:30",
                          "sources": {"chittorgarh": "https://c/x"}, "official_url": "", "message_id": 7,
                          "alerted": True, "baseline": False}
    run(state, tg, SME_HTML)
    replies = [(t, r) for t, r in tg.channel if r == 7]
    assert len(replies) == 1 and "Download link found" in replies[0][0] and BSE_ZIP in replies[0][0]
    run(state, tg, SME_HTML)
    assert len([1 for _, r in tg.channel if r == 7]) == 1  # not repeated
