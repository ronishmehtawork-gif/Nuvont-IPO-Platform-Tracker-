"""Fetch Indian sector index returns (1D to 3Y) via public chart JSON using requests."""
from __future__ import annotations

import json
from datetime import datetime

SECTOR_TICKERS = {
    "BANKING & FINANCE": [
        ("Nifty Bank", "^NSEBANK"),
        ("PSU Banks", "^CNXPSUBANK"),
        ("Financial Services", "NIFTY_FIN_SERVICE.NS"),
    ],
    "AUTOMOBILES & COMPONENTS": [
        ("Nifty Auto", "^CNXAUTO"),
    ],
    "TECHNOLOGY & TELECOM": [
        ("Nifty IT", "^CNXIT"),
        ("Nifty Media", "^CNXMEDIA"),
    ],
    "HEALTHCARE": [
        ("Nifty Pharma", "^CNXPHARMA"),
    ],
    "METALS & MINING": [
        ("Nifty Metal", "^CNXMETAL"),
    ],
    "OIL, GAS & COAL": [
        ("Nifty Energy", "^CNXENERGY"),
    ],
    "FMCG & STAPLES": [
        ("Nifty FMCG", "^CNXFMCG"),
    ],
    "REALTY & INFRA": [
        ("Nifty Realty", "^CNXREALTY"),
        ("Nifty Infra", "^CNXINFRA"),
    ],
}

HORIZONS = {"1D": 1, "1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252, "3Y": 756}


def _pct(closes: list[float], days: int) -> float | None:
    if len(closes) <= days or not closes[-1 - days]:
        return None
    return round(((closes[-1] - closes[-1 - days]) / closes[-1 - days]) * 100, 2)


def fetch_sector_heatmap(fetcher, now: datetime) -> dict:
    """Returns a dict ready to store in state['sectors'] and embed in docs/index.html."""
    groups = {}
    for group, items in SECTOR_TICKERS.items():
        rows = []
        for name, symbol in items:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5y&interval=1d"
            res = fetcher.get(f"sector_{symbol}", url)
            if not res.ok:
                continue
            try:
                data = json.loads(res.text)
                raw = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                closes = [c for c in raw if c is not None]
                returns = {k: _pct(closes, d) for k, d in HORIZONS.items()}
                rows.append({"name": name, "symbol": symbol, "returns": returns})
            except Exception:
                continue
        if rows:
            groups[group] = rows
    return {
        "updated": now.strftime("%d %b %Y, %I:%M %p IST"),
        "groups": groups,
    }
