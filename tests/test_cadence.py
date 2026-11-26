"""How often the radar runs and when the stock prices are fetched again."""
import json
from datetime import datetime, timedelta

from radar import cadence as c
from radar import run as radar_run
from radar import state as st

IST = c.IST


def at(day, h, m=0):
    return datetime(2026, 11, day, h, m, tzinfo=IST)  # 26 Nov 2026 is a Thursday, 28 Nov a Saturday


def test_the_radar_runs_day_and_night_and_slows_down_after_hours():
    assert c.next_delay_minutes(at(26, 11)) == 8  # Indian market open
    assert c.next_delay_minutes(at(26, 8)) == 30 and c.next_delay_minutes(at(26, 18)) == 30
    assert c.next_delay_minutes(at(26, 23)) == 30 and c.next_delay_minutes(at(26, 3)) == 30
    assert c.next_delay_minutes(at(28, 2)) == 60 and c.next_delay_minutes(at(28, 11)) == 60  # Saturday
    assert c.next_delay_minutes(at(29, 20)) == 60     # Sunday
    assert c.next_delay_minutes(at(30, 3)) == 30      # Monday before the market opens


def test_stock_prices_are_refetched_often_while_the_market_is_open():
    fresh = {"asof": "2026-11-26", "live": True, "updated": at(26, 11, 0).isoformat()}
    assert c.market_due(at(26, 11, 5), fresh) is False
    assert c.market_due(at(26, 11, 13), fresh) is True
    assert c.market_due(at(26, 11), None) is True
    assert c.market_due(at(26, 11), {"asof": "2026-11-25"}) is True  # no timestamp: fetch


def test_the_day_is_settled_once_after_the_close_then_left_alone():
    live = {"asof": "2026-11-26", "live": True, "updated": at(26, 15, 40).isoformat()}
    assert c.market_due(at(26, 16, 0), live) is True  # saved mid-session: fetch the finished day
    final = {"asof": "2026-11-26", "live": False, "updated": at(26, 16, 0).isoformat()}
    assert c.market_due(at(26, 17, 0), final) is False
    assert c.market_due(at(26, 17, 0), {"asof": "2026-11-25", "live": False, "updated": at(26, 9, 0).isoformat()}) is True
    assert c.market_due(at(27, 3, 0), final) is False
    assert c.market_due(at(27, 6, 0), final) is True  # more than 12 hours old


def test_a_weekend_does_not_refetch_a_finished_friday_every_hour():
    friday = {"asof": "2026-11-27", "live": False, "updated": at(27, 16, 0).isoformat()}
    assert c.market_due(at(28, 11), friday) is False


class TG:
    enabled = True

    def __init__(self):
        self.admin = []

    def send_admin(self, text):
        self.admin.append(text)


def test_a_failed_refresh_tells_the_admin_once_a_day_and_a_crash_is_contained(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(radar_run.market_data, "refresh",
                        lambda make, mp, up, now: (calls.append(1), (None, {"ok": False, "note": "kept previous data: x"}))[1])
    monkeypatch.setattr(radar_run.diag, "record", lambda *a, **k: None)
    state, tg, path = st.empty_state(), TG(), str(tmp_path / "state.json")
    market = str(tmp_path / "market.json")
    radar_run.refresh_market(state, path, market, at(26, 11), tg)
    radar_run.refresh_market(state, path, market, at(26, 12), tg)
    assert len(calls) == 2 and len(tg.admin) == 1 and "kept previous data" in tg.admin[0]
    assert json.loads((tmp_path / "state.json").read_text())["market_note"] == "2026-11-26"
    radar_run.refresh_market(state, path, market, at(27, 12), tg)
    assert len(tg.admin) == 2
    monkeypatch.setattr(radar_run.market_data, "refresh", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    radar_run.refresh_market(state, path, market, at(27, 13), tg)  # must not raise


def test_nothing_is_fetched_when_the_data_is_fresh(monkeypatch, tmp_path):
    market = tmp_path / "market.json"
    now = at(26, 11, 5)
    market.write_text(json.dumps({"stocks": [1], "clusters": [1], "asof": "2026-11-26", "live": True,
                                  "updated": at(26, 11, 0).isoformat()}))
    monkeypatch.setattr(radar_run.market_data, "refresh", lambda *a: (_ for _ in ()).throw(AssertionError("fetched")))
    radar_run.refresh_market(st.empty_state(), str(tmp_path / "s.json"), str(market), now, TG())
    assert now - at(26, 11, 0) < timedelta(minutes=12)


def test_every_run_in_market_hours_refreshes_the_prices():
    # A run starts BUSY_EVERY minutes plus its own run time after the last one; the price refresh must be due by then.
    assert c.LIVE_EVERY < c.BUSY_EVERY + 1
