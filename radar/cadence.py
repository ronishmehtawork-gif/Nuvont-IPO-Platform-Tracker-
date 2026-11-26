"""When things run. GitHub's own schedule fires only a few times a day (it dropped most of the hourly runs), so the
radar keeps itself going: each run starts a small waiting job (tick.yml) that starts the next run after the pause
chosen here. The old hourly schedule stays as a backup that restarts the chain if it ever breaks.

All times are IST. Nothing here makes a network call."""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

SESSION_OPEN, SESSION_CLOSE = time(9, 15), time(15, 45)  # the second is when the day's bar is treated as final
BUSY_FROM, BUSY_TO = time(9, 0), time(16, 15)            # market hours plus a margin: check often
EXTENDED_FROM, EXTENDED_TO = time(7, 0), time(22, 0)     # filings and commodities: check a few times an hour

LIVE_EVERY = 12  # minutes between stock-price refreshes while the market is open
IDLE_REFRESH_HOURS = 12  # outside the session, stock data is refreshed at most this often


def _ist(now: datetime) -> datetime:
    return now.astimezone(IST)


def next_delay_minutes(now: datetime) -> int:
    """Pause before the next run. Short while the market is open, longer around it, hourly at night and weekends."""
    t = _ist(now)
    if t.weekday() < 5:
        if BUSY_FROM <= t.time() < BUSY_TO:
            return 10
        if EXTENDED_FROM <= t.time() < EXTENDED_TO:
            return 25
    return 55


def session_open(now: datetime) -> bool:
    t = _ist(now)
    return t.weekday() < 5 and SESSION_OPEN <= t.time() < SESSION_CLOSE


def _parse(iso: object) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=IST)


def market_due(now: datetime, saved: dict | None) -> bool:
    """Should this run fetch the stock prices again? Often while the market is open, once after the close to settle
    the day, and otherwise only now and then. A failed attempt leaves the saved file as it was, so it is retried."""
    if not saved:
        return True
    t = _ist(now)
    updated = _parse(saved.get("updated"))
    if updated is None:
        return True
    age = (t - _ist(updated)).total_seconds() / 60
    if session_open(t):
        return age >= LIVE_EVERY
    if saved.get("live"):
        return True  # saved while the session was running; the finished day has not been fetched yet
    if t.weekday() < 5 and t.time() >= SESSION_CLOSE and saved.get("asof") != t.date().isoformat():
        return True  # the day has ended and its final prices are not saved yet
    if t.weekday() >= 5:
        friday = (t - timedelta(days=t.weekday() - 4)).date().isoformat()
        if (saved.get("asof") or "") >= friday:
            return False  # the market is shut and Friday's finished prices are already saved
    return age >= IDLE_REFRESH_HOURS * 60


def main() -> int:
    print(next_delay_minutes(datetime.now(IST)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
