"""New Listings (5-Session Debut Tracker): tracks newly listed IPOs across their first 5 trading sessions.

Sources:
  * Candidates: data/bankers_record.json + data/ipos.json (listing date <= today, within last 14 calendar days).
  * Ticker resolution: NSE official master CSVs (EQUITY_L.csv, SME_EQUITY_L.csv) first, cached daily, with
    optional manual overrides in data/listings_overrides.json and Yahoo search fallback for BSE-only SMEs.
  * Prices & volume: Yahoo Finance daily OHLCV chart JSON (NSE volume only for .NS tickers).

Never invents or estimates prices. Keeps last valid data on temporary feed errors."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

IST = timezone(timedelta(hours=5, minutes=30))

NSE_MAIN_CSV = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_SME_CSV = "https://nsearchives.nseindia.com/content/equities/SME_EQUITY_L.csv"
YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=6&newsCount=0"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"

LOOKBACK_DAYS = 14
MAX_SESSIONS = 5


def _key(name: str) -> str:
    s = re.sub(r"\b(limited|ltd|pvt|private|india|industries)\b", "", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", s)


def _strict_key(name: str) -> str:
    s = re.sub(r"\b(limited|ltd|pvt|private)\b", "", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", s)


def _read_json(path: str) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def _parse_nse_csv(text: str, segment: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    rows = []
    for raw in reader:
        r = {(k or "").strip().upper(): (v or "").strip() for k, v in raw.items()}
        sym = r.get("SYMBOL", "").upper()
        name = r.get("NAME OF COMPANY", "")
        if not sym or not name or not re.fullmatch(r"[A-Z0-9&\-]{1,20}", sym):
            continue
        rows.append({"s": sym, "n": name, "sg": segment, "ls": r.get("DATE OF LISTING", "")})
    return rows


def _load_nse_master(fetcher, cache_path: str, today_iso: str) -> list[dict]:
    cached = _read_json(cache_path)
    if cached and cached.get("fetched") == today_iso and isinstance(cached.get("rows"), list):
        return cached["rows"]
    rows: list[dict] = []
    for seg, url in (("M", NSE_MAIN_CSV), ("S", NSE_SME_CSV)):
        res = fetcher.get(f"nse_master_{seg}", url)
        if res.ok:
            rows.extend(_parse_nse_csv(res.text, seg))
    if rows:
        _write_json(cache_path, {"fetched": today_iso, "rows": rows})
        return rows
    return (cached or {}).get("rows") or []


def _parse_ohlcv(text: str) -> list[dict]:
    data = json.loads(text)
    result = data["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    q = result["indicators"]["quote"][0]
    opens = q.get("open") or [None] * len(stamps)
    highs = q.get("high") or [None] * len(stamps)
    lows = q.get("low") or [None] * len(stamps)
    closes = q.get("close") or [None] * len(stamps)
    vols = q.get("volume") or [None] * len(stamps)
    by_day: dict[date, dict] = {}
    for st, o, h, l, c, v in zip(stamps, opens, highs, lows, closes, vols):
        if not isinstance(c, (int, float)) or c != c or c <= 0:
            continue
        day = datetime.fromtimestamp(st, IST).date()
        ov = float(o) if isinstance(o, (int, float)) and o == o and o > 0 else float(c)
        hv = float(h) if isinstance(h, (int, float)) and h == h and h > 0 else max(ov, float(c))
        lv = float(l) if isinstance(l, (int, float)) and l == l and l > 0 else min(ov, float(c))
        vv = int(v) if isinstance(v, (int, float)) and v == v and v > 0 else 0
        if day not in by_day:
            by_day[day] = {"d": day, "o": ov, "h": hv, "l": lv, "c": float(c), "v": vv}
        else:
            cur = by_day[day]
            cur["h"] = max(cur["h"], hv)
            cur["l"] = min(cur["l"], lv)
            cur["c"] = float(c)
            cur["v"] = max(cur["v"], vv)
    return [by_day[d] for d in sorted(by_day)]


def _fetch_valid_bars(fetcher, symbol: str, ls_date: date, issue_price: float | None) -> list[dict] | None:
    res = fetcher.get("listing_chart", YAHOO_CHART.format(symbol=quote(symbol, safe="")))
    if not res.ok:
        return None
    try:
        bars = _parse_ohlcv(res.text)
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    post = [b for b in bars if b["d"] >= ls_date]
    if not post:
        return None
    if (post[0]["d"] - ls_date).days > 4:
        return None
    if issue_price and issue_price > 0:
        ratio = post[0]["o"] / issue_price
        if not (0.2 <= ratio <= 5.0):
            return None
    return post[:MAX_SESSIONS]


def _resolve_candidates(name: str, seg: str, nse_rows: list[dict], fetcher) -> list[str]:
    sk, lk = _strict_key(name), _key(name)
    out: list[str] = []
    for row in nse_rows:
        rsk, rlk = _strict_key(row["n"]), _key(row["n"])
        if sk == rsk or (lk and len(lk) >= 5 and (lk == rlk or lk in rsk or rsk in sk)):
            sym = row["s"]
            if row["sg"] == "S" or seg == "S":
                for cand in (f"{sym}-SM.NS", f"{sym}.NS"):
                    if cand not in out:
                        out.append(cand)
            else:
                cand = f"{sym}.NS"
                if cand not in out:
                    out.append(cand)
    if not out:
        res = fetcher.get("listing_search", YAHOO_SEARCH.format(q=quote(name)))
        if res.ok:
            try:
                quotes = json.loads(res.text).get("quotes") or []
            except ValueError:
                quotes = []
            for q in quotes:
                sym = (q.get("symbol") or "").upper()
                if sym.endswith((".NS", ".BO")) and sym not in out:
                    out.append(sym)
    return out[:4]


def _circuit_flag(sess: list[dict], seg: str, sz: float | None) -> str:
    if not sess:
        return ""
    last = sess[-1]
    o, h, l, c = last["o"], last["h"], last["l"], last["c"]
    spread = (h - l) / h if h > 0 else 1.0
    small_band = (seg == "S") or (sz is not None and sz <= 250.0)
    band = 0.05 if small_band else 0.20
    if len(sess) == 1:
        move = (c / o - 1.0) if o > 0 else 0.0
        if h > 0 and (h - c) / h <= 0.0015 and move >= band - 0.0025:
            return "upper_lock"
        if l > 0 and (c - l) / l <= 0.0015 and move <= -(band - 0.0025):
            return "lower_lock"
        return ""
    prev_c = sess[-2]["c"]
    chg = (c / prev_c - 1.0) if prev_c > 0 else 0.0
    if spread <= 0.0025 and chg >= 0.0475:
        return "upper_lock"
    if spread <= 0.0025 and chg <= -0.0475:
        return "lower_lock"
    if h > 0 and (h - c) / h <= 0.0015 and chg >= band - 0.0025:
        return "upper_lock"
    if l > 0 and (c - l) / l <= 0.0015 and chg <= -(band - 0.0025):
        return "lower_lock"
    return ""


def _build_entry(base: dict, symbol: str, bars: list[dict] | None, now: datetime) -> dict:
    ist = now.astimezone(IST)
    today = ist.date()
    ls_iso = base["ls"]
    ip = base.get("ip")
    sz = base.get("sz")
    gmp = base.get("gmp")
    gmp_pct = base.get("gmp_pct")
    if gmp_pct is None and gmp is not None and ip:
        gmp_pct = round(gmp / ip * 100, 2)

    out = {
        "n": base["n"], "sg": base.get("sg") or "M", "x": base.get("x") or "",
        "sc": base.get("sc") or "", "bk": base.get("bk") or [], "ls": ls_iso,
        "sym": symbol, "ip": ip, "sz": sz, "gmp": gmp, "gmp_pct": gmp_pct,
        "day": 0, "status": "awaiting", "sess": [],
    }
    if not bars:
        if ls_iso == today.isoformat() and dtime(9, 0) <= ist.time() < dtime(10, 0):
            out["status"] = "preopen"
        return out

    sess = []
    for idx, b in enumerate(bars):
        o, h, l, c, v = round(b["o"], 2), round(b["h"], 2), round(b["l"], 2), round(b["c"], 2), int(b["v"])
        turn = round(c * v / 1e7, 2) if v > 0 else 0.0
        ref = o if idx == 0 else bars[idx - 1]["c"]
        chg = round((c / ref - 1.0) * 100, 2) if ref > 0 else 0.0
        sess.append({"d": b["d"].isoformat(), "o": o, "h": h, "l": l, "c": c, "v": v, "t": turn, "chg": chg})

    day_n = len(sess)
    lo_open, ltp = sess[0]["o"], sess[-1]["c"]
    hi5, lo5 = max(s["h"] for s in sess), min(s["l"] for s in sess)
    rpos = round((ltp - lo5) / (hi5 - lo5) * 100, 1) if hi5 > lo5 else 50.0
    cum_turn = round(sum(s["t"] for s in sess), 2)
    churn = round(cum_turn / sz * 100, 1) if sz and sz > 0 and cum_turn > 0 else None
    v1 = sess[0]["v"]
    vol_vs_d1 = round(sess[-1]["v"] / v1 * 100, 1) if day_n > 1 and v1 > 0 else None

    lg_rs = round(lo_open - ip, 2) if ip else None
    lg_pct = round((lo_open / ip - 1.0) * 100, 2) if ip else None
    tg_pct = round((ltp / ip - 1.0) * 100, 2) if ip else None
    o2c_pct = round((ltp / lo_open - 1.0) * 100, 2) if lo_open > 0 else None
    gmp_diff = round(lg_pct - gmp_pct, 2) if (lg_pct is not None and gmp_pct is not None) else None

    verdict = ""
    if ip and lo_open:
        if ltp >= lo_open and ltp >= ip:
            verdict = "Holding above open"
        elif ltp < lo_open and ltp >= ip:
            verdict = "Fading from pop"
        elif ltp >= lo_open and ltp < ip:
            verdict = "Recovering below issue"
        else:
            verdict = "Broken issue price"

    market_open = ist.weekday() < 5 and (dtime(10, 0) if day_n == 1 and ls_iso == today.isoformat() else dtime(9, 15)) <= ist.time() < dtime(15, 45)
    if day_n >= MAX_SESSIONS and not (market_open and sess[-1]["d"] == today.isoformat()):
        status = "completed"
    elif market_open and sess[-1]["d"] == today.isoformat():
        status = "live"
    else:
        status = "active"

    out.update({
        "day": day_n, "status": status, "lo": lo_open, "ltp": ltp,
        "lg_rs": lg_rs, "lg_pct": lg_pct, "tg_pct": tg_pct, "o2c_pct": o2c_pct,
        "gmp_diff": gmp_diff, "verdict": verdict,
        "hi5": hi5, "lo5": lo5, "rpos": rpos, "circ": _circuit_flag(sess, out["sg"], sz),
        "turn_cr": cum_turn, "churn": churn, "vol_d1": vol_vs_d1, "sess": sess,
    })
    return out


def refresh(fetcher, path: str, now: datetime, ledger_path: str = "data/bankers_record.json",
            ipos: dict | None = None, companies: list[dict] | None = None,
            overrides_path: str = "data/listings_overrides.json",
            symbols_path: str = "data/nse_symbols.json") -> tuple[dict | None, dict]:
    """Build or refresh 5-session debut tracking for newly listed IPOs. Never raises."""
    ist = now.astimezone(IST)
    today = ist.date()
    today_iso = today.isoformat()
    cutoff_iso = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    previous = load(path)
    prev_by = {_strict_key(i["n"]): i for i in (previous or {}).get("items", []) if i.get("n")}
    overrides = _read_json(overrides_path) or {}
    comp_by = {_strict_key(c["n"]): c for c in (companies or []) if c.get("n")}

    # Collect candidates from bankers_record.json + ipos.json
    cands: dict[str, dict] = {}
    ledger = _read_json(ledger_path) or {}
    for row in ledger.get("ipos") or []:
        ls = row.get("ls") or ""
        if ls and cutoff_iso <= ls <= today_iso:
            k = _strict_key(row["n"])
            cands[k] = {
                "n": row["n"], "sg": row.get("sg") or "M", "bk": row.get("bk") or [],
                "sz": row.get("sz"), "ip": row.get("ip"), "ls": ls,
                "gmp": row.get("gmp"), "gmp_pct": row.get("gmp_pct"), "x": "", "sc": "",
            }
    for row in (ipos or {}).get("items") or []:
        ls = row.get("ls") or ""
        if ls and cutoff_iso <= ls <= today_iso:
            k = _strict_key(row["n"])
            cur = cands.setdefault(k, {"n": row["n"], "sg": row.get("sg") or "M", "bk": [], "ls": ls})
            for src, dst in (("sz", "sz"), ("hi", "ip"), ("gmp", "gmp"), ("pct", "gmp_pct"), ("sc", "sc")):
                if row.get(src) is not None and cur.get(dst) in (None, ""):
                    cur[dst] = row[src]
            if row.get("bk") and not cur.get("bk"):
                cur["bk"] = list(row["bk"])
            cur["x"] = row.get("x") or row.get("ex") or cur.get("x") or ""

    # Backfill sector/exchange from filings companies
    for k, cur in cands.items():
        co = comp_by.get(k)
        if co:
            if not cur.get("sc") and co.get("sec"):
                cur["sc"] = co["sec"]
            if not cur.get("x") and co.get("x"):
                cur["x"] = co["x"]

    if not cands:
        payload = {"updated": ist.isoformat(timespec="seconds"), "items": []}
        _write_json(path, payload)
        return payload, {"ok": True, "tracked": 0, "priced": 0, "note": "no debuts in window"}

    try:
        nse_rows = _load_nse_master(fetcher, symbols_path, today_iso)
        items: list[dict] = []
        priced = 0
        for k, base in cands.items():
            prev = prev_by.get(k) or {}
            if prev.get("gmp") is not None and base.get("gmp") is None:
                base["gmp"], base["gmp_pct"] = prev.get("gmp"), prev.get("gmp_pct")
            ls_date = date.fromisoformat(base["ls"])
            sym = overrides.get(k) or overrides.get(_key(base["n"])) or prev.get("sym") or ""
            bars = None
            if sym:
                bars = _fetch_valid_bars(fetcher, sym, ls_date, base.get("ip"))
            if bars is None:
                for cand_sym in _resolve_candidates(base["n"], base["sg"], nse_rows, fetcher):
                    got = _fetch_valid_bars(fetcher, cand_sym, ls_date, base.get("ip"))
                    if got:
                        sym, bars = cand_sym, got
                        break
            if bars is None and prev.get("sess"):
                # Keep last good session data on temporary Yahoo failure
                kept = dict(prev)
                kept["sc"] = kept.get("sc") or base.get("sc", "")
                kept["x"] = kept.get("x") or base.get("x", "")
                items.append(kept)
                priced += 1
                continue
            entry = _build_entry(base, sym, bars, now)
            if entry["sess"]:
                priced += 1
            items.append(entry)
        items.sort(key=lambda r: (r["ls"], r["n"]), reverse=True)
        payload = {"updated": ist.isoformat(timespec="seconds"), "items": items}
        _write_json(path, payload)
        return payload, {"ok": True, "tracked": len(items), "priced": priced, "note": "saved"}
    except Exception as e:  # noqa: BLE001
        return previous, {"ok": False, "note": f"kept previous data: {type(e).__name__}: {str(e)[:120]}"}


def load(path: str) -> dict | None:
    data = _read_json(path)
    return data if data and isinstance(data.get("items"), list) else None
