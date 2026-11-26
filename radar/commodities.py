"""Commodity prices for the Commodities tab: precious and industrial metals, energy, agriculture and a few gauges.

Source: Yahoo Finance's public chart JSON for front-month futures (and a few index and currency symbols).
Output: data/commodities.json, replaced only when enough symbols pass the checks below. A bad run keeps the last
        good file, so the page never shows half-fetched numbers.

Prices are shown in the contract's own unit and currency. India rates for gold and silver are international parity
(world price converted at the day's USD/INR), before import duty, GST and dealer margins, so they will not match
the MCX or a jeweller's rate. Nothing here is investment advice."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .cadence import futures_open

IST = timezone(timedelta(hours=5, minutes=30))
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d"

# (group, [(name, yahoo symbol, unit)])
GROUPS = [
    ("Precious metals", [("Gold", "GC=F", "USD per troy oz"), ("Silver", "SI=F", "USD per troy oz"),
                         ("Platinum", "PL=F", "USD per troy oz"), ("Palladium", "PA=F", "USD per troy oz")]),
    ("Energy", [("Crude oil (WTI)", "CL=F", "USD per barrel"), ("Brent crude", "BZ=F", "USD per barrel"),
                ("Natural gas", "NG=F", "USD per MMBtu"), ("Heating oil", "HO=F", "USD per gallon"),
                ("Gasoline (RBOB)", "RB=F", "USD per gallon")]),
    ("Industrial metals", [("Copper", "HG=F", "USD per lb"), ("Aluminium", "ALI=F", "USD per tonne")]),
    ("Grains and oilseeds", [("Corn", "ZC=F", "US cents per bushel"), ("Wheat", "ZW=F", "US cents per bushel"),
                             ("Soybeans", "ZS=F", "US cents per bushel"), ("Soybean oil", "ZL=F", "US cents per lb"),
                             ("Soybean meal", "ZM=F", "USD per short ton"), ("Oats", "ZO=F", "US cents per bushel"),
                             ("Rough rice", "ZR=F", "US cents per cwt")]),
    ("Softs and livestock", [("Sugar", "SB=F", "US cents per lb"), ("Coffee", "KC=F", "US cents per lb"),
                             ("Cotton", "CT=F", "US cents per lb"), ("Cocoa", "CC=F", "USD per tonne"),
                             ("Orange juice", "OJ=F", "US cents per lb"), ("Live cattle", "LE=F", "US cents per lb"),
                             ("Feeder cattle", "GF=F", "US cents per lb"), ("Lean hogs", "HE=F", "US cents per lb"),
                             ("Lumber", "LBR=F", "USD per 1,000 board ft")]),
]
# Gauges that move commodities. 'ab' items change in points, not per cent (a yield or an index level).
CONTEXT = [
    ("USD/INR", "INR=X", "rupees per dollar", False),
    ("US dollar index", "DX-Y.NYB", "index", False),
    ("US 10-year yield", "^TNX", "per cent", True),
    ("India VIX", "^INDIAVIX", "index", False),
]
GRAMS_PER_TROY_OZ = 31.1034768
HORIZONS = [("1W", 7), ("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365)]
HORIZON_KEYS = ["1D"] + [k for k, _ in HORIZONS] + ["YTD"]

MIN_BARS = 60
MAX_STALE_DAYS = 6           # futures trade on weekdays; a longer gap means the feed has stopped
MAX_DAILY_MOVE = 0.45        # a bigger one-day move in the latest sessions is read as a data fault
RECENT_BARS = 10             # older jumps are real moves or contract rolls and stay in the history (natural gas, Jan 2026)
MIN_SHARE_OK = 0.5           # share of the requested symbols that must pass, or the old file stays
SPARK_POINTS = 90
WORKERS = 4
POLITE_DELAY = 0.1
BUDGET_SECONDS = 90
# Loose plausibility ranges (in each contract's unit). Outside them the number is a fault, not a market.
PLAUSIBLE = {"GC=F": (300, 30_000), "SI=F": (3, 1_000), "CL=F": (5, 400), "BZ=F": (5, 400), "INR=X": (40, 200)}



class CommodityError(Exception):
    """The new data failed a check; the previous file stays in place."""


def parse_chart(text: str) -> tuple[dict[date, float], float | None]:
    """Yahoo chart JSON to ({local session date: close}, last quote epoch). Raises ValueError on a wrong shape."""
    data = json.loads(text)
    result = data["chart"]["result"][0]
    offset = int((result.get("meta") or {}).get("gmtoffset") or 0)
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    if len(stamps) != len(closes):
        raise ValueError("timestamp and close lengths differ")
    out: dict[date, float] = {}
    for stamp, close in zip(stamps, closes):
        if isinstance(close, (int, float)) and close == close and close > 0:
            out[datetime.fromtimestamp(stamp + offset, timezone.utc).date()] = float(close)
    last = (result.get("meta") or {}).get("regularMarketTime")
    return out, float(last) if isinstance(last, (int, float)) else None


def _before(closes: dict[date, float], day: date) -> float | None:
    """Close on the last session at or before `day`."""
    keys = [d for d in closes if d <= day]
    return closes[max(keys)] if keys else None


def summarize(symbol: str, closes: dict[date, float], now: datetime, absolute: bool = False) -> dict:
    """Price, changes, 52-week range and a sparkline for one symbol. Raises CommodityError if it fails a check."""
    days = sorted(closes)
    if len(days) < MIN_BARS:
        raise CommodityError(f"only {len(days)} bars")
    last_day, last = days[-1], closes[days[-1]]
    if (now.astimezone(IST).date() - last_day).days > MAX_STALE_DAYS:
        raise CommodityError(f"last bar is from {last_day}")
    window = days[-(RECENT_BARS + 1):]
    for a, b in zip(window, window[1:]):
        if abs(closes[b] / closes[a] - 1) > MAX_DAILY_MOVE:
            raise CommodityError(f"{(closes[b] / closes[a] - 1) * 100:+.0f}% in one day on {b}")
    lo_ok, hi_ok = PLAUSIBLE.get(symbol, (0, float("inf")))
    if not lo_ok <= last <= hi_ok:
        raise CommodityError(f"price {last} is outside the plausible range")

    def change(base: float | None) -> float | None:
        if base is None or base <= 0:
            return None
        return round(last - base, 3) if absolute else round((last / base - 1) * 100, 2)

    ch = {"1D": change(closes[days[-2]])}
    for key, n in HORIZONS:
        ch[key] = change(_before(closes, last_day - timedelta(days=n))) if days[0] <= last_day - timedelta(days=n) else None
    ch["YTD"] = change(_before(closes, date(last_day.year - 1, 12, 31)))
    year = [closes[d] for d in days if d > last_day - timedelta(days=365)]
    hi, lo = max(year), min(year)
    pos = round((last - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0
    spark = [float(f"{closes[d]:.5g}") for d in days[-SPARK_POINTS:]]
    return {"p": float(f"{last:.6g}"), "d": last_day.isoformat(), "ch": ch, "hi": float(f"{hi:.6g}"),
            "lo": float(f"{lo:.6g}"), "pos": pos, "sp": spark}


def parity(items: dict[str, dict]) -> dict | None:
    """Gold per 10 grams and silver per kg in rupees at the world price and the day's USD/INR."""
    fx = items.get("INR=X")
    out = {}
    if fx:
        for key, symbol, grams in (("gold10g", "GC=F", 10), ("silverkg", "SI=F", 1000)):
            if symbol in items:
                out[key] = int(round(items[symbol]["p"] / GRAMS_PER_TROY_OZ * grams * fx["p"]))
        if "gold10g" in out and "silverkg" in out and items["SI=F"]["p"] > 0:
            out["ratio"] = round(items["GC=F"]["p"] / items["SI=F"]["p"], 1)
    return out or None


def fetch_all(symbols: list[str], make_fetcher, deadline: float):
    results: dict[str, dict[date, float]] = {}
    failures: dict[str, str] = {}
    lock = threading.Lock()
    local = threading.local()

    def work(symbol: str) -> None:
        if time.monotonic() > deadline:
            with lock:
                failures[symbol] = "skipped"
            return
        if not hasattr(local, "fetcher"):
            local.fetcher = make_fetcher()
        res = local.fetcher.get("commodity", CHART_URL.format(symbol=quote(symbol, safe="")))
        try:
            if not res.ok:
                raise ValueError(res.error or "no response")
            closes, _ = parse_chart(res.text)
            with lock:
                results[symbol] = closes
        except (ValueError, KeyError, IndexError, TypeError) as e:
            with lock:
                failures[symbol] = str(e)[:60] or type(e).__name__
        time.sleep(POLITE_DELAY)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(work, symbols))
    return results, failures


def build(closes_by_symbol: dict[str, dict[date, float]], now: datetime) -> tuple[dict, dict[str, str]]:
    """The page data. Returns (payload, {symbol: reason it was left out})."""
    left_out: dict[str, str] = {}
    done: dict[str, dict] = {}
    for symbol, closes in closes_by_symbol.items():
        absolute = any(symbol == s and ab for _, s, _, ab in CONTEXT)
        try:
            done[symbol] = summarize(symbol, closes, now, absolute)
        except CommodityError as e:
            left_out[symbol] = str(e)
    groups = []
    for group, items in GROUPS:
        rows = [dict(n=name, y=sym, u=unit, **done[sym]) for name, sym, unit in items if sym in done]
        if rows:
            groups.append({"n": group, "items": rows})
    context = [dict(n=name, y=sym, u=unit, ab=ab, **done[sym]) for name, sym, unit, ab in CONTEXT if sym in done]
    dates = [r["d"] for g in groups for r in g["items"]]
    return {
        "asof": max(dates) if dates else "",
        "updated": now.astimezone(IST).isoformat(timespec="minutes"),
        "live": futures_open(now),
        "groups": groups,
        "context": context,
        "parity": parity(done),
        "horizons": HORIZON_KEYS,
    }, left_out


def validate(new: dict, previous: dict | None, asked: int) -> None:
    count = sum(len(g["items"]) for g in new["groups"])
    if count < MIN_SHARE_OK * asked:
        raise CommodityError(f"only {count} of {asked} commodities passed the checks")
    if previous and new["asof"] and new["asof"] < previous.get("asof", ""):
        raise CommodityError(f"new data ({new['asof']}) is older than the saved data ({previous['asof']})")


def refresh(make_fetcher, path: str, now: datetime) -> tuple[dict | None, dict]:
    """Fetch, check, save. Returns (payload to show, diagnostics). Never raises."""
    previous = load(path)
    info: dict = {"ok": False, "note": ""}
    try:
        symbols = [s for _, items in GROUPS for _, s, _ in items] + [s for _, s, _, _ in CONTEXT]
        closes, failures = fetch_all(symbols, make_fetcher, time.monotonic() + BUDGET_SECONDS)
        payload, left_out = build(closes, now)
        info["requested"] = len(symbols)
        if failures:
            info["not_fetched"] = failures
        if left_out:
            info["left_out"] = left_out
        validate(payload, previous, len(symbols))
        if not (previous and {k: v for k, v in previous.items() if k != "updated"} == {k: v for k, v in payload.items() if k != "updated"}):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        info.update(ok=True, shown=sum(len(g["items"]) for g in payload["groups"]), asof=payload["asof"],
                    parity="gold10g" in (payload["parity"] or {}), note="saved")
        return payload, info
    except CommodityError as e:
        info["note"] = f"kept previous data: {e}"
    except Exception as e:  # noqa: BLE001 - never stop the alert run for the commodities page
        info["note"] = f"kept previous data: unexpected {type(e).__name__}: {str(e)[:160]}"
    return previous, info


def load(path: str) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("groups"), list) and data["groups"] else None
