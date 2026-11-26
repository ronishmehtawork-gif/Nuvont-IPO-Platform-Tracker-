import json

from radar import bankers as bk
from radar.site import build_site, enrich_ipos

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def test_names_are_cleaned_and_renamed_firms_are_merged():
    assert bk.canon("Dam Capital Advisors Limited (Formerly Idfc Securities Limited)") == "DAM Capital Advisors"
    assert bk.canon("Oneview Corporate Advisors Private Limited (Formerly known as Guiness Corporate Advisors)") == "Oneview Corporate Advisors"
    assert bk.canon("IIFL Securities") == bk.canon("IIFL Capital Services Ltd.") == "IIFL Capital Services"
    assert bk.canon("Jm Financial") == "JM Financial" and bk.canon("Hdfc Bank") == "HDFC Bank"
    assert bk.canon("ERUDORE CAPITAL") == bk.canon("Erudore Capital") == "Erudore Capital"
    assert bk.canon("360 ONE WAM") == "360 ONE WAM"
    assert bk.canon("N/A") == "" and bk.canon("") == "" and bk.canon(None) == ""
    assert bk.canon_list(["Axis Capital", "Axis Capital Limited", "n/a"]) == ["Axis Capital"]


def test_ipo_desk_issues_use_clean_manager_names():
    out = enrich_ipos([{"n": "Acme Ltd", "bk": ["JM FINANCIAL", "Jm Financial Limited"]}], [])
    assert out[0]["bk"] == ["JM Financial"]


def test_the_ledger_grows_as_issues_are_seen_and_never_invents_a_listing_price(tmp_path):
    path = str(tmp_path / "rec.json")
    items = [{"n": "Alpha Industries Limited", "sg": "M", "bk": ["Axis Capital"], "sz": 450.5, "hi": 120.0, "ls": "2026-12-03", "o": "2026-11-26"}]
    bk.update_ledger(items, path, "2026-11-26")
    items[0]["sz"] = 460.0
    ledger = bk.update_ledger(items + [{"n": "Beta Ltd", "sg": "S", "bk": [], "sz": 20.0}], path, "2026-11-27")
    assert ledger["since"] == "2026-11-26" and len(ledger["ipos"]) == 2
    alpha = next(r for r in ledger["ipos"] if r["n"].startswith("Alpha"))
    assert alpha["sz"] == 460.0 and alpha["ip"] == 120.0 and alpha["bk"] == ["Axis Capital"] and "lp" not in alpha
    rec = bk.record(path, str(tmp_path / "none.csv"))
    assert [r["n"] for r in rec["ipos"]] == ["Alpha Industries Limited"]  # an issue with no manager is not listed


def test_seed_file_adds_listing_prices_and_skips_bad_rows(tmp_path):
    path, seed = str(tmp_path / "rec.json"), tmp_path / "seed.csv"
    bk.update_ledger([{"n": "Alpha Industries Limited", "sg": "M", "bk": ["Axis Capital"], "sz": 450.5, "hi": 120.0}], path, "2026-11-26")
    seed.write_text(
        "company,segment,lead_managers,issue_size_cr,issue_price,listing_date,listing_price,current_price\n"
        "Alpha Industries,mainboard,Axis Capital;ICICI Securities,450.5,120,2026-12-03,132,140\n"
        "Gamma Ltd,sme,GYR Capital Advisors,30,50,2026-10-01,45,\n"
        "Broken Ltd,sme,,30,50,2026-10-01,45,\n"
        ",sme,Axis Capital,1,1,,,\n")
    rows, skipped = bk.read_seed(str(seed))
    assert len(rows) == 2 and skipped == 2
    rec = bk.record(path, str(seed))
    by = {r["n"]: r for r in rec["ipos"]}
    assert by["Alpha Industries"]["lp"] == 132 and by["Alpha Industries"]["cp"] == 140 and by["Alpha Industries"]["bk"] == ["Axis Capital", "ICICI Securities"]
    assert by["Gamma Ltd"]["sg"] == "S" and "cp" not in by["Gamma Ltd"]
    assert rec["seed"] == 2 and rec["skipped"] == 2


def test_the_record_travels_in_the_page(tmp_path):
    out = tmp_path / "index.html"
    rec = {"since": "2026-11-26", "ipos": [{"n": "Alpha", "sg": "M", "bk": ["Axis Capital"], "sz": 10.0}], "seed": 0, "skipped": 0}
    build_site({"seen": {}}, str(out), datetime(2026, 11, 26, 12, 0, tzinfo=IST), bankers=rec)
    html = out.read_text()
    assert '"bankers":{"since":"2026-11-26"' in html
    build_site({"seen": {}}, str(out), datetime(2026, 11, 26, 12, 0, tzinfo=IST))
    assert '"bankers":null' in out.read_text()
    assert json.loads(json.dumps(rec)) == rec
