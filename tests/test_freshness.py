"""Old filings that an aggregator lists late are history: saved for the dashboard, never alerted."""
from radar import state as st
from radar.models import Record

from test_run import FakeTG, go, src


def r(company, event="drhp_filed", filed_on="", source="bse_sme"):
    return Record(source, company, event, "sme", "BSE SME", f"https://x/{source}/{company}-{event}", filed_on=filed_on)


def seeded():
    state, tg = st.empty_state(), FakeTG()
    go([src("bse_sme", [r("Seed Ltd")])], state, tg)  # silent baseline
    return state, tg


def test_recent_filing_is_alerted():
    state, tg = seeded()
    go([src("bse_sme", [r("Seed Ltd"), r("Fresh Ltd", filed_on="2026-10-25")])], state, tg, day=2)
    assert len(tg.channel) == 1 and "Fresh" in tg.channel[0][1]


def test_old_filing_listed_late_is_saved_but_not_alerted():
    state, tg = seeded()
    go([src("bse_sme", [r("Seed Ltd"), r("Old Ltd", filed_on="2026-09-01")])], state, tg, day=2)
    assert tg.channel == []
    entry = state["seen"]["old|drhp_filed"]
    assert entry["backfill"] and not entry["alerted"]
    go([src("bse_sme", [r("Seed Ltd"), r("Old Ltd", filed_on="2026-09-01")])], state, tg, day=2, hour=12)
    assert tg.channel == []  # and it never comes back


def test_filing_without_a_date_is_still_alerted():
    state, tg = seeded()
    go([src("bse_sme", [r("Seed Ltd"), r("Undated Ltd")])], state, tg, day=2)
    assert len(tg.channel) == 1


def test_approval_of_a_known_company_is_alerted():
    state, tg = seeded()
    go([src("bse_sme", [r("Seed Ltd"), r("Seed Ltd", "approval", "2025-01-01")])], state, tg, day=2)
    assert len(tg.channel) == 1 and "Approval" in tg.channel[0][1]


def test_approval_that_arrives_already_attached_to_a_new_company_is_not_alerted():
    state, tg = seeded()
    batch = [r("Seed Ltd"), r("Late Ltd", "drhp_filed", "2026-10-30"), r("Late Ltd", "approval", "2026-10-30")]
    go([src("bse_sme", batch)], state, tg, day=2)
    assert len(tg.channel) == 1 and "Late" in tg.channel[0][1]  # the new filing only, not a second card
    assert state["seen"]["late|approval"]["backfill"]
