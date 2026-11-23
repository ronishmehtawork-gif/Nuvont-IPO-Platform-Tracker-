from datetime import datetime

import requests

from radar import run as radar_run
from radar.telegram import Telegram, _plain, format_alert, format_digest, format_official_update

NOW = datetime(2026, 11, 23, 13, 5, tzinfo=radar_run.IST)
BSE_ZIP = "https://www.bsesme.com/download/1/DRHP_Zadley.zip"


def entry(**kw):
    base = {
        "company": "Zadley India Limited", "event": "drhp_filed", "segment": "sme", "exchange": "BSE SME",
        "filed_on": "2026-11-16", "sources": {"ipoplatform": "https://www.ipoplatform.com/ipo/zadley/1"},
        "official_url": "", "documents": [{"label": "DRHP ZIP on BSE", "url": BSE_ZIP}],
        "info": {"city": "Noida", "sector": "Clothing and Apparels", "bankers": "Expert Global Consultants Private Limited"},
    }
    base.update(kw)
    return base


def test_sme_filing_card_has_the_summary_a_reader_needs():
    t = format_alert(entry(), NOW)
    assert t.splitlines()[0] == "🆕 <b>New DRHP detected</b> · BSE SME"
    assert "<b>Zadley India Limited</b>" in t and "<i>Clothing and Apparels · Noida</i>" in t
    assert "📅 DRHP filed 16 Nov 2026 (7 days ago)" in t
    assert "🏦 Lead manager: Expert Global Consultants Private Limited" in t
    assert f'📥 <a href="{BSE_ZIP}">Download · DRHP ZIP on BSE</a>' in t
    assert "<blockquote>Next: review by the exchange." in t
    assert "via IPO Platform · detected 23 Nov, 1:05 PM IST" in t
    assert "will follow" not in t


def test_mainboard_card_names_sebi_and_the_regulator_chain():
    e = entry(segment="mainboard", exchange="SEBI", official_url="https://www.sebi.gov.in/filings/x.html",
              documents=[{"label": "Draft offer document (PDF)", "url": "https://www.sebi.gov.in/a.pdf"}],
              sources={"sebi": "https://www.sebi.gov.in/filings/x.html"}, info={})
    t = format_alert(e, NOW)
    assert "· Mainboard · SEBI" in t.splitlines()[0]
    assert "Next: review by SEBI and the exchange." in t
    assert "🔗 <a href=\"https://www.sebi.gov.in/filings/x.html\">SEBI filing page</a>" in t
    assert "<i>" not in t.split("<blockquote>")[0].replace("<i>via", "")  # no empty tagline when details are missing


def test_each_event_type_reads_differently():
    approved = format_alert(entry(event="approval"), NOW)
    refiled = format_alert(entry(event="drhp_refiled"), NOW)
    issue = format_alert(entry(event="issue", documents=[]), NOW)
    assert approved.startswith("✅ <b>Approval update</b>") and "Approval received." in approved
    assert refiled.startswith("🔁 <b>DRHP refiled</b>") and "replaces the earlier one" in refiled
    assert issue.startswith("🚀 <b>Issue update</b>") and "Open the report" in issue and "will follow" in issue


def test_card_without_details_still_looks_clean():
    t = format_alert(entry(info={}, filed_on="", documents=[]), NOW)
    assert "🏦" not in t and "📅" not in t and "Open the report" in t


def test_filed_today_and_special_characters():
    t = format_alert(entry(filed_on="2026-11-23", company="A & B <Foods> Ltd"), NOW)
    assert "(today)" in t and "A &amp; B &lt;Foods&gt; Ltd" in t


def test_digest_groups_by_segment():
    items = [entry(), entry(company="Big Co Limited", segment="mainboard", exchange="NSE", event="approval")]
    t = format_digest(items, "https://example.com/list")
    assert "2 more filings" in t and "<b>SME</b>" in t and "<b>Mainboard</b>" in t
    assert "• Big Co Limited (NSE · Approved)" in t and "See the full list" in t


def test_follow_up_reply_names_the_company_and_link():
    t = format_official_update(entry())
    assert t.startswith("📥 <b>Download link found</b>") and "Zadley India Limited" in t and BSE_ZIP in t


def test_plain_text_fallback_keeps_the_links():
    plain = _plain(format_alert(entry(), NOW))
    assert "<" not in plain and f"Download · DRHP ZIP on BSE ({BSE_ZIP})" in plain


class Resp:
    def __init__(self, code, body):
        self.status_code, self._body, self.ok = code, body, code < 400

    def json(self):
        return self._body


def test_rejected_formatting_is_resent_as_plain_text():
    tg = Telegram("T", "@chan", "1", pause=0)
    calls = []

    def post(url, json, timeout):
        calls.append(dict(json))
        if "parse_mode" in json:
            return Resp(400, {"description": "Bad Request: can't parse entities: Unsupported start tag"})
        return Resp(200, {"result": {"message_id": 5}})

    tg.session.post = post
    assert tg.send_channel("<b>Hi</b> <blockquote>x</blockquote>") == 5
    assert len(calls) == 2 and "parse_mode" not in calls[1] and "<" not in calls[1]["text"]
