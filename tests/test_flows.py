"""FII/FPI and DII daily activity: reading the report, keeping a history, never replacing good data with bad."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from radar import flows
from radar.http import FetchResult

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 26, 20, 0, tzinfo=IST)

REPORT = """<html><body><script>var x = "FII/FPI 01-Jan-2020 1 2 3";</script>
<h3>FII trading activity on MSE, NSE and BSE in Capital Market Segment (In Crores)</h3>
<table><tr><th>Category</th><th>Date</th><th>Buy&nbsp;Value</th><th>Sell Value</th><th>Net Value</th></tr>
<tr><td>FII/FPI</td><td>25-Nov-2026</td><td>24,309.87</td><td>19,830.79</td><td>4,479.08</td></tr>
<tr><td>As on 25 Nov, 2026</td><td></td></tr></table>
<h3>DII trading activity on MSE, NSE and BSE in Capital Market Segment (In Crores)</h3>
<table><tr><th>Category</th><th>Date</th><th>Buy Value</th><th>Sell Value</th><th>Net Value</th></tr>
<tr><td>DII</td><td>25-Nov-2026</td><td>22,379.82</td><td>18,361.10</td><td>4,018.72</td></tr></table>
</body></html>"""


class Fake:
    def __init__(self, text="", ok=True, error=""):
        self.result = FetchResult(ok, 200 if ok else 500, text, error)
        self.calls = 0

    def get(self, name, url):
        self.calls += 1
        return self.result


def test_the_report_is_read_into_one_row():
    row = flows.parse(REPORT)
    assert row == {"d": "2026-11-25", "fb": 24309.87, "fs": 19830.79, "fn": 4479.08,
                   "db": 22379.82, "ds": 18361.10, "dn": 4018.72}


def test_a_net_selling_day_keeps_its_sign():
    page = REPORT.replace("4,479.08", "-6,030.38").replace("24,309.87", "14,434.69").replace("19,830.79", "20,465.07")
    assert flows.parse(page)["fn"] == -6030.38


@pytest.mark.parametrize("page,why", [
    ("<html>nothing here</html>", "not found"),
    (REPORT.replace("4,479.08", "9,999.00"), "buy minus sell"),
    (REPORT.replace("DII</td><td>25-Nov-2026", "DII</td><td>24-Nov-2026"), "DII row"),
    (REPORT.replace("24,309.87", "-5.00"), "sensible"),
])
def test_a_report_that_does_not_add_up_is_refused(page, why):
    with pytest.raises(flows.FlowsError, match=why):
        flows.parse(page)


def row(day, fn=1.0, dn=2.0):
    return {"d": day, "fb": 10.0, "fs": 9.0, "fn": fn, "db": 12.0, "ds": 10.0, "dn": dn}


def test_a_day_is_replaced_not_duplicated_and_history_is_kept_in_order():
    days = flows.merge([row("2026-11-24"), row("2026-11-25", fn=1.0)], row("2026-11-25", fn=7.0))
    assert [d["d"] for d in days] == ["2026-11-24", "2026-11-25"] and days[1]["fn"] == 7.0
    days = flows.merge(days, row("2026-11-23"))
    assert [d["d"] for d in days][0] == "2026-11-23"
    many = [row("2025-%02d-%02d" % (1 + i // 28, 1 + i % 28)) for i in range(450)]
    assert len(flows.merge(many[:-1], many[-1])) == flows.KEEP_DAYS


def test_first_fetch_saves_a_history_file_and_the_page_gets_it(tmp_path):
    path = str(tmp_path / "flows.json")
    data, info = flows.refresh(Fake(REPORT), path, NOW)
    assert info == {"ok": True, "days": 1, "asof": "2026-11-25"}
    saved = json.loads((tmp_path / "flows.json").read_text())
    assert saved["days"][0]["fn"] == 4479.08 and saved["unit"] == "Rs crore" and saved["updated"].startswith("2026-11-26")
    page = flows.load(path)
    assert page["asof"] == "2026-11-25" and len(page["days"]) == 1


def test_a_bad_answer_keeps_the_saved_history(tmp_path):
    path = str(tmp_path / "flows.json")
    flows.refresh(Fake(REPORT), path, NOW)
    later = NOW + timedelta(hours=2)
    data, info = flows.refresh(Fake("<html>maintenance</html>"), path, later)
    assert info["ok"] is False and "kept previous data" in info["note"] and info["days"] == 1
    assert flows.load(path)["days"][0]["d"] == "2026-11-25"
    data, info = flows.refresh(Fake(ok=False, error="HTTP 403"), path, later)
    assert info["ok"] is False and "HTTP 403" in info["note"]


def test_nothing_is_saved_when_the_very_first_fetch_fails(tmp_path):
    path = tmp_path / "flows.json"
    data, info = flows.refresh(Fake(ok=False, error="HTTP 500"), str(path), NOW)
    assert data is None and info["ok"] is False and not path.exists() and flows.load(str(path)) is None


def test_the_report_is_asked_for_at_most_once_an_hour(tmp_path):
    path = str(tmp_path / "flows.json")
    flows.refresh(Fake(REPORT), path, NOW)
    quiet = Fake(REPORT)
    _, info = flows.refresh(quiet, path, NOW + timedelta(minutes=30))
    assert quiet.calls == 0 and info["skipped"] is True
    again = Fake(REPORT)
    flows.refresh(again, path, NOW + timedelta(minutes=61))
    assert again.calls == 1


def test_the_page_gets_only_the_newest_rows(tmp_path):
    path = tmp_path / "flows.json"
    days = [row("2026-%02d-%02d" % (1 + i // 28, 1 + i % 28)) for i in range(120)]
    path.write_text(json.dumps({"updated": NOW.isoformat(), "days": days}))
    page = flows.load(str(path))
    assert len(page["days"]) == flows.SHOW_DAYS and page["days"][-1] == days[-1]
