from datetime import datetime, timedelta

from radar import run as radar_run
from radar import state as st
from radar.models import Record, SourceResult
from radar.site import build_site

IST = radar_run.IST
SEGS = {"sme", "unknown"}


class FakeTG:
    def __init__(self, enabled=True, fail=False):
        self.enabled, self.fail = enabled, fail
        self.channel, self.admin, self._id = [], [], 100

    def send_channel(self, text, reply_to=None):
        if self.fail:
            return None
        self._id += 1
        self.channel.append((self._id, text, reply_to))
        return self._id

    def send_admin(self, text):
        self.admin.append(text)


def rec(source, company, segment="sme", exchange="BSE SME", event="drhp_filed", url=None):
    return Record(source, company, event, segment, exchange, url or f"https://x/{source}/{company}")


def src(name, records, ok=True, note=""):
    return (name, lambda f: SourceResult(name, ok, list(records), note))


def go(sources, state, tg, hour=11, day=1):
    now = datetime(2026, 11, day, hour, 0, tzinfo=IST)
    return radar_run.run_once(sources, None, tg, state, now, SEGS)


def test_first_run_is_a_silent_baseline_with_source_report():
    state, tg = st.empty_state(), FakeTG()
    go([src("bse_sme", [rec("bse_sme", "Old One Ltd"), rec("bse_sme", "Old Two Ltd")])], state, tg)
    assert tg.channel == []
    assert len(state["seen"]) == 2 and all(e["baseline"] for e in state["seen"].values())
    assert len(tg.admin) == 1 and "First run" in tg.admin[0] and "2 items" in tg.admin[0]


def test_all_sources_failing_reports_once_not_every_run():
    state, tg = st.empty_state(), FakeTG()
    bad = src("bse_sme", [], ok=False, note="ProxyError")
    go([bad], state, tg, day=1)
    go([bad], state, tg, day=1, hour=12)
    assert len([m for m in tg.admin if "First run" in m]) == 1


def test_source_that_comes_online_later_is_baselined_silently():
    state, tg = st.empty_state(), FakeTG()
    go([src("bse_sme", [rec("bse_sme", "Seed Ltd")]), src("sebi", [], ok=False, note="HTTP 403")],
       state, tg, day=1)
    sebi_items = [rec("sebi", "Old Sebi A Ltd", "sme", "SEBI"), rec("sebi", "Old Sebi B Ltd", "sme", "SEBI")]
    go([src("bse_sme", [rec("bse_sme", "Seed Ltd")]), src("sebi", sebi_items)], state, tg, day=2)
    assert tg.channel == []
    assert any("Baseline saved" in m and "SEBI" in m for m in tg.admin)
    go([src("sebi", sebi_items + [rec("sebi", "Truly New Ltd", "sme", "SEBI")])], state, tg, day=3)
    assert len(tg.channel) == 1 and "Truly New Ltd" in tg.channel[0][1]


def test_new_filing_alerts_once_and_not_again():
    state, tg = st.empty_state(), FakeTG()
    base = [rec("bse_sme", "Old One Ltd")]
    go([src("bse_sme", base)], state, tg)
    more = base + [rec("bse_sme", "Fresh Co Limited")]
    go([src("bse_sme", more)], state, tg, day=2)
    go([src("bse_sme", more)], state, tg, day=3)
    assert len(tg.channel) == 1 and "Fresh Co Limited" in tg.channel[0][1]


def test_cross_source_merges_and_official_link_follows_as_reply():
    state, tg = st.empty_state(), FakeTG()
    old_bse = rec("bse_sme", "Old One Ltd")
    old_chit = rec("chittorgarh", "Old Chit Ltd", url="https://chit/old")
    go([src("bse_sme", [old_bse]), src("chittorgarh", [old_chit])], state, tg)
    news = rec("chittorgarh", "Example Industries Ltd.", url="https://chit/example")
    go([src("bse_sme", [old_bse]), src("chittorgarh", [old_chit, news])], state, tg, day=2)
    assert len(tg.channel) == 1 and "will follow" in tg.channel[0][1]
    first_id = tg.channel[0][0]
    official = rec("bse_sme", "EXAMPLE INDUSTRIES LIMITED", url="https://bse/example.pdf")
    go([src("chittorgarh", [old_chit, news]), src("bse_sme", [old_bse, official])], state, tg, day=3)
    assert len(tg.channel) == 2
    assert tg.channel[1][2] == first_id and "bse/example.pdf" in tg.channel[1][1]
    assert len([e for e in state["seen"].values() if "example" in e["company"].lower()]) == 1


def test_mainboard_is_recorded_but_not_alerted_by_default():
    state, tg = st.empty_state(), FakeTG()
    go([src("sebi", [rec("sebi", "Seed Ltd", "sme", "SEBI")])], state, tg)
    go([src("sebi", [rec("sebi", "Big Main Ltd", "mainboard", "SEBI")])], state, tg, day=2)
    assert tg.channel == []
    assert any(e["company"] == "Big Main Ltd" for e in state["seen"].values())


def test_flood_is_capped_with_one_digest():
    state, tg = st.empty_state(), FakeTG()
    go([src("bse_sme", [rec("bse_sme", "Seed Ltd")])], state, tg)
    many = [rec("bse_sme", f"Company {c} Ltd") for c in "ABCDEFGHIJKLMNO"]
    go([src("bse_sme", many)], state, tg, day=2)
    assert len(tg.channel) == radar_run.MAX_ALERTS + 1
    assert "3 more filings" in tg.channel[-1][1]


def test_failed_delivery_is_retried_next_run():
    state = st.empty_state()
    go([src("bse_sme", [rec("bse_sme", "Seed Ltd")])], state, FakeTG())
    new = [rec("bse_sme", "Seed Ltd"), rec("bse_sme", "Retry Me Ltd")]
    go([src("bse_sme", new)], state, FakeTG(fail=True), day=2)
    assert not any(e["company"] == "Retry Me Ltd" for e in state["seen"].values())
    tg = FakeTG()
    go([src("bse_sme", new)], state, tg, day=3)
    assert len(tg.channel) == 1


def test_source_down_alert_is_sent_once_then_recovery():
    state, tg = st.empty_state(), FakeTG()
    ok = src("bse_sme", [rec("bse_sme", "Seed Ltd")])
    go([ok], state, tg)
    bad = src("nse_emerge", [], ok=False, note="HTTP 403")
    for d in range(2, 7):
        go([ok, bad], state, tg, day=d)
    problems = [m for m in tg.admin if "Source problem" in m]
    assert len(problems) == 1 and "HTTP 403" in problems[0]
    good = src("nse_emerge", [rec("nse_emerge", "Nse Co Ltd", exchange="NSE Emerge")])
    go([ok, good], state, tg, day=8)
    assert any("Source recovered" in m for m in tg.admin)


def test_daily_heartbeat_once_after_nine_ist():
    state, tg = st.empty_state(), FakeTG()
    ok = src("bse_sme", [rec("bse_sme", "Seed Ltd")])
    go([ok], state, tg, day=1)  # baseline
    n = len(tg.admin)
    go([ok], state, tg, hour=8, day=2)
    assert len(tg.admin) == n
    go([ok], state, tg, hour=10, day=2)
    assert len(tg.admin) == n + 1 and "Daily check" in tg.admin[-1]
    go([ok], state, tg, hour=12, day=2)
    assert len(tg.admin) == n + 1


def test_a_crashing_source_does_not_stop_the_others():
    def boom(f):
        raise RuntimeError("layout changed")

    state, tg = st.empty_state(), FakeTG()
    go([("bse_sme", boom), src("sebi", [rec("sebi", "Seed Ltd", "sme", "SEBI")])], state, tg)
    assert len(state["seen"]) == 1 and "FAILED" in tg.admin[0]


def test_state_roundtrip_and_site_escaping(tmp_path):
    state, tg = st.empty_state(), FakeTG()
    go([src("bse_sme", [rec("bse_sme", "Seed Ltd")])], state, tg)
    go([src("bse_sme", [rec("bse_sme", "Seed Ltd"), rec("bse_sme", "Evil </script> Ltd")])],
       state, tg, day=2)
    path = tmp_path / "state.json"
    st.save(path, state)
    assert st.load(path)["seen"].keys() == state["seen"].keys()
    out = tmp_path / "index.html"
    build_site(state, str(out), datetime(2026, 11, 2, 12, 0, tzinfo=IST))
    html = out.read_text()
    assert "Seed Ltd" in html and html.count("</script>") == 1


def test_addendum_is_alerted_once():
    state, tg = st.empty_state(), FakeTG()
    go([src("bse_sme", [rec("bse_sme", "Seed Ltd")])], state, tg)
    batch = [rec("bse_sme", "Seed Ltd"), rec("bse_sme", "Amended Co Ltd", event="addendum")]
    go([src("bse_sme", batch)], state, tg, day=2)
    assert len(tg.channel) == 1 and "DRHP addendum" in tg.channel[0][1] and "Amended Co" in tg.channel[0][1]
    go([src("bse_sme", batch)], state, tg, day=2, hour=12)
    assert len(tg.channel) == 1  # not repeated


def test_second_addendum_from_same_company_is_a_new_alert():
    state, tg = st.empty_state(), FakeTG()
    go([src("bse_sme", [rec("bse_sme", "Seed Ltd")])], state, tg)
    first = rec("bse_sme", "Amended Co Ltd", event="addendum", url="https://x/filings/amended-addendum_100.html")
    go([src("bse_sme", [rec("bse_sme", "Seed Ltd"), first])], state, tg, day=2)
    second = rec("bse_sme", "Amended Co Ltd", event="addendum", url="https://x/filings/amended-addendum-2_200.html")
    both = [rec("bse_sme", "Seed Ltd"), first, second]
    go([src("bse_sme", both)], state, tg, day=3)
    go([src("bse_sme", both)], state, tg, day=3, hour=12)
    assert len(tg.channel) == 2  # first once, second once, no repeats
