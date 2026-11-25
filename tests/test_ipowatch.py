"""IPO pipeline: reading the GMP table, the derived numbers, and keeping the last good data on a bad fetch."""
from datetime import date, datetime

import pytest

from radar import ipowatch as w
from radar.http import FetchResult
from radar.market import IST

TODAY = date(2026, 11, 26)
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=IST)

HEAD = "<tr><th>IPO NAME</th><th>IPO GMP</th><th>TREND</th><th>PRICE BAND</th><th>DATE</th><th>TYPE</th><th>STATUS</th><th>LAST UPDATED</th></tr>"


def row(name, gmp, band, dates, typ, status, updated="25 Nov 2026", href=None):
    link = f'<a href="{href}">{name}</a>' if href else name
    return (f"<tr><td>{link}</td><td>{gmp}</td><td>↑</td><td>{band}</td><td>{dates}</td><td>{typ}</td>"
            f"<td>{status}</td><td>{updated}</td></tr>")


PAGE = "<html><body><table>" + HEAD + "".join([
    row("Mahanadi Coalfields", "₹0", "₹129 - ₹136", "19 Nov - 23 Nov", "Mainboard", "Closed",
        href="https://ipowatch.in/mahanadi-coalfields-ipo-details/"),
    row("Watrana Rentals", "+₹41", "₹232 - ₹245", "19 Nov - 23 Nov", "NSE Emerge", "Closed",
        href="https://ipowatch.in/watrana-rentals-ipo-details/"),
    row("Shree Siddhivinayak Greentech Industries", "+₹20", "₹130 - ₹142", "25 Nov - 27 Nov", "NSE Emerge", "Open",
        href="https://ipowatch.in/shree-siddhivinayak-ipo-details/"),
    row("Foodlink F&B Holdings", "+₹27", "₹181 - ₹191", "26 Nov - 30 Nov", "Mainboard", "Open",
        href="https://ipowatch.in/foodlink-ipo-details/"),
    row("A J Packaging", "--", "₹170 - ₹180", "1 Dec - 3 Dec", "NSE Emerge", "Upcoming",
        href="https://evil.example/ajp"),
    row("Old Listed Co", "+₹5", "₹90 - ₹95", "1 Oct - 5 Oct", "BSE SME", "Closed"),
    row("Broken Dates Co", "₹3", "₹90 - ₹95", "soon", "BSE SME", "Upcoming"),
    row("No Status Co", "₹3", "₹90 - ₹95", "1 Dec - 3 Dec", "BSE SME", "Withdrawn"),
]) + "</table></body></html>"


def test_field_parsers():
    assert [w.parse_money(t) for t in ("+₹41", "₹0", "-₹5", "₹ -12.5", "--", "", "−₹3")] == [41.0, 0.0, -5.0, -12.5, None, None, -3.0]
    assert w.parse_band("₹129 - ₹136") == (129.0, 136.0) and w.parse_band("₹1,136") == (1136.0, 1136.0)
    assert w.parse_band("--") == (None, None)
    assert w.parse_date_range("19 Nov - 23 Nov", TODAY) == ("2026-11-19", "2026-11-23")
    assert w.parse_date_range("29 Dec - 2 Jan", date(2026, 12, 20)) == ("2026-12-29", "2027-01-02")
    assert w.parse_date_range("5 Jan - 8 Jan", date(2026, 12, 28)) == ("2027-01-05", "2027-01-08")
    assert w.parse_date_range("tomorrow", TODAY) == ("", "")
    assert w.parse_full_date("25 Nov 2026") == "2026-11-25" == w.parse_full_date("Nov 25, 2026")
    assert w.parse_full_date("Wednesday, December 3, 2026") == "2026-12-03"


def test_money_units_all_become_crores():
    assert w.to_crore(175.5, "Crores") == 175.5
    assert w.to_crore(1500, "Lakhs") == 15.0
    assert w.to_crore(2500, "Million") == 250.0 or w.to_crore(2500, "Million") == 250
    assert w.to_crore(1.2, "Billion") == 120.0
    assert w.to_crore(5, "furlongs") is None


def test_table_rows_become_clean_items():
    items = w.parse_table(PAGE, TODAY)
    by = {i["n"]: i for i in items}
    assert len(items) == 8
    foodlink = by["Foodlink F&B Holdings"]
    assert foodlink == {"n": "Foodlink F&B Holdings", "u": "https://ipowatch.in/foodlink-ipo-details/", "sg": "M", "x": "",
                        "st": "open", "o": "2026-11-26", "c": "2026-11-30", "lo": 181.0, "hi": 191.0, "gmp": 27.0,
                        "up": "2026-11-25"}
    assert by["Watrana Rentals"]["sg"] == "S" and by["Watrana Rentals"]["x"] == "NSE Emerge"
    assert by["A J Packaging"]["u"] == "" and by["A J Packaging"]["gmp"] is None  # outside link dropped, '--' is no quote
    assert by["Broken Dates Co"]["o"] == "" and by["No Status Co"]["st"] == ""


def test_checks_drop_rows_that_cannot_be_right():
    items = w.validate_items(w.parse_table(PAGE, TODAY))
    names = {i["n"] for i in items}
    assert "No Status Co" not in names and "Foodlink F&B Holdings" in names
    bad = dict(items[0], lo=300.0, hi=100.0)
    assert w.validate_items([bad]) == []
    assert w.validate_items([dict(items[0], o="2026-12-05", c="2026-12-01")]) == []


def test_selection_order_and_closed_cutoff():
    items = w.validate_items(w.parse_table(PAGE, TODAY))
    shown = [i["n"] for i in w.select(items, TODAY)]
    # open (earliest close first), then upcoming (earliest open first, undated last), then closed (latest first)
    assert shown == ["Shree Siddhivinayak Greentech Industries", "Foodlink F&B Holdings", "A J Packaging",
                     "Broken Dates Co", "Watrana Rentals", "Mahanadi Coalfields"]
    assert "Old Listed Co" not in shown  # closed in October


def test_derived_numbers():
    it = w.derive({"hi": 191.0, "gmp": 27.0, "lot": 78})
    assert it["pct"] == 14.14 and it["est"] == 218.0 and it["inv"] == 14898
    assert w.derive({"hi": 100.0, "gmp": -4.0})["pct"] == -4.0
    nope = w.derive({"hi": 100.0, "gmp": 900.0})              # a 9x premium is a parsing slip
    assert nope["gmp"] is None and nope["pct"] is None and nope["est"] is None
    assert w.derive({"hi": None, "gmp": 5.0})["pct"] is None and w.derive({"hi": 50.0, "gmp": None})["est"] is None


def test_detail_page_facts_in_crores():
    html = ("<html><body><h1>Foodlink IPO</h1><table><tr><td>Issue Size</td><td>₹360.76 Crores</td></tr>"
            "<tr><td>Lot Size</td><td>78 Shares</td></tr><tr><td>Listing Date</td><td>Wednesday, December 3, 2026</td></tr>"
            "</table></body></html>")
    assert w.parse_details(html) == {"sz": 360.76, "lot": 78, "ls": "2026-12-03"}
    lakhs = "<p>Issue Size: ₹1,500 Lakhs</p><p>Market Lot 1,200 shares</p>"
    assert w.parse_details(lakhs) == {"sz": 15.0, "lot": 1200}
    assert w.parse_details("<p>nothing useful</p>") == {}


ISSUE_PAGE = """<html><body>
<nav><a href="https://ipowatch.in/sectors/banking/">Banking</a></nav>
<h1>Foodlink F&amp;B Holdings IPO Details</h1><div>MainboardOpen</div>
<div><a href="https://ipowatch.in/sectors/hotels-and-resorts/">Hotels &amp; Resorts</a> &middot; Listed on BSE , NSE &middot; 26 Nov 2026 - 30 Nov 2026</div>
<div>Issue Size</div><div>360.76 Cr</div><div>Lot Size</div><div>78 Shares</div>
<div>26 Nov 2026</div><div>IPO Opens</div><div>ACTIVE</div><div>30 Nov 2026</div>
<div>Lead Managers</div>
<div><a href="https://ipowatch.in/lead-managers/equirus-capital-pvt-ltd/">Equirus Capital Pvt Ltd</a></div>
<div><a href="https://ipowatch.in/lead-managers/jm-financial-ltd/">JM Financial Ltd</a></div>
<div>Registrar</div><div><a href="https://ipowatch.in/registrar/mufg-intime-india-pvt-ltd/">MUFG Intime India Pvt Ltd</a></div>
<h2>IPO Timeline</h2>
<div>IPO Opens</div><div>ACTIVE</div><div>26 Nov 2026</div>
<div>IPO Closes</div><div>PENDING</div><div>30 Nov 2026</div>
<div>Basis of Allotment</div><div>PENDING</div><div>01 Dec 2026</div>
<div>Refunds</div><div>PENDING</div><div>02 Dec 2026</div>
<div>Demat Credit</div><div>PENDING</div><div>02 Dec 2026</div>
<div>Listing</div><div>PENDING</div><div>03 Dec 2026</div>
<div><a href="https://ipowatch.in/lead-managers/someone-else-ltd/">Someone Else Ltd</a></div>
</body></html>"""


def test_issue_page_gives_dates_bankers_registrar_and_sector():
    got = w.parse_details(ISSUE_PAGE)
    assert got["sc"] == "Hotels & Resorts"                       # not the 'Banking' link in the menu
    assert got["ex"] == "BSE, NSE"
    assert got["bk"] == ["Equirus Capital", "JM Financial"]      # stops at the Registrar label
    assert got["rg"] == "MUFG Intime India"
    assert (got["al"], got["rf"], got["dc"], got["ls"]) == ("2026-12-01", "2026-12-02", "2026-12-02", "2026-12-03")
    assert (got["o2"], got["c2"]) == ("2026-11-26", "2026-11-30")
    assert got["sz"] == 360.76 and got["lot"] == 78


def test_dates_are_kept_only_where_the_issue_process_puts_them():
    item = {"o": "2026-11-26", "c": "2026-11-30", "al": "2026-12-01", "rf": "2026-12-02", "dc": "2026-12-02", "ls": "2026-12-03"}
    assert w.check_dates(dict(item)) == item
    bad = w.check_dates(dict(item, al="2026-12-25", rf="2026-12-01", ls="2027-01-20"))
    assert "al" not in bad and "ls" not in bad and bad["rf"] == "2026-12-01" and bad["dc"] == "2026-12-02"
    backwards = w.check_dates(dict(item, rf="2026-12-01", al="2026-12-02"))
    assert "rf" not in backwards and backwards["al"] == "2026-12-02"
    no_close = w.check_dates({"o": "2026-11-26", "c": "", "al": "2026-12-01"})
    assert "al" not in no_close


def test_banker_names_are_tidied_so_the_same_bank_matches_across_issues():
    assert w.clean_banker("Equirus Capital Pvt Ltd") == "Equirus Capital"
    assert w.clean_banker("Anand Rathi Advisors Limited") == "Anand Rathi Advisors"
    assert w.clean_banker("JM Financial Ltd.") == "JM Financial"
    assert w.clean_banker("Ambit Private Limited") == "Ambit"


# ----------------------------------------------------------------------------- the job

class FakeSite:
    def __init__(self, table=PAGE, detail=None, ok=True):
        self.table, self.ok = table, ok
        self.detail = detail or ("<p>Issue Size ₹54.00 Crores</p><p>Lot Size 800 Shares</p>"
                                 "<p>Listing Date 8 Dec 2026</p>")
        self.detail_calls = 0

    def get(self, name, url):
        if not self.ok:
            return FetchResult(False, 503, "", "HTTP 503")
        if name == "ipowatch_detail":
            self.detail_calls += 1
            return FetchResult(True, 200, self.detail, "")
        return FetchResult(True, 200, self.table, "")


def test_job_saves_enriches_and_caches_details(tmp_path):
    path = str(tmp_path / "ipos.json")
    site = FakeSite()
    payload, info = w.refresh(site, path, NOW)
    assert info["ok"] and info["shown"] == 6 and info["with_size"] == 4 and info["with_gmp"] == 5
    foodlink = next(i for i in payload["items"] if i["n"].startswith("Foodlink"))
    assert foodlink["sz"] == 54.0 and foodlink["lot"] == 800 and foodlink["inv"] == 152_800 and foodlink["ls"] == "2026-12-08"
    first_calls = site.detail_calls
    assert first_calls == 4                                   # four issues have their own page
    w.refresh(site, path, NOW)
    assert site.detail_calls == first_calls                   # details are cached for the day


def test_job_keeps_last_good_data_when_the_site_fails_or_changes(tmp_path):
    path = str(tmp_path / "ipos.json")
    good, _ = w.refresh(FakeSite(), path, NOW)
    saved = (tmp_path / "ipos.json").read_text()
    down, info = w.refresh(FakeSite(ok=False), path, NOW)
    assert not info["ok"] and "HTTP 503" in info["note"] and down == good
    changed, info2 = w.refresh(FakeSite(table="<html><table><tr><th>A</th><th>B</th></tr></table></html>"), path, NOW)
    assert not info2["ok"] and "no table" in info2["note"] and changed == good
    assert (tmp_path / "ipos.json").read_text() == saved


def test_first_run_with_a_bad_site_has_nothing_to_show(tmp_path):
    payload, info = w.refresh(FakeSite(ok=False), str(tmp_path / "ipos.json"), NOW)
    assert payload is None and not info["ok"] and w.load(str(tmp_path / "ipos.json")) is None
