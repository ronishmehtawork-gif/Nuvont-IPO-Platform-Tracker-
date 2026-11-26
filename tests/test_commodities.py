"""Commodity data: parsing, arithmetic and the checks that keep bad numbers off the page."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from radar import commodities as c
from radar.http import FetchResult

NOW = datetime(2026, 11, 26, 18, 0, tzinfo=c.IST)


def days(last=date(2026, 11, 25), count=300):
    out, d = [], last
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def ramp(start=100.0, step=0.1, count=300, last=date(2026, 11, 25)):
    return {d: round(start + i * step, 4) for i, d in enumerate(days(last, count))}


def chart(closes: dict, offset=-18000) -> str:
    ds = sorted(closes)
    stamps = [int(datetime(d.year, d.month, d.day, 0, 0, tzinfo=timezone.utc).timestamp()) - offset for d in ds]
    return json.dumps({"chart": {"result": [{"meta": {"gmtoffset": offset, "regularMarketTime": stamps[-1]},
                                             "timestamp": stamps, "indicators": {"quote": [{"close": [closes[d] for d in ds]}]}}]}})


def test_parsing_uses_the_exchange_timezone_for_the_session_date():
    closes = {date(2026, 11, 24): 10.0, date(2026, 11, 25): 11.0}
    parsed, last = c.parse_chart(chart(closes))
    assert parsed == closes and last is not None
    body = json.loads(chart(closes))
    body["chart"]["result"][0]["indicators"]["quote"][0]["close"][0] = None
    assert len(c.parse_chart(json.dumps(body))[0]) == 1
    body["chart"]["result"][0]["timestamp"].pop()
    with pytest.raises(ValueError):
        c.parse_chart(json.dumps(body))


def test_changes_range_and_sparkline_are_exact():
    closes = ramp()  # +0.1 a session
    out = c.summarize("HG=F", closes, NOW)
    last = max(closes)
    assert out["p"] == closes[last] and out["d"] == "2026-11-25" and len(out["sp"]) == c.SPARK_POINTS
    prev = sorted(closes)[-2]
    assert out["ch"]["1D"] == round((closes[last] / closes[prev] - 1) * 100, 2)
    week = max(d for d in closes if d <= last - timedelta(days=7))
    assert out["ch"]["1W"] == round((closes[last] / closes[week] - 1) * 100, 2)
    assert out["ch"]["YTD"] == round((closes[last] / closes[date(2025, 12, 31)] - 1) * 100, 2)
    assert out["hi"] == closes[last] and out["pos"] == 100.0 and out["lo"] < out["hi"]


def test_history_too_short_for_a_horizon_gives_a_blank_not_a_guess():
    out = c.summarize("HG=F", ramp(count=80), NOW)
    assert out["ch"]["1D"] is not None and out["ch"]["1W"] is not None and out["ch"]["1Y"] is None and out["ch"]["YTD"] is None


def test_yields_change_in_points_not_per_cent():
    closes = ramp(4.0, 0.001)
    out = c.summarize("^TNX", closes, NOW, absolute=True)
    last = max(closes)
    assert out["ch"]["1D"] == round(closes[last] - closes[sorted(closes)[-2]], 3)


def test_bad_series_are_refused_with_a_reason():
    with pytest.raises(c.CommodityError, match="bars"):
        c.summarize("HG=F", ramp(count=30), NOW)
    with pytest.raises(c.CommodityError, match="last bar"):
        c.summarize("HG=F", ramp(last=date(2026, 11, 10)), NOW)
    glitch = ramp()
    glitch[max(glitch)] = glitch[sorted(glitch)[-2]] * 2.5
    with pytest.raises(c.CommodityError, match="in one day"):
        c.summarize("HG=F", glitch, NOW)
    # a big jump months ago (a contract roll or a real spike) is history, not a fault
    old = ramp()
    day = sorted(old)[-120]
    for d in old:
        if d >= day:
            old[d] *= 0.5
    assert c.summarize("NG=F", old, NOW)["p"] > 0
    with pytest.raises(c.CommodityError, match="plausible"):
        c.summarize("GC=F", ramp(start=1.0, step=0.0001), NOW)  # gold at one dollar


def test_india_parity_converts_ounces_to_grams_and_dollars_to_rupees():
    items = {"GC=F": {"p": 3110.34768}, "SI=F": {"p": 31.1034768}, "INR=X": {"p": 90.0}}
    out = c.parity(items)
    assert out["gold10g"] == 90_000 and out["silverkg"] == 90_000 and out["ratio"] == 100.0
    assert c.parity({"GC=F": {"p": 1.0}}) is None


# ----------------------------------------------------------------------------- the whole job, with a fake network

class FakeNet:
    def __init__(self, charts, status=200):
        self.charts, self.status, self.calls = charts, status, 0

    def get(self, name, url):
        self.calls += 1
        if self.status != 200:
            return FetchResult(False, self.status, "", f"HTTP {self.status}")
        from urllib.parse import unquote
        body = self.charts.get(unquote(url.split("/chart/")[1].split("?")[0]))
        return FetchResult(True, 200, body, "") if body else FetchResult(False, 404, "", "HTTP 404")


def all_charts(skip=()):
    symbols = [s for _, items in c.GROUPS for _, s, _ in items] + [s for _, s, _, _ in c.CONTEXT]
    out = {}
    for i, s in enumerate(symbols):
        if s in skip:
            continue
        start = {"GC=F": 2000.0, "SI=F": 25.0, "INR=X": 83.0, "CL=F": 70.0, "BZ=F": 74.0}.get(s, 50.0)
        out[s] = chart(ramp(start, start * 0.0005))
    return out


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(c, "POLITE_DELAY", 0)


def test_job_saves_groups_context_and_parity_and_survives_partial_failures(tmp_path):
    path = str(tmp_path / "commodities.json")
    payload, info = c.refresh(lambda: FakeNet(all_charts(skip={"ALI=F", "^INDIAVIX"})), path, NOW)
    assert info["ok"] and "ALI=F" in info["not_fetched"] and payload["asof"] == "2026-11-25"
    names = [i["n"] for g in payload["groups"] for i in g["items"]]
    assert "Gold" in names and "Aluminium" not in names and payload["parity"]["gold10g"] > 0
    assert [x["n"] for x in payload["context"]] == ["USD/INR", "US dollar index", "US 10-year yield"]
    assert c.load(path)["asof"] == "2026-11-25"


def test_the_payload_says_whether_futures_are_trading_so_the_page_can_show_live(tmp_path):
    from datetime import timedelta
    weekday = NOW.replace(hour=14, minute=0)
    payload, _ = c.refresh(lambda: FakeNet(all_charts()), str(tmp_path / "a.json"), weekday)
    assert payload["live"] is (weekday.weekday() < 5)
    saturday = weekday + timedelta(days=(5 - weekday.weekday()) % 7 or 7)
    saturday = saturday.replace(hour=12)
    payload, _ = c.refresh(lambda: FakeNet(all_charts()), str(tmp_path / "b.json"), saturday)
    assert payload["live"] is False


def test_job_keeps_the_last_good_file_when_the_site_fails(tmp_path):
    path = str(tmp_path / "commodities.json")
    good, _ = c.refresh(lambda: FakeNet(all_charts()), path, NOW)
    saved = (tmp_path / "commodities.json").read_text()
    down, info = c.refresh(lambda: FakeNet({}, status=429), path, NOW)
    assert not info["ok"] and "kept previous data" in info["note"] and down == good
    assert (tmp_path / "commodities.json").read_text() == saved
    first, info2 = c.refresh(lambda: FakeNet({}, status=429), str(tmp_path / "none.json"), NOW)
    assert first is None and not info2["ok"]


def test_older_data_never_replaces_newer_data(tmp_path):
    path = str(tmp_path / "commodities.json")
    c.refresh(lambda: FakeNet(all_charts()), path, NOW)
    older = {s: chart(ramp(50.0, 0.01, last=date(2026, 11, 24))) for s in all_charts()}
    _, info = c.refresh(lambda: FakeNet(older), path, NOW)
    assert not info["ok"] and "older" in info["note"]
