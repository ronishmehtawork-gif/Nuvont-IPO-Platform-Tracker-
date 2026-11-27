"""Daily market job: refresh stock-level data, then rebuild the dashboard. Run by the market workflow."""
from __future__ import annotations

import os
import sys
from datetime import datetime

from . import bankers, diag, flows, ipowatch, market
from . import state as st
from .http import Fetcher
from .run import IST
from .site import build_site
from .telegram import Telegram, esc
from .whatsapp import join_link


def main() -> None:
    now = datetime.now(IST)
    market_path = os.getenv("RADAR_MARKET", "data/market.json")
    universe_path = os.getenv("RADAR_UNIVERSE", "data/universe.json")
    payload, info = market.refresh(lambda: Fetcher(), market_path, universe_path, now)
    diag.record("market", info)
    print("market:", {k: v for k, v in info.items() if k != "dropped"})

    if not info["ok"]:
        # One short note to the private admin chat; the dashboard keeps showing the last good data.
        tg = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHANNEL", ""),
                      os.getenv("TELEGRAM_ADMIN_CHAT_ID", ""))
        if tg.enabled:
            tg.send_admin(f"<b>Market data problem</b>\n{esc(info['note'])[:400]}")

    flows_path = os.getenv("RADAR_FLOWS", "data/flows.json")
    _, flow_info = flows.refresh(Fetcher(), flows_path, now)
    diag.record("flows", flow_info)
    print("flows:", flow_info)

    state = st.load(os.getenv("RADAR_STATE", "data/state.json"))
    ipos = ipowatch.load(os.getenv("RADAR_IPOS", "data/ipos.json"))
   bankers_data = bankers.record(os.getenv("RADAR_BANKERS", "data/bankers_record.json"),
                                  os.getenv("RADAR_BANKERS_SEED", "data/bankers_seed.csv"))
    build_site(state, os.getenv("RADAR_SITE", "docs/index.html"), now, market=payload, ipos=ipos,
               flows=flows.load(flows_path), whatsapp_join=join_link(), bankers=bankers_data)
    if not info["ok"] and not payload:
        sys.exit(1)  # nothing to show at all: fail the run so it is noticed


if __name__ == "__main__":
    main()
