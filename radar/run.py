from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import bankers, cadence, diag, flows, ipowatch, listings
from . import market as market_data
from . import state as st
from .documents import exchange_doc_label, find_pdf_links
from .http import Fetcher
from .models import ALERT_EVENTS, SEGMENT_RANK, SourceResult, label
from .sectors import fetch_sector_heatmap
from .site import build_companies, build_site, enrich_ipos
from .sources import all_sources
from .whatsapp import WhatsApp, join_link
from .telegram import (
    Telegram,
    esc,
    format_alert,
    format_digest,
    format_official_update,
)

IST = ZoneInfo("Asia/Kolkata")
FAIL_THRESHOLD = 3  # failed runs in a row before the admin is told
EMPTY_LIMIT = 16  # runs in a row with zero items, for a source that used to return items
MAX_ALERTS = 12  # per run; the rest go out as one digest
HEARTBEAT_HOUR = 9  # IST
_TRUNC_BANKERS = re.compile(r"\+\s*\d+\s*more\s*$", re.I)


def _run_source(name, fn, fetcher) -> SourceResult:
    try:
        return fn(fetcher)
    except Exception as e:  # one broken adapter must not stop the others
        return SourceResult(name, False, [], f"crashed: {type(e).__name__}")


def format_report(title: str, results: list, state: dict, now: datetime) -> str:
    lines = [f"📊 <b>{esc(title)}</b>"]
    for r in results:
        if not r.ok:
            lines.append(f"⚠️ {esc(label(r.name))}: FAILED ({esc(r.note)})")
        elif not r.records:
            lines.append(f"⚠️ {esc(label(r.name))}: answered, 0 items parsed (layout may differ)")
        else:
            lines.append(f"✅ {esc(label(r.name))}: answered, {len(r.records)} items")
    cutoff = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    fresh = sum(
        1 for e in state["seen"].values() if not e.get("baseline") and e["first_seen"] >= cutoff
    )
    lines.append(f"🆕 New filings in the last 24 hours: {fresh}")
    return "\n".join(lines)


def run_once(sources, fetcher, tg, state, now, alert_segments, site_url="") -> list:
    first_run = not state["started"]
    stamp = now.isoformat(timespec="seconds")
    results = [_run_source(n, fn, fetcher) for n, fn in sources]
    # A source's first-ever batch of items is a baseline: stored silently, never alerted.
    boot_sources = {
        r.name for r in results if r.records and not state["health"].get(r.name, {}).get("ever_parsed")
    }

    known_companies = {k.split("|")[0] for k in state["seen"]}  # on the radar before this run
    today = now.astimezone(IST).date()
    merged: dict = {}
    for res in results:
        for rec in res.records:
            merged.setdefault(_record_key(rec, state), []).append(rec)

    new_entries, official_updates = [], []
    for key, recs in merged.items():
        entry = state["seen"].get(key)
        is_new = entry is None
        baseline = all(r.source in boot_sources for r in recs)
        if is_new:
            first = recs[0]
            entry = {
                "company": first.company,
                "event": first.event,
                "segment": first.segment,
                "exchange": first.exchange,
                "filed_on": "",
                "first_seen": stamp,
                "sources": {},
                "official_url": "",
                "message_id": None,
                "alerted": False,
                "baseline": baseline,
            }
            state["seen"][key] = entry
        for rec in recs:
            entry["company"] = _better_name(entry["company"], rec.company)
            entry["sources"].setdefault(rec.source, rec.url)
            if SEGMENT_RANK[rec.segment] > SEGMENT_RANK[entry["segment"]]:
                entry["segment"] = rec.segment
            entry["exchange"] = entry["exchange"] or rec.exchange
            entry["filed_on"] = entry["filed_on"] or rec.filed_on
            if rec.official and not entry["official_url"]:
                entry["official_url"] = rec.url
                if not is_new and entry["message_id"] and _not_listed(official_updates, entry):
                    official_updates.append(entry)
            for k, v in rec.info.items():
                if not v:
                    continue
                cur = entry.get("info", {}).get(k, "")
                if not cur or (k == "bankers" and _TRUNC_BANKERS.search(cur) and not _TRUNC_BANKERS.search(v)):
                    entry.setdefault("info", {})[k] = v
            if rec.doc_url and all(d["url"] != rec.doc_url for d in entry.get("documents", [])):
                entry.setdefault("documents", []).append(
                    {"label": exchange_doc_label(rec.doc_url), "url": rec.doc_url}
                )
                if not is_new and entry["message_id"] and _not_listed(official_updates, entry):
                    official_updates.append(entry)
        if is_new and not baseline and _is_backfill(entry, key, known_companies, today):
            entry["backfill"] = True  # kept for the dashboard, never alerted
        elif is_new and not baseline and entry["segment"] in alert_segments and entry["event"] in ALERT_EVENTS:
            new_entries.append((key, entry))

    for key, entry in new_entries[:MAX_ALERTS]:
        _attach_documents(entry, fetcher)
        mid = tg.send_channel(format_alert(entry, now))
        if tg.enabled and mid is None:
            del state["seen"][key]  # delivery failed: forget it so the next run retries
            continue
        entry["message_id"], entry["alerted"] = mid, True
    overflow = new_entries[MAX_ALERTS:]
    if overflow:
        tg.send_channel(format_digest([e for _, e in overflow], site_url))
        for _, e in overflow:
            e["alerted"] = True
    for entry in official_updates:
        tg.send_channel(format_official_update(entry), reply_to=entry["message_id"])

    _update_health(results, state, tg, stamp)

    today = now.astimezone(IST).date().isoformat()
    if first_run:
        tg.send_admin(format_report("First run. Source check", results, state, now))
        state["started"], state["last_heartbeat"] = True, today
    elif boot_sources:
        parts = [f"{label(r.name)} ({len(r.records)} items)" for r in results if r.name in boot_sources]
        tg.send_admin("<b>Baseline saved</b>\nFirst data received from: " + esc(", ".join(parts))
                      + ". Existing items were stored silently; only new ones will alert.")
    if not first_run and now.astimezone(IST).hour >= HEARTBEAT_HOUR and state["last_heartbeat"] != today:
        tg.send_admin(format_report("Daily check", results, state, now))
        state["last_heartbeat"] = today
    return results


# A filing older than this is history, not news. It is saved for the dashboard but never sent to the channel.
MAX_AGE_DAYS = int(os.getenv("ALERT_MAX_AGE_DAYS", "45") or 45)


def _is_backfill(entry: dict, key: str, known_companies: set, today: date) -> bool:
    """True when the radar is only now noticing something that is not new.

    Aggregator pages keep adding older companies to their lists, so 'not seen before' is not the same as
    'just filed'. A filing is news only if its date is recent. An approval is news only if the company was
    already on the radar before it showed up as approved (a real status change), not when it first appears
    with the approval already attached."""
    if entry["event"] == "approval":
        return key.split("|")[0] not in known_companies
    try:
        filed = date.fromisoformat(entry["filed_on"])
    except ValueError:
        return False  # no usable date: cannot call it old
    return (today - filed).days > MAX_AGE_DAYS


def _record_key(rec, state) -> str:
    """Normally company|event. A later addendum from the same company is a new filing with its own page,
    so it gets its own key instead of being swallowed as a duplicate of the first one."""
    key = rec.key
    if rec.event == "addendum":
        old = state["seen"].get(key)
        if old and old.get("sources", {}).get(rec.source, rec.url) != rec.url:
            m = re.search(r"_(\d+)\.html", rec.url)
            key = f"{key}|{m.group(1) if m else rec.url}"
    return key


def _name_score(name: str) -> int:
    return (0 if name.isupper() else 2) + (1 if name.rstrip(". ").lower().endswith("limited") else 0)


def _better_name(old: str, new: str) -> str:
    """Prefer 'Zadley India Limited' over 'ZADLEY INDIA LTD.' when sources spell a company differently."""
    return new if _name_score(new) > _name_score(old) else old


def _not_listed(entries: list, entry: dict) -> bool:
    return all(e is not entry for e in entries)


def _attach_documents(entry: dict, fetcher) -> None:
    """For SEBI filings, read the filing page once and put its direct PDF links first. Never blocks an alert."""
    url = entry.get("official_url") or ""
    if "sebi.gov.in" not in url or entry.get("sebi_checked"):
        return
    try:
        res = fetcher.get("sebi_filing_page", url)
        if res.ok:
            pdfs = find_pdf_links(res.text, url)
            have = [d for d in entry.get("documents", []) if all(d["url"] != p["url"] for p in pdfs)]
            if pdfs or have:
                entry["documents"] = pdfs + have
            entry["sebi_checked"] = True
    except Exception:  # the alert must still go out with the filing-page link
        pass


def _update_health(results, state, tg, stamp) -> None:
    for res in results:
        h = state["health"].setdefault(
            res.name,
            {"failures": 0, "down_alerted": False, "empty_streak": 0, "ever_parsed": False,
             "last_ok": "", "last_note": ""},
        )
        h["last_note"] = res.note
        if res.ok and res.records:
            h["ever_parsed"], h["empty_streak"] = True, 0
        elif res.ok:
            h["empty_streak"] += 1
        bad = (not res.ok) or (h["ever_parsed"] and h["empty_streak"] >= EMPTY_LIMIT)
        if not bad:
            was_down = h["down_alerted"]
            # date only: a full timestamp would change state.json (and cause a commit) every run
            h.update(failures=0, down_alerted=False, last_ok=stamp[:10])
            if was_down:
                tg.send_admin(f"<b>Source recovered</b>\n{esc(label(res.name))} is answering again.")
            continue
        h["failures"] += 1
        if h["failures"] >= FAIL_THRESHOLD and not h["down_alerted"]:
            h["down_alerted"] = True
            tg.send_admin(
                f"<b>Source problem</b>\n{esc(label(res.name))} has failed {h['failures']} runs "
                f"in a row ({esc(res.note)}). Other sources are still running."
            )


def refresh_market(state: dict, state_path: str, market_path: str, now: datetime, tg) -> None:
    """Refresh the stock prices behind the heatmap and sector flow when they are due (see cadence.market_due):
    every few minutes while the market is open, once after the close, and now and then otherwise.
    A failure keeps the last good data and tells the admin chat once a day."""
    try:
        if not cadence.market_due(now, market_data.load(market_path)):
            print("market: not due")
            return
        _, info = market_data.refresh(lambda: Fetcher(), market_path, os.getenv("RADAR_UNIVERSE", "data/universe.json"), now)
        diag.record("market", info)
        print("market:", {k: v for k, v in info.items() if k not in ("dropped", "bars_per_date", "missing_prev_examples")})
        day = now.astimezone(IST).date().isoformat()
        if not info["ok"] and state.get("market_note") != day and tg.enabled:
            tg.send_admin(f"<b>Market data problem</b>\n{esc(info['note'])[:400]}")
            state["market_note"] = day
            st.save(state_path, state)
    except Exception as e:  # noqa: BLE001 - the stock pages must never stop filings, alerts or the page build
        print(f"market refresh failed: {type(e).__name__}", file=sys.stderr)


def site_address(env=None) -> str:
    """Where the dashboard lives, for the links inside alerts. RADAR_SITE_URL wins when set; otherwise it is worked out
    from the repository (owner/name), so renaming the repository moves the links with it."""
    env = os.environ if env is None else env
    url = (env.get("RADAR_SITE_URL") or "").strip()
    if url:
        return url
    owner, _, name = (env.get("GITHUB_REPOSITORY") or "").partition("/")
    return f"https://{owner.lower()}.github.io/{name}/" if owner and name else ""


def main() -> None:
    path = os.getenv("RADAR_STATE", "data/state.json")
    tg = Telegram(
        os.getenv("TELEGRAM_BOT_TOKEN", ""),
        os.getenv("TELEGRAM_CHANNEL", ""),
        os.getenv("TELEGRAM_ADMIN_CHAT_ID", ""),
    )
    if os.getenv("GITHUB_ACTIONS") and not (tg.enabled and os.getenv("TELEGRAM_CHANNEL")
                                           and os.getenv("TELEGRAM_ADMIN_CHAT_ID")):
        # Stop before saving anything: a baseline taken without Telegram would hide the first source report.
        print("Telegram secrets are missing. Add TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL and "
              "TELEGRAM_ADMIN_CHAT_ID under Settings > Secrets and variables > Actions.", file=sys.stderr)
        sys.exit(1)
    segments = {s.strip() for s in os.getenv("ALERT_SEGMENTS", "sme,unknown,mainboard").split(",") if s.strip()}
    fetcher = Fetcher(snapshot_dir=os.getenv("RADAR_SNAPSHOTS", "snapshots"))
    state = st.load(path)
    now = datetime.now(IST)
    site_url = site_address()
    run_once(all_sources(), fetcher, tg, state, now, segments, site_url)
    st.save(path, state)  # save first: alerts are already out, so nothing below may cause a repeat
    # WhatsApp is a second, independent channel: it sends the alerts Telegram has just announced, one message
    # per recipient, and saves its own progress after each alert. Whatever happens there, Telegram is done.
    wa = WhatsApp.from_env()
    wa_info = wa.deliver(state, now, site_url, admin=tg, checkpoint=lambda: st.save(path, state))
    st.save(path, state)
    diag.record("whatsapp", wa_info)
    print("whatsapp:", wa_info)
    try:
        sectors = fetch_sector_heatmap(fetcher, now)
        if sectors["groups"]:
            state["sectors"] = sectors
            st.save(path, state)
    except Exception as e:  # noqa: BLE001 - the index strip is optional; alerts and the page go on without it
        print(f"sector index fetch failed: {type(e).__name__}", file=sys.stderr)
    ipos, ipo_info = ipowatch.refresh(fetcher, os.getenv("RADAR_IPOS", "data/ipos.json"), now)
    diag.record("ipo_pipeline", ipo_info)
    print("ipo pipeline:", ipo_info)
    flows_path = os.getenv("RADAR_FLOWS", "data/flows.json")
    _, flow_info = flows.refresh(fetcher, flows_path, now)
    diag.record("flows", flow_info)
    print("flows:", flow_info)
    market_path = os.getenv("RADAR_MARKET", "data/market.json")
    refresh_market(state, path, market_path, now, tg)
    ledger_path, seed_path = os.getenv("RADAR_BANKERS", "data/bankers_record.json"), os.getenv("RADAR_BANKERS_SEED", "data/bankers_seed.csv")
    companies = build_companies(state)
    try:
        if ipos and ipos.get("items"):
            bankers.update_ledger(enrich_ipos(ipos["items"], companies), ledger_path, now.date().isoformat())
    except Exception as e:  # noqa: BLE001 - the directory's record is optional; the page builds without it
        print(f"bankers ledger update failed: {type(e).__name__}", file=sys.stderr)
    listings_path = os.getenv("RADAR_LISTINGS", "data/listings.json")
    listings_data, list_info = listings.refresh(fetcher, listings_path, now, ledger_path=ledger_path, ipos=ipos, companies=companies)
    diag.record("listings", list_info)
    print("listings:", list_info)
    try:
        if listings_data and listings_data.get("items"):
            bankers.record_listing_prices(listings_data["items"], ledger_path)
        bankers_data = bankers.record(ledger_path, seed_path)
    except Exception as e:  # noqa: BLE001 - the directory's record is optional; the page builds without it
        print(f"bankers record failed: {type(e).__name__}", file=sys.stderr)
        bankers_data = None
    build_site(state, os.getenv("RADAR_SITE", "docs/index.html"), now,
               market=market_data.load(market_path), ipos=ipos,
               flows=flows.load(flows_path), whatsapp_join=join_link(), bankers=bankers_data,
               listings=listings_data)


if __name__ == "__main__":
    main()
