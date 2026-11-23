"""One-off catch-up: announce filings that the silent first-run baseline stored without an alert.

Run by hand from the 'catch-up' workflow with a start date. It posts to the channel, marks those entries as
alerted so they never repeat, and changes nothing else."""
from __future__ import annotations

import os
import sys
from datetime import datetime

from . import state as st
from .http import Fetcher
from .models import ALERT_EVENTS
from .run import IST, MAX_ALERTS, _attach_documents
from .telegram import Telegram, format_alert


def pick(state: dict, since: str, segments: set, source: str = "") -> list:
    """Baseline entries filed on or after `since` that were never announced, oldest first.
    With `source` set, only entries that source reported (e.g. 'sebi' for official SEBI filings)."""
    rows = [
        (key, e) for key, e in state["seen"].items()
        if e.get("baseline") and not e.get("alerted") and (e.get("filed_on") or "") >= since
        and e["event"] in ALERT_EVENTS and e["segment"] in segments
        and (not source or source in e.get("sources", {}))
    ]
    return sorted(rows, key=lambda r: (r[1].get("filed_on") or "", r[0]))[:MAX_ALERTS]


def main() -> int:
    since = (os.getenv("CATCHUP_SINCE") or "").strip()
    try:
        datetime.strptime(since, "%Y-%m-%d")
    except ValueError:
        print("CATCHUP_SINCE must be a date like 2026-11-23.", file=sys.stderr)
        return 1
    tg = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHANNEL", ""),
                  os.getenv("TELEGRAM_ADMIN_CHAT_ID", ""))
    if not (tg.enabled and tg.channel):
        print("Telegram secrets are missing.", file=sys.stderr)
        return 1
    segments = {s.strip() for s in os.getenv("ALERT_SEGMENTS", "sme,unknown,mainboard").split(",") if s.strip()}
    path = os.getenv("RADAR_STATE", "data/state.json")
    state = st.load(path)
    fetcher, now, sent = Fetcher(), datetime.now(IST), 0
    rows = pick(state, since, segments, (os.getenv("CATCHUP_SOURCE") or "").strip())
    for key, entry in rows:
        _attach_documents(entry, fetcher)
        mid = tg.send_channel(format_alert(entry, now))
        print(f"{entry['company']} / {entry['event']}: {'sent' if mid else 'NOT sent'}")
        if mid:
            entry["message_id"], entry["alerted"] = mid, True
            sent += 1
    st.save(path, state)
    print(f"{sent} of {len(rows)} filing(s) announced")
    return 0 if sent == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
