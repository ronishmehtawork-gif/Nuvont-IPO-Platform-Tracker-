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


def chart_json(series: dict, volume=1_000_000) -> str:
    days = sorted(series)
    stamps = [int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=m.IST).timestamp()) for d in days]
    quote = {"close": [series[d] for d in days]}
    if volume is not None:
        quote["volume"] = [volume(d) if callable(volume) else volume for d in days]
    return json.dumps({"chart": {"result": [{"timestamp": stamps, "indicators": {"quote": [quote]}}]}})


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
    out = m.build_market(uni, series, NOW)
    assert out["asof"] == "2026-11-25" and out["priced"] == 420 and out["universe"] == 420
    fin = next(c for c in out["clusters"] if c["n"] == "Financial Services")
    assert fin["k"] == 84 and fin["med"]["1D"] == 10.0 and fin["med"]["1W"] == 20.0 and fin["adv"] == 1.0
    health = next(c for c in out["clusters"] if c["n"] == "Healthcare")
    assert health["med"]["1D"] == 0.0 and health["adv"] == 0.0
    assert out["clusters"][0]["med"]["1Y"] is None  # only 130 sessions of history, so no 1Y or 2Y yet
    stock = next(s for s in out["stocks"] if s[0] == "STK000")
    assert stock[3][0] == 10.0 and len(stock[3]) == len(m.HORIZON_KEYS)


def test_todays_unfinished_bar_is_ignored():
    uni, series, nifty, cal = make_market()
    today = date(2026, 11, 26)
    for s in series.values():
        s[today] = 101.0
    nifty[today] = 99999.0
    during = datetime(2026, 11, 26, 11, 0, tzinfo=m.IST)
    assert m.build_market(uni, series, during)["asof"] == "2026-11-25"
    assert m.build_market(uni, series, NOW)["asof"] == "2026-11-26"


# ----------------------------------------------------------------------------- checks

def test_outlier_and_stale_series_are_excluded_and_reported():
    def mutate(series, cal):
        series["STK000"][cal[-1]] = 180.0                 # +80% in a day: more likely an unadjusted split
        del series["STK001"][cal[-1]]                     # no close on the latest session
        del series["STK002"]                              # no data at all
    uni, series, nifty, cal = make_market(mutate=mutate)
    out = m.build_market(uni, series, NOW)
    assert out["priced"] == 417 and out["dropped"] == {"no_data": 1, "stale": 1, "short": 0, "outlier": ["STK000"]}
    assert all(s[0] not in ("STK000", "STK001", "STK002") for s in out["stocks"])


def test_too_little_coverage_or_history_raises():
    def mutate(series, cal):
        for i in range(100):
            del series[f"STK{i:03d}"]
    uni, series, nifty, _ = make_market(mutate=mutate)
    with pytest.raises(m.MarketError, match="fresh close"):
        m.build_market(uni, series, NOW)
    uni, series, nifty, _ = make_market(n=60)  # under 100 sessions of history
    with pytest.raises(m.MarketError, match="sessions"):
        m.build_market(uni, series, NOW)


def test_old_data_is_rejected():
    uni, series, nifty, _ = make_market()
    with pytest.raises(m.MarketError, match="days old"):
        m.build_market(uni, series, datetime(2026, 12, 20, 18, 0, tzinfo=m.IST))


def test_validate_blocks_regressions_and_impossible_numbers():
    uni, series, nifty, _ = make_market()
    good = m.build_market(uni, series, NOW)
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
    def __init__(self, uni_csv, charts, fno_csv="", universe_ok=True, status=200):
        self.uni_csv, self.charts, self.fno_csv, self.universe_ok, self.status = uni_csv, charts, fno_csv, universe_ok, status
        self.calls = 0

    def get(self, name, url):
        self.calls += 1
        if name == "universe":
            return FetchResult(True, 200, self.uni_csv, "") if self.universe_ok else FetchResult(False, 403, "", "HTTP 403")
        if name == "fno":
            return FetchResult(True, 200, self.fno_csv, "") if self.fno_csv else FetchResult(False, 404, "", "HTTP 404")
        if self.status != 200:
            return FetchResult(False, self.status, "", f"HTTP {self.status}")
        symbol = url.split("/chart/")[1].split("?")[0]
        body = self.charts.get(symbol)
        return FetchResult(True, 200, body, "") if body else FetchResult(False, 404, "", "HTTP 404")


FNO_HEAD = ("UNDERLYING                          ,SYMBOL    ,DEC-26     ,JAN-27     \n"
            "NIFTY 50                            ,NIFTY     ,65         ,65         \n"
            "NIFTY BANK                          ,BANKNIFTY ,30         ,30         \n"
            "Derivatives on Individual Securities,Symbol    ,DEC-26     ,JAN-27     \n")


def fno_text(count=150):
    return FNO_HEAD + "\n".join(f"STOCK {i}                  ,STK{i:03d}    ,500        ,500        " for i in range(count))


def net_for(count=420, n=130, fno=150, **kw):
    uni, series, nifty, cal = make_market(count, n)
    charts = {row["s"] + ".NS": chart_json(series[row["s"]]) for row in uni}  # Yahoo only knows NSE stocks as SYMBOL.NS
    text = CSV_HEAD + "\n".join(f"{r['n']},{r['i']},{r['s']},EQ,INE{ix:09d}" for ix, r in enumerate(uni))
    return FakeNet(text, charts, fno_text(fno) if fno else "", **kw)


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
    net.charts = {}  # every stock 404s
    _, info = m.refresh(lambda: net, str(tmp_path / "m.json"), str(tmp_path / "u.json"), NOW)
    assert not info["ok"] and info["fetch_failures"] == {"HTTP 404": 420}
    assert len(info["failure_examples"]) == 5 and info["failure_examples"][0].endswith("HTTP 404")


def test_a_real_twenty_fold_runner_stays_but_a_fifty_fold_fault_is_left_out_without_failing_the_job():
    def mutate(series, cal):
        series["STK001"] = {d: 10.0 * (1 + i / (len(cal) - 1) * 20) for i, d in enumerate(cal)}   # 21x, smooth
        series["STK002"] = {d: 10.0 * (1 + i / (len(cal) - 1) * 70) for i, d in enumerate(cal)}   # 71x, smooth
    uni, series, nifty, cal = make_market(mutate=mutate)
    payload = m.build_market(uni, series, NOW)
    names = {s[0] for s in payload["stocks"]}
    assert "STK001" in names and "STK002" not in names and "STK002" in payload["dropped"]["outlier"]
    m.validate(payload, None)  # no job-level failure over one stock


def test_an_unadjusted_split_long_ago_is_caught_across_the_whole_two_year_window():
    def mutate(series, cal):
        cut = len(cal) - 400
        series["STK003"] = {d: (500.0 if i < cut else 100.0) for i, d in enumerate(cal)}  # 5:1 split never adjusted
    uni, series, nifty, cal = make_market(n=520, mutate=mutate)
    payload = m.build_market(uni, series, NOW)
    assert "STK003" in payload["dropped"]["outlier"]


def test_nse_placeholder_rows_are_not_treated_as_stocks():
    text = csv_text() + "\nDummy HEG Ltd.,Capital Goods,DUMMYHEG,EQ,INE0000000"
    assert all(not r["s"].startswith("DUMMY") for r in m.parse_universe(text))


# ----------------------------------------------------------------------------- sector flow

def test_fno_list_parses_after_the_sub_header_and_rejects_broken_files():
    syms = m.parse_fno(fno_text(150))
    assert len(syms) == 150 and syms[0] == "STK000" and "NIFTY" not in syms and "BANKNIFTY" not in syms
    with pytest.raises(m.MarketError, match="F&O"):
        m.parse_fno(fno_text(20))
    with pytest.raises(m.MarketError, match="F&O"):
        m.parse_fno("UNDERLYING,SYMBOL\nNIFTY 50,NIFTY\n")  # no individual-securities section at all


def flow_market(mutate=None, fno=150):
    def volumes(series):
        return {s: {d: 1_000_000.0 for d in closes} for s, closes in series.items()}
    uni, series, nifty, cal = make_market()
    if mutate:
        mutate(series, cal)
    return uni, series, volumes(series), [f"STK{i:03d}" for i in range(fno)], cal


def test_flow_counts_turnover_as_inflow_on_up_days_and_outflow_on_down_days():
    def mutate(series, cal):
        for i in range(0, 150, 5):                       # Financial Services: up 10% on the last day
            series[f"STK{i:03d}"][cal[-1]] = 110.0
        for i in range(1, 150, 5):                       # Healthcare: down 10% on the last day
            series[f"STK{i:03d}"][cal[-1]] = 90.0
    uni, series, vols, fno, cal = flow_market(mutate)
    flow = m.build_market(uni, series, NOW, vols, fno)["flow"]
    assert flow["asof"] == "2026-11-25" and flow["count"] == 150 and flow["listed"] == 150 and len(flow["days"]) == m.FLOW_SESSIONS
    fin = next(s for s in flow["sectors"] if s["n"] == "Financial Services")
    # 30 stocks x 110 rupees x 1,000,000 shares = 330 crore on the last day, all of it inflow
    assert fin["k"] == 30 and fin["t1"] == 330.0 and fin["n1"] == 330.0 and fin["s1"] == 100.0 and fin["adv"] == 30
    hc = next(s for s in flow["sectors"] if s["n"] == "Healthcare")
    assert hc["n1"] == -270.0 and hc["s1"] == -100.0 and hc["dec"] == 30
    assert fin["sp"][-1] == 330.0 and len(fin["sp"]) == m.FLOW_SESSIONS
    # flat days carry turnover but no direction: 5 days = 4 flat (30 x 100 x 1,000,000 = 300 crore each) + 1 big move
    assert fin["t5"] == 330.0 + 4 * 300.0 and fin["n5"] == 330.0
    stock = next(r for r in flow["stocks"] if r[0] == "STK000")
    assert stock[2] == "Financial Services" and stock[3] == 10.0 and stock[5] == 11.0


def test_flow_is_left_out_when_volumes_are_missing_or_absurd_and_never_blocks_the_heatmap():
    uni, series, vols, fno, cal = flow_market()
    assert m.build_market(uni, series, NOW, {}, fno)["flow"] is None          # no volume data at all
    assert m.build_market(uni, series, NOW, vols, [])["flow"] is None         # no F&O list
    for sym in fno[:10]:
        vols[sym] = {d: 5e12 for d in vols[sym]}                              # 50,000 crore a day: a bad feed
    flow = m.build_market(uni, series, NOW, vols, fno)["flow"]
    assert flow["count"] == 140
    out = m.build_market(uni, series, NOW, vols, fno)
    assert out["priced"] == 420 and out["clusters"]


def test_job_builds_flow_from_the_fno_list_and_survives_a_missing_list(tmp_path):
    mp, up = str(tmp_path / "market.json"), str(tmp_path / "universe.json")
    payload, info = m.refresh(lambda: net_for(), mp, up, NOW)
    assert info["ok"] and payload["flow"]["count"] == 150 and info["flow"] == {"stocks": 150, "listed": 150}
    assert (tmp_path / "fno.json").exists()
    other = tmp_path / "fresh"
    payload2, info2 = m.refresh(lambda: net_for(fno=0), str(other / "m.json"), str(other / "u.json"), NOW)
    assert info2["ok"] and payload2["flow"] is None and "F&O list unavailable" in info2["fno_note"]
