"""Send sample alerts to the private admin chat only, built from real entries already in the state.

Run by hand from the 'preview-alerts' workflow to see exactly how the alert cards look in Telegram.
It never posts to the channel and changes no saved data."""
from __future__ import annotations

import os
import sys
from datetime import datetime

from . import state as st
from .http import Fetcher
from .run import IST, _attach_documents
from .telegram import Telegram, format_alert

BANNER = "🧪 <b>SAMPLE</b> · design preview of a real past entry, not a new filing\n\n"


def pick_samples(state: dict) -> list:
    """One entry per alert style, newest filing first. Missing styles are skipped."""
    entries = sorted(state["seen"].values(), key=lambda e: e.get("filed_on") or "", reverse=True)

    def first(test):
        return next((e for e in entries if test(e)), None)

    picks = [
        first(lambda e: e["segment"] == "sme" and e["event"] == "drhp_filed" and e.get("documents") and e.get("info")),
        first(lambda e: e["segment"] == "mainboard" and e["event"] == "drhp_filed" and "sebi.gov.in" in (e.get("official_url") or "")),
        first(lambda e: e["segment"] == "sme" and e["event"] == "approval" and e.get("documents")),
        first(lambda e: e["event"] == "drhp_refiled"),
    ]
    return [dict(p) for p in picks if p]  # copies: nothing here may touch the saved state


def main() -> int:
    tg = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""), "", os.getenv("TELEGRAM_ADMIN_CHAT_ID", ""))
    if not (tg.enabled and tg.admin_chat):
        print("Telegram secrets are missing.", file=sys.stderr)
        return 1
    state = st.load(os.getenv("RADAR_STATE", "data/state.json"))
    fetcher = Fetcher()
    now = datetime.now(IST)
    sent = 0
    for entry in pick_samples(state):
        _attach_documents(entry, fetcher)
        if tg.send_admin(BANNER + format_alert(entry, now)):
            sent += 1
        print(f"sample {entry['event']} / {entry['segment']}: {'sent' if sent else 'not sent'}")
    print(f"{sent} sample(s) sent to the admin chat")
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
