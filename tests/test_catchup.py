from datetime import datetime

from radar import catchup
from radar import state as st
from radar.run import IST


def entry(company, event="addendum", filed_on="2026-11-23", baseline=True, alerted=False, segment="mainboard"):
    return {
        "company": company, "event": event, "segment": segment, "exchange": "SEBI", "filed_on": filed_on,
        "first_seen": "2026-11-23T12:47:29+05:30", "sources": {"sebi": f"https://x/{company}"},
        "official_url": "", "message_id": None, "alerted": alerted, "baseline": baseline,
    }


SEGS = {"sme", "unknown", "mainboard"}


def test_pick_takes_only_unannounced_baseline_entries_from_the_date_on_oldest_first():
    state = st.empty_state()
    state["seen"] = {
        "a|addendum": entry("A Ltd", filed_on="2026-11-23"),
        "b|drhp_filed": entry("B Ltd", event="drhp_filed", filed_on="2026-11-22"),
        "old|addendum": entry("Old Ltd", filed_on="2026-11-10"),
        "done|addendum": entry("Done Ltd", alerted=True),
        "live|addendum": entry("Live Ltd", baseline=False),
        "nodate|addendum": entry("No Date Ltd", filed_on=""),
    }
    rows = catchup.pick(state, "2026-11-22", SEGS)
    assert [e["company"] for _, e in rows] == ["B Ltd", "A Ltd"]


def test_pick_respects_alert_segments():
    state = st.empty_state()
    state["seen"] = {"s|drhp_filed": entry("S Ltd", event="drhp_filed", segment="sme")}
    assert catchup.pick(state, "2026-11-01", {"mainboard"}) == []


def test_pick_can_be_limited_to_one_source():
    state = st.empty_state()
    state["seen"] = {
        "a|addendum": entry("A Ltd"),
        "b|drhp_filed": {**entry("B Ltd", event="drhp_filed"), "sources": {"ipoplatform": "https://x/b"}},
    }
    assert [e["company"] for _, e in catchup.pick(state, "2026-11-23", SEGS, "sebi")] == ["A Ltd"]
    assert len(catchup.pick(state, "2026-11-23", SEGS)) == 2
