"""Stock-level market data for the dashboard: sector heatmap and market breadth.

Universe: the Nifty 500 constituent list published by NSE (company, industry, symbol).
Prices:   daily closes for each stock and for the Nifty 50, from Yahoo Finance's public chart JSON.
Output:   data/market.json, written only when the new data passes every check below. A bad run keeps the last
          good file, so the page never shows half-fetched numbers.

Nothing here is investment advice: it is arithmetic on published closing prices."""
from __future__ import annotations

import csv
import io
import json
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .http import Fetcher

IST = timezone(timedelta(hours=5, minutes=30))

UNIVERSE_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=3y&interval=1d"
NIFTY_SYMBOL = "^NSEI"

# Trading sessions back from the latest one. Two years is 504 sessions, so the 2Y column needs three years of history.
HORIZONS = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252), ("2Y", 504)]
HORIZON_KEYS = [k for k, _ in HORIZONS] + ["YTD"]

MIN_UNIVERSE = 400           # NSE publishes 500; far fewer means a broken file
MIN_COVERAGE = 0.85          # share of the universe that must have a fresh close
MAX_DAILY_MOVE = 0.50        # a bigger one-day move in the history is treated as an unadjusted corporate action
MAX_RETURN = 5000.0        # percent. A 50x move on any horizon is far more likely a data fault than a result
MIN_SESSION_SHARE = 0.6    # a date counts as a trading session when this share of the stocks have a close on it
JUMP_WINDOW = 505          # sessions checked for unadjusted splits: the whole 2Y horizon plus one
MAX_STALE_DAYS = 7           # the latest session may not be older than this
BREADTH_SESSIONS = 20
WORKERS = 6
POLITE_DELAY = 0.12          # seconds each worker rests between requests
BUDGET_SECONDS = 480         # stop fetching after this long and judge what we have
BLOCK_LIMIT = 0.30           # stop early if this share of the first requests are refused (never push through a block)

# NSE industry names, shortened where the long form is unwieldy.
SHORT_INDUSTRY = {
    "Fast Moving Consumer Goods": "FMCG",
    "Automobile and Auto Components": "Automobiles & Components",
    "Oil Gas & Consumable Fuels": "Oil, Gas & Fuels",
    "Media Entertainment & Publication": "Media & Entertainment",
    "Metals & Mining": "Metals & Mining",
}


class MarketError(Exception):
    """The new data failed a check; the previous file stays in place."""


# ----------------------------------------------------------------------------- universe

def parse_universe(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    heads = {(h or "").strip() for h in (reader.fieldnames or [])}
    if not {"Company Name", "Industry", "Symbol"} <= heads:
        raise MarketError(f"universe file has unexpected columns: {sorted(heads)[:8]}")
    rows, seen = [], set()
    for raw in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        symbol = row.get("Symbol", "").upper()
        if row.get("Series", "EQ").upper() != "EQ" or not re.fullmatch(r"[A-Z0-9&\-]{1,20}", symbol) or symbol in seen:
            continue
        if symbol.startswith("DUMMY") or row.get("Company Name", "").lower().startswith("dummy "):
            continue  # NSE placeholder rows for pending corporate actions; they do not trade
        industry = row.get("Industry", "") or "Unclassified"
        seen.add(symbol)
        rows.append({"s": symbol, "n": row.get("Company Name", "") or symbol,
                     "i": SHORT_INDUSTRY.get(industry, industry)})
    if len(rows) < MIN_UNIVERSE:
        raise MarketError(f"universe has only {len(rows)} stocks (expected about 500)")
    return rows


def load_universe(fetcher: Fetcher, cache_path: str, today: date) -> tuple[list[dict], str]:
    """Fresh list when NSE answers, otherwise the last good copy. Returns (rows, note)."""
    errors = []
    for url in UNIVERSE_URLS:
        res = fetcher.get("universe", url)
        if not res.ok:
            errors.append(f"{url.split('/')[2]}: {res.error or 'no response'}")
            continue
        try:
            rows = parse_universe(res.text)
        except MarketError as e:
            errors.append(f"{url.split('/')[2]}: {e}")
            continue
        _write_json(cache_path, {"fetched": today.isoformat(), "rows": rows})
        return rows, f"universe fetched from {url.split('/')[2]}"
    cached = _read_json(cache_path)
    if cached and len(cached.get("rows", [])) >= MIN_UNIVERSE:
        return cached["rows"], f"universe from cache dated {cached.get('fetched', '?')} ({'; '.join(errors)})"
    raise MarketError("universe unavailable: " + "; ".join(errors))


# ----------------------------------------------------------------------------- prices

def parse_chart(text: str) -> dict[date, float]:
    """Yahoo chart JSON to {session date: close}. Raises ValueError when the shape is wrong."""
    data = json.loads(text)
    result = data["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    if len(stamps) != len(closes):
        raise ValueError("timestamp and close lengths differ")
    out: dict[date, float] = {}
    for stamp, close in zip(stamps, closes):
        if not isinstance(close, (int, float)) or close != close or close <= 0:
            continue
        out[datetime.fromtimestamp(stamp, IST).date()] = float(close)  # one bar per date; the last one wins
    return out


def yahoo_symbol(symbol: str) -> str:
    """NSE equities are listed on Yahoo with a .NS suffix; index symbols (^NSEI) are used as they are."""
    return symbol if symbol.startswith("^") else symbol + ".NS"


def fetch_series(symbols: list[str], make_fetcher, deadline: float) -> tuple[dict[str, dict], dict[str, int], list[str]]:
    """Fetch every symbol politely. Returns ({symbol: {date: close}}, {reason: count}, a few example failures)."""
    series: dict[str, dict] = {}
    failures: dict[str, int] = {}
    samples: list[str] = []  # a few "SYMBOL: reason" lines, so a bad run can be diagnosed from the public report
    lock = threading.Lock()
    local = threading.local()
    state = {"done": 0, "blocked": 0, "stop": False}

    def work(symbol: str) -> None:
        if state["stop"] or time.monotonic() > deadline:
            with lock:
                failures["skipped"] = failures.get("skipped", 0) + 1
            return
        if not hasattr(local, "fetcher"):
            local.fetcher = make_fetcher()
        res = local.fetcher.get("chart", CHART_URL.format(symbol=quote(yahoo_symbol(symbol), safe="")))
        reason = ""
        if res.ok:
            try:
                parsed = parse_chart(res.text)
                if len(parsed) < 20:
                    reason = "too little history"
                else:
                    with lock:
                        series[symbol] = parsed
            except (ValueError, KeyError, IndexError, TypeError):
                reason = "unreadable response"
        else:
            reason = res.error or "no response"
        with lock:
            state["done"] += 1
            if reason:
                failures[reason] = failures.get(reason, 0) + 1
                if len(samples) < 5:
                    samples.append(f"{symbol}: {reason}")
                if reason in ("HTTP 429", "HTTP 403"):
                    state["blocked"] += 1
            if state["done"] >= 30 and state["blocked"] / state["done"] > BLOCK_LIMIT:
                state["stop"] = True
        time.sleep(POLITE_DELAY)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(work, symbols))
    return series, failures, samples


# ----------------------------------------------------------------------------- maths

def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old <= 0:
        return None
    return round((new / old - 1) * 100, 2)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def session_calendar(series: dict[str, dict]) -> list[date]:
    """Trading sessions as the stocks themselves show them. The index feed can lag a day or two behind."""
    counts: dict[date, int] = {}
    for closes in series.values():
        for d in closes:
            counts[d] = counts.get(d, 0) + 1
    need = MIN_SESSION_SHARE * len(series)
    return sorted(d for d, c in counts.items() if c >= need)


def build_market(universe: list[dict], series: dict[str, dict], nifty: dict[date, float], now: datetime) -> dict:
    """All the arithmetic. Pure function: same inputs, same output."""
    calendar = session_calendar(series)
    today = now.astimezone(IST).date()
    if calendar and calendar[-1] == today and now.astimezone(IST).time() < dtime(15, 45):
        calendar = calendar[:-1]  # today's bar is still forming; use the last finished session
    if len(calendar) < 100:
        raise MarketError(f"only {len(calendar)} trading sessions found in the stock prices")
    asof = calendar[-1]
    if (today - asof).days > MAX_STALE_DAYS:
        raise MarketError(f"latest session {asof} is more than {MAX_STALE_DAYS} days old")
    n = len(calendar)

    # Aligned close arrays, with the checks that keep bad series out of every statistic.
    dropped = {"no_data": 0, "stale": 0, "outlier": [], "short": 0}
    grid: dict[str, list[float | None]] = {}
    meta = {row["s"]: row for row in universe}
    for symbol, row in meta.items():
        closes = series.get(symbol)
        if not closes:
            dropped["no_data"] += 1
            continue
        arr = [closes.get(d) for d in calendar]
        if arr[-1] is None:
            dropped["stale"] += 1
            continue
        window = [v for v in arr[-JUMP_WINDOW:] if v is not None]
        jumps = [abs(b / a - 1) for a, b in zip(window, window[1:])]
        if jumps and max(jumps) > MAX_DAILY_MOVE:
            dropped["outlier"].append(symbol)
            continue
        if sum(v is not None for v in arr) < 30:
            dropped["short"] += 1
            continue
        grid[symbol] = arr
    if len(grid) < MIN_COVERAGE * len(universe):
        raise MarketError(f"only {len(grid)} of {len(universe)} stocks have a fresh close on {asof}")

    prev_year_idx = max((i for i, d in enumerate(calendar) if d.year < asof.year), default=None)

    def returns(arr: list[float | None]) -> list[float | None]:
        out = []
        for _, days in HORIZONS:
            i = n - 1 - days
            out.append(_pct(arr[-1], arr[i]) if i >= 0 else None)
        out.append(_pct(arr[-1], arr[prev_year_idx]) if prev_year_idx is not None else None)  # YTD
        return out

    industries = sorted({meta[s]["i"] for s in grid})
    index_of = {name: i for i, name in enumerate(industries)}
    stocks = []
    for symbol, arr in list(grid.items()):
        rets = returns(arr)
        if any(v is not None and not -99.5 <= v <= MAX_RETURN for v in rets):
            dropped["outlier"].append(symbol)  # almost certainly an unadjusted corporate action
            del grid[symbol]
            continue
        stocks.append([symbol, meta[symbol]["n"], index_of[meta[symbol]["i"]], rets])
    stocks.sort(key=lambda s: (s[2], s[0]))

    clusters = []
    for name, ci in index_of.items():
        members = [s for s in stocks if s[2] == ci]
        med = {}
        for hi, key in enumerate(HORIZON_KEYS):
            med[key] = _median([s[3][hi] for s in members if s[3][hi] is not None])
        day = [s[3][0] for s in members if s[3][0] is not None]
        clusters.append({"n": name, "k": len(members), "med": med,
                         "adv": round(sum(1 for v in day if v > 0) / len(day), 3) if day else None})

    # Breadth: needs the daily move for BREADTH_SESSIONS + 9 sessions so the 10-day thrust has a full window.
    span = BREADTH_SESSIONS + 9
    up4, dn4 = {}, {}
    for j in range(n - span, n):
        u = d = 0
        for arr in grid.values():
            if arr[j] is not None and arr[j - 1] is not None:
                move = arr[j] / arr[j - 1] - 1
                u += move >= 0.04
                d += move <= -0.04
        up4[j], dn4[j] = u, d

    def ratio(j: int, window: int) -> float | None:
        down = sum(dn4[k] for k in range(j - window + 1, j + 1))
        return round(sum(up4[k] for k in range(j - window + 1, j + 1)) / down, 2) if down else None

    def move_counts(j: int, back: int, limit: float) -> tuple[int, int]:
        u = d = 0
        if j - back < 0:
            return 0, 0
        for arr in grid.values():
            if arr[j] is not None and arr[j - back] is not None:
                change = arr[j] / arr[j - back] - 1
                u += change >= limit
                d += change <= -limit
        return u, d

    sessions = []
    for j in range(n - 1, n - 1 - BREADTH_SESSIONS, -1):
        tracked = sum(arr[j] is not None for arr in grid.values())
        above = total = 0
        for arr in grid.values():
            win = [v for v in arr[j - 39:j + 1] if v is not None] if j >= 39 else []
            if arr[j] is not None and len(win) >= 30:
                total += 1
                above += arr[j] > sum(win) / len(win)
        uq, dq = move_counts(j, 65, 0.25)
        um, dm = move_counts(j, 20, 0.25)
        ut, dt = move_counts(j, 34, 0.13)
        sessions.append({
            "d": calendar[j].isoformat(), "up4": up4[j], "dn4": dn4[j], "r5": ratio(j, 5), "r10": ratio(j, 10),
            "uq": uq, "dq": dq, "um": um, "dm": dm, "ut": ut, "dt": dt, "tr": tracked,
            "a40": round(above / total * 100, 1) if total else None,
            "nf": round(nifty[calendar[j]], 2) if calendar[j] in nifty else None,
            "nc": _pct(nifty.get(calendar[j]), nifty.get(calendar[j - 1])),
        })

    return {
        "asof": asof.isoformat(),
        "universe": len(universe),
        "priced": len(grid),
        "horizons": HORIZON_KEYS,
        "industries": industries,
        "clusters": clusters,
        "stocks": stocks,
        "breadth": sessions,
        "dropped": {"no_data": dropped["no_data"], "stale": dropped["stale"], "short": dropped["short"],
                    "outlier": sorted(dropped["outlier"])[:25]},
    }


def validate(new: dict, previous: dict | None) -> None:
    """Final gate before the file is replaced."""
    if previous and new["asof"] < previous.get("asof", ""):
        raise MarketError(f"new data ({new['asof']}) is older than the saved data ({previous['asof']})")
    if not new["clusters"] or not new["stocks"] or not new["breadth"]:
        raise MarketError("a section came out empty")
    latest = new["breadth"][0]
    if latest["tr"] < MIN_COVERAGE * new["universe"]:
        raise MarketError("breadth covers too few stocks")
    for s in new["stocks"]:
        for v in s[3]:
            if v is not None and not -99.5 <= v <= MAX_RETURN:
                raise MarketError(f"{s[0]} has an impossible return ({v}%)")


# ----------------------------------------------------------------------------- job

def _read_json(path: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def refresh(make_fetcher, market_path: str, universe_path: str, now: datetime) -> tuple[dict | None, dict]:
    """Fetch, compute, check, save. Returns (payload to show, diagnostics). Never raises."""
    started = time.monotonic()
    previous = _read_json(market_path)
    info: dict = {"ok": False, "note": ""}
    try:
        universe, note = load_universe(make_fetcher(), universe_path, now.astimezone(IST).date())
        info["universe_note"] = note
        symbols = [row["s"] for row in universe]
        nifty_series, nifty_fail, _ = fetch_series([NIFTY_SYMBOL], make_fetcher, time.monotonic() + 60)
        if NIFTY_SYMBOL not in nifty_series:
            raise MarketError(f"Nifty 50 history unavailable ({nifty_fail})")
        info["nifty_last_sessions"] = [d.isoformat() for d in sorted(nifty_series[NIFTY_SYMBOL])[-3:]]
        series, failures, examples = fetch_series(symbols, make_fetcher, started + BUDGET_SECONDS)
        info["stock_last_sessions"] = [d.isoformat() for d in session_calendar(series)[-3:]]
        info["fetch_failures"] = failures
        if examples:
            info["failure_examples"] = examples
        info["fetched"] = len(series)
        payload = build_market(universe, series, nifty_series[NIFTY_SYMBOL], now)
        validate(payload, previous)
        _write_json(market_path, payload)
        info.update(ok=True, asof=payload["asof"], universe=payload["universe"], priced=payload["priced"],
                    dropped=payload["dropped"], note="saved")
        return payload, info
    except MarketError as e:
        info["note"] = f"kept previous data: {e}"
    except Exception as e:  # noqa: BLE001 - a data job must report, never crash the workflow
        info["note"] = f"kept previous data: unexpected {type(e).__name__}: {str(e)[:160]}"
    info["asof_kept"] = (previous or {}).get("asof", "")
    return previous, info


def load(market_path: str) -> dict | None:
    data = _read_json(market_path)
    if data and data.get("stocks") and data.get("clusters") and data.get("breadth"):
        return data
    return None
