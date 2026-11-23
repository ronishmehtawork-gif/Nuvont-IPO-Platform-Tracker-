import pytest

from radar.classify import classify_event, classify_segment, extract_company, parse_date
from radar.models import normalize_company


@pytest.mark.parametrize(
    "title,company,event,segment,exchange",
    [
        ("Intergen Energy Ltd. Files IPO Draft Papers with the BSE SME",
         "Intergen Energy Ltd.", "drhp_filed", "sme", "BSE SME"),
        ("Biocon Electric Ltd. Advances IPO Plans, Files DRHP with SEBI",
         "Biocon Electric Ltd.", "drhp_filed", "mainboard", "SEBI"),
        ("Franchiseindia.com Ltd. Refiles DRHP for Its Upcoming IPO",
         "Franchiseindia.com Ltd.", "drhp_refiled", "unknown", ""),
        ("IPO alert! Sai Urja Indo Ventures gets green signal from BSE SME; details",
         "Sai Urja Indo Ventures", "approval", "sme", "BSE SME"),
        ("Bonfiglioli Transmissions files DRHP for an IPO",
         "Bonfiglioli Transmissions", "drhp_filed", "unknown", ""),
        ("Vishwas Refoils & Consumer Ltd. Files DRHP: Check Issue Size and Key Details",
         "Vishwas Refoils & Consumer Ltd.", "drhp_filed", "unknown", ""),
        ("Upcoming IPO! Hindustan Laboratories files DRHP with Sebi; check details",
         "Hindustan Laboratories", "drhp_filed", "mainboard", "SEBI"),
    ],
)
def test_headlines(title, company, event, segment, exchange):
    assert extract_company(title) == company
    assert classify_event(title) == event
    assert classify_segment(title) == (segment, exchange)


def test_unrelated_headline_is_ignored():
    assert classify_event("Stock markets close higher on Friday") is None
    assert extract_company("Markets close higher on Friday") is None


def test_company_name_variants_match():
    names = ["Example Industries Limited", "EXAMPLE INDUSTRIES LTD.", "Example Industries Pvt. Ltd."]
    assert len({normalize_company(n) for n in names}) == 1
    assert normalize_company("A & B Ltd") == normalize_company("A and B Limited")


def test_parse_date_formats():
    assert parse_date("Filed on 20/11/2026") == "2026-11-20"
    assert parse_date("20-Nov-2026") == "2026-11-20"
    assert parse_date("Nov 20, 2026") == "2026-11-20"
    assert parse_date("5th December 2026") == "2026-12-05"
    assert parse_date("no date here") == ""
    assert parse_date("31/02/2026") == ""
