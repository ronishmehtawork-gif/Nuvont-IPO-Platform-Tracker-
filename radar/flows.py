"""Daily FII/FPI and DII cash-market activity (buy, sell and net, in ₹ crore).

Source: the combined MSE, NSE and BSE report that MSE publishes after each session
(https://www.msei.in/downloads/equity-reports/fii-dii-activities). It shows one trading day, so the radar keeps its
own history in data/flows.json, one row per day, and the Sector Outlook page draws from that. The exchanges call the
figures provisional; a later run replaces a day's row if the report is revised.

A page that does not parse, or numbers that do not add up, leave the saved history as it was."""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

URL = "https://www.msei.in/downloads/equity-reports/fii-dii-activities"
KEEP_DAYS = 400       # rows kept in the file
SHOW_DAYS = 90        # rows sent to the page
REFRESH_MINUTES = 60  # the report changes once a day, so there is no need to ask more often
EVENING_REFRESH_MINUTES = 15  # but on a weekday evening, until that day's report is saved, look more often
EVENING = (time(16, 0), time(22, 0))  # the exchanges usually publish the day's figures in this window
IST = timezone(timedelta(hours=5, minutes=30))

_NUM = r"(-?[\d,]+(?:\.\d+)?)"
_DATE = r"(\d{1,2}-[A-Za-z]{3}-\d{4})"


class FlowsError(Exception):
    pass


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _day(text: str) -> date:
    return datetime.strptime(text, "%d-%b-%Y").date()


def _plain(page: str) -> str:
    page = re.sub(r"(?is)<(script|style).*?</\1>", " ", page)
    page = re.sub(r"<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(page).replace("\xa0", " "))


def parse(page: str) -> dict:
    """Returns {"d": "YYYY-MM-DD", "fb", "fs", "fn", "db", "ds", "dn"} for the day on the report, or raises."""
    text = _plain(page)
    fii = re.search(r"FII/FPI\s+" + _DATE + r"\s+" + _NUM + r"\s+" + _NUM + r"\s+" + _NUM, text)
    dii = re.search(r"\bDII\s+" + _DATE + r"\s+" + _NUM + r"\s+" + _NUM + r"\s+" + _NUM, text)
    if not fii or not dii:
        raise FlowsError("the FII or DII row was not found on the report")
    try:
        f_day, d_day = _day(fii.group(1)), _day(dii.group(1))
    except ValueError as e:
        raise FlowsError("a date on the report could not be read") from e
    if f_day != d_day:
        raise FlowsError(f"the FII row is for {f_day} but the DII row is for {d_day}")
    row = {"d": f_day.isoformat()}
    for key, m in (("f", fii), ("d", dii)):
        buy, sell, net = (_number(m.group(i)) for i in (2, 3, 4))
        if buy < 0 or sell < 0 or buy > 1e7 or sell > 1e7:
            raise FlowsError("a buy or sell value is outside any sensible range")
        if abs((buy - sell) - net) > 0.5:
            raise FlowsError("net value does not equal buy minus sell")
        row[key + "b"], row[key + "s"], row[key + "n"] = round(buy, 2), round(sell, 2), round(net, 2)
    return row


def merge(days: list[dict], row: dict) -> list[dict]:
    """Add the day, replacing an earlier row for the same date, oldest first, newest KEEP_DAYS kept."""
    out = {d["d"]: d for d in days if isinstance(d, dict) and d.get("d")}
    out[row["d"]] = row
    return [out[k] for k in sorted(out)][-KEEP_DAYS:]


def _waiting_for_today(now: datetime, saved: dict) -> bool:
    """A weekday evening when today's report is not saved yet: it should appear any time now."""
    t = now.astimezone(IST)
    if t.weekday() >= 5 or not (EVENING[0] <= t.time() < EVENING[1]):
        return False
    days = saved.get("days") or []
    return not days or str(days[-1].get("d", "")) < t.date().isoformat()


def due(now: datetime, saved: dict | None) -> bool:
    if not saved or not saved.get("days"):
        return True
    try:
        updated = datetime.fromisoformat(saved["updated"])
    except (KeyError, ValueError, TypeError):
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=now.tzinfo)
    minutes = EVENING_REFRESH_MINUTES if _waiting_for_today(now, saved) else REFRESH_MINUTES
    return (now - updated).total_seconds() >= minutes * 60


def _read(path: str) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("days"), list) else None


def _write(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def refresh(fetcher, path: str, now: datetime) -> tuple[dict | None, dict]:
    """Fetch the report when it is due, add the day to the history and save it. Never raises."""
    saved = _read(path)
    if not due(now, saved):
        return saved, {"ok": True, "skipped": True, "days": len(saved["days"]), "asof": saved["days"][-1]["d"]}
    try:
        res = fetcher.get("flows_msei", URL)
        if not res.ok:
            raise FlowsError(res.error or "no answer")
        row = parse(res.text)
    except Exception as e:  # noqa: BLE001 - a flow problem must never stop filings, alerts or the page build
        note = str(e) if isinstance(e, FlowsError) else type(e).__name__
        info = {"ok": False, "note": "kept previous data: " + note}
        if saved:
            info.update(days=len(saved["days"]), asof=saved["days"][-1]["d"])
        return saved, info
    days = merge((saved or {}).get("days", []), row)
    data = {"source": "MSE, NSE and BSE combined FII/FPI and DII cash-market report", "unit": "Rs crore",
            "updated": now.isoformat(timespec="seconds"), "days": days}
    _write(path, data)
    return data, {"ok": True, "days": len(days), "asof": days[-1]["d"]}


def load(path: str) -> dict | None:
    """What the page gets: the newest SHOW_DAYS rows, or None before the first successful fetch."""
    data = _read(path)
    if not data or not data["days"]:
        return None
    return {"asof": data["days"][-1]["d"], "updated": data.get("updated", ""), "days": data["days"][-SHOW_DAYS:]}
