"""Market data: parsing, arithmetic, and the checks that keep bad data off the page."""
import json
import random
from datetime import date, datetime, timedelta

import pytest

from radar import market as m
from radar.http import FetchResult

NOW = datetime(2026, 11, 26, 18, 0, tzinfo=m.IST)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(m, "POLITE_DELAY", 0)
INDUSTRIES = ["Financial Services", "Healthcare", "Capital Goods", "Fast Moving Consumer Goods", "Power"]


def sessions(last: date, count: int) -> list[date]:
    out, d = [], last
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def universe(count=420):
    return [{"s": f"STK{i:03d}", "n": f"Stock {i} Ltd.", "i": INDUSTRIES[i % len(INDUSTRIES)]} for i in range(count)]


def flat(cal, price=100.0):
    return {d: price for d in cal}


def make_market(count=420, n=130, mutate=None):
    cal = sessions(date(2026, 11, 25), n)
    uni = universe(count)
    series = {row["s"]: flat(cal) for row in uni}
    nifty = {d: 25000.0 + i for i, d in enumerate(cal)}
    if mutate:
        mutate(series, cal)
    return uni, series, nifty, cal


# ----------------------------------------------------------------------------- universe and chart parsing

CSV_HEAD = "Company Name,Industry,Symbol,Series,ISIN Code\n"


def csv_text(count=450):
    rows = [f"Company {i} Ltd.,{INDUSTRIES[i % 5]},SYM{i:03d},EQ,INE{i:09d}" for i in range(count)]
    return CSV_HEAD + "\n".join(rows)


def test_universe_parses_dedupes_and_shortens():
    text = csv_text() + "\nDup Ltd.,Power,SYM001,EQ,INE0\nBond Ltd.,Power,BOND1,N1,INE1\nOdd Ltd.,Power,bad symbol!,EQ,INE2"
    rows = m.parse_universe(text)
    assert len(rows) == 450 and rows[0] == {"s": "SYM000", "n": "Company 0 Ltd.", "i": "Financial Services"}
    assert any(r["i"] == "FMCG" for r in rows)


def test_universe_rejects_wrong_columns_and_tiny_files():
    with pytest.raises(m.MarketError):
        m.parse_universe("Name,Sector\nA,B\n")
    with pytest.raises(m.MarketError):
        m.parse_universe(csv_text(50))


def chart_json(series: dict) -> str:
    stamps = [int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=m.IST).timestamp()) for d in sorted(series)]
    return json.dumps({"chart": {"result": [{"timestamp": stamps,
                                             "indicators": {"quote": [{"close": [series[d] for d in sorted(series)]}]}}]}})


def test_chart_parsing_skips_blanks_and_rejects_bad_shapes():
    cal = sessions(date(2026, 11, 25), 30)
    series = flat(cal)
    parsed = m.parse_chart(chart_json(series))
    assert len(parsed) == 30 and parsed[cal[-1]] == 100.0
    body = json.loads(chart_json(series))
    body["chart"]["result"][0]["indicators"]["quote"][0]["close"][3] = None
    assert len(m.parse_chart(json.dumps(body))) == 29
    body["chart"]["result"][0]["timestamp"].pop()
    with pytest.raises(ValueError):
        m.parse_chart(json.dumps(body))
    with pytest.raises(KeyError):
        m.parse_chart('{"chart": {"result": null, "error": {"code": "Not Found"}}}'.replace("null", "[{}]"))


# ----------------------------------------------------------------------------- arithmetic

def test_clusters_and_returns_are_exact():
    def mutate(series, cal):
        for i in range(0, 420, 5):  # every Financial Services stock: +10% on the last day, +20% over the week
            series[f"STK{i:03d}"][cal[-1]] = 110.0
            series[f"STK{i:03d}"][cal[-6]] = 91.6666667
    uni, series, nifty, cal = make_market(mutate=mutate)
    out = m.build_market(uni, series, nifty, NOW)
    assert out["asof"] == "2026-11-25" and out["priced"] == 420 and out["universe"] == 420
    fin = next(c for c in out["clusters"] if c["n"] == "Financial Services")
    assert fin["k"] == 84 and fin["med"]["1D"] == 10.0 and fin["med"]["1W"] == 20.0 and fin["adv"] == 1.0
    health = next(c for c in out["clusters"] if c["n"] == "Healthcare")
    assert health["med"]["1D"] == 0.0 and health["adv"] == 0.0
    assert out["clusters"][0]["med"]["1Y"] is None  # only 130 sessions of history, so no 1Y or 2Y yet
    stock = next(s for s in out["stocks"] if s[0] == "STK000")
    assert stock[3][0] == 10.0 and len(stock[3]) == len(m.HORIZON_KEYS)


def test_breadth_counts_and_thrust_ratio():
    def mutate(series, cal):
        for i in range(10):
            series[f"STK{i:03d}"][cal[-1]] = 105.0       # up 5% on the last session
        for i in range(10, 14):
            series[f"STK{i:03d}"][cal[-1]] = 94.0        # down 6%
    uni, series, nifty, cal = make_market(mutate=mutate)
    latest = m.build_market(uni, series, nifty, NOW)["breadth"][0]
    assert latest["d"] == "2026-11-25" and latest["up4"] == 10 and latest["dn4"] == 4
    assert latest["r5"] == 2.5 and latest["r10"] == 2.5
    assert latest["tr"] == 420 and latest["nf"] == nifty[cal[-1]]
    assert latest["a40"] is not None and 0 < latest["a40"] < 5   # the 10 risers sit above their 40-day average
    out = m.build_market(uni, series, nifty, NOW)["breadth"]
    assert len(out) == m.BREADTH_SESSIONS and out[1]["up4"] == 0 and out[1]["r5"] is None  # no decliners: no ratio


def test_quarter_month_and_34_day_counts():
    def mutate(series, cal):
        series["STK000"][cal[-1]] = 130.0                # +30% against every earlier flat close
        series["STK001"][cal[-1]] = 70.0
        series["STK002"][cal[-1]] = 115.0                # +15%: counts for 34 days, not for quarter or month
    uni, series, nifty, cal = make_market(mutate=mutate)
    b = m.build_market(uni, series, nifty, NOW)["breadth"][0]
    assert (b["uq"], b["dq"], b["um"], b["dm"], b["ut"], b["dt"]) == (1, 1, 1, 1, 2, 1)


def test_todays_unfinished_bar_is_ignored():
    uni, series, nifty, cal = make_market()
    today = date(2026, 11, 26)
    for s in series.values():
        s[today] = 101.0
    nifty[today] = 99999.0
    during = datetime(2026, 11, 26, 11, 0, tzinfo=m.IST)
    assert m.build_market(uni, series, nifty, during)["asof"] == "2026-11-25"
    assert m.build_market(uni, series, nifty, NOW)["asof"] == "2026-11-26"


# ----------------------------------------------------------------------------- checks

def test_outlier_and_stale_series_are_excluded_and_reported():
    def mutate(series, cal):
        series["STK000"][cal[-1]] = 180.0                 # +80% in a day: more likely an unadjusted split
        del series["STK001"][cal[-1]]                     # no close on the latest session
        del series["STK002"]                              # no data at all
    uni, series, nifty, cal = make_market(mutate=mutate)
    out = m.build_market(uni, series, nifty, NOW)
    assert out["priced"] == 417 and out["dropped"] == {"no_data": 1, "stale": 1, "short": 0, "outlier": ["STK000"]}
    assert all(s[0] not in ("STK000", "STK001", "STK002") for s in out["stocks"])


def test_too_little_coverage_or_history_raises():
    def mutate(series, cal):
        for i in range(100):
            del series[f"STK{i:03d}"]
    uni, series, nifty, _ = make_market(mutate=mutate)
    with pytest.raises(m.MarketError, match="fresh close"):
        m.build_market(uni, series, nifty, NOW)
    uni, series, nifty, cal = make_market(n=130)
    with pytest.raises(m.MarketError, match="sessions"):
        m.build_market(uni, series, {d: 1.0 for d in cal[-50:]}, NOW)


def test_old_data_is_rejected():
    uni, series, nifty, _ = make_market()
    with pytest.raises(m.MarketError, match="days old"):
        m.build_market(uni, series, nifty, datetime(2026, 12, 20, 18, 0, tzinfo=m.IST))


def test_validate_blocks_regressions_and_impossible_numbers():
    uni, series, nifty, _ = make_market()
    good = m.build_market(uni, series, nifty, NOW)
    m.validate(good, None)
    m.validate(good, {"asof": "2026-11-24"})
    with pytest.raises(m.MarketError, match="older"):
        m.validate(good, {"asof": "2026-11-26"})
    bad = json.loads(json.dumps(good))
    bad["stocks"][0][3][0] = -150.0
    with pytest.raises(m.MarketError, match="impossible"):
        m.validate(bad, None)


# ----------------------------------------------------------------------------- the whole job, with a fake network

class FakeNet:
    def __init__(self, uni_csv, charts, universe_ok=True, status=200):
        self.uni_csv, self.charts, self.universe_ok, self.status = uni_csv, charts, universe_ok, status
        self.calls = 0

    def get(self, name, url):
        self.calls += 1
        if name == "universe":
            return FetchResult(True, 200, self.uni_csv, "") if self.universe_ok else FetchResult(False, 403, "", "HTTP 403")
        if self.status != 200:
            return FetchResult(False, self.status, "", f"HTTP {self.status}")
        symbol = url.split("/chart/")[1].split("?")[0].replace("%5E", "^")
        body = self.charts.get(symbol)
        return FetchResult(True, 200, body, "") if body else FetchResult(False, 404, "", "HTTP 404")


def net_for(count=420, n=130, **kw):
    uni, series, nifty, cal = make_market(count, n)
    charts = {row["s"] + ".NS": chart_json(series[row["s"]]) for row in uni}  # Yahoo only knows NSE stocks as SYMBOL.NS
    charts["^NSEI"] = chart_json(nifty)
    text = CSV_HEAD + "\n".join(f"{r['n']},{r['i']},{r['s']},EQ,INE{ix:09d}" for ix, r in enumerate(uni))
    return FakeNet(text, charts, **kw)


def test_job_saves_good_data_and_keeps_it_when_the_next_run_fails(tmp_path):
    mp, up = str(tmp_path / "market.json"), str(tmp_path / "universe.json")
    payload, info = m.refresh(lambda: net_for(), mp, up, NOW)
    assert info["ok"] and payload["priced"] == 420 and m.load(mp)["asof"] == "2026-11-25"
    before = (tmp_path / "market.json").read_text()
    # NSE and Yahoo both refuse: the last good file stays, the universe comes from the cache, and the run says so
    payload2, info2 = m.refresh(lambda: net_for(universe_ok=False, status=429), mp, up, NOW)
    assert not info2["ok"] and "kept previous data" in info2["note"] and payload2["asof"] == "2026-11-25"
    assert (tmp_path / "market.json").read_text() == before


def test_job_stops_early_when_the_data_site_is_blocking(tmp_path):
    net = net_for(status=429)
    _, info = m.refresh(lambda: net, str(tmp_path / "market.json"), str(tmp_path / "universe.json"), NOW)
    assert not info["ok"] and net.calls < 100  # it gave up instead of hammering 420 requests
    assert not (tmp_path / "market.json").exists()


def test_no_universe_and_no_cache_is_a_clean_failure(tmp_path):
    _, info = m.refresh(lambda: net_for(universe_ok=False), str(tmp_path / "m.json"), str(tmp_path / "u.json"), NOW)
    assert not info["ok"] and "universe unavailable" in info["note"]


def test_yahoo_symbols_get_the_nse_suffix_but_indexes_do_not():
    assert m.yahoo_symbol("RELIANCE") == "RELIANCE.NS"
    assert m.yahoo_symbol("M&M") == "M&M.NS"
    assert m.yahoo_symbol("^NSEI") == "^NSEI"


def test_a_site_that_answers_404_for_everything_leaves_examples_to_diagnose(tmp_path):
    net = net_for()
    net.charts = {"^NSEI": net.charts["^NSEI"]}  # every stock 404s, only the index answers
    _, info = m.refresh(lambda: net, str(tmp_path / "m.json"), str(tmp_path / "u.json"), NOW)
    assert not info["ok"] and info["fetch_failures"] == {"HTTP 404": 420}
    assert len(info["failure_examples"]) == 5 and info["failure_examples"][0].endswith("HTTP 404")
    assert info["nifty_last_sessions"][-1] == "2026-11-25"
