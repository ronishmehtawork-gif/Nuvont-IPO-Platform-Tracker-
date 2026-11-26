"""WhatsApp channel: message layout from the notification note, delivery rules, error handling and privacy.
Meta is replaced by a fake session, so nothing here touches the network."""
import json
from datetime import datetime, timedelta

import requests

from radar import run as radar_run
from radar import state as st
from radar import whatsapp as wa_mod
from radar.models import Record, SourceResult
from radar.site import build_site
from radar.whatsapp import MAX_TRIES, WhatsApp, join_link, params_for, parse_recipients, render

IST = radar_run.IST
NOW = datetime(2026, 11, 26, 11, 0, tzinfo=IST)
TOKEN = "EAAB-secret-token-value"
PHONE_ID = "109876543210987"
SITE = "https://owner.github.io/Radar-Tracker/"
NUMBERS = "+91 98765 43210, 9123456789"


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeMeta:
    """Stands in for requests.Session. `script(call)` returns (status, body) or raises."""

    def __init__(self, script=None):
        self.calls = []
        self.script = script or (lambda call: (200, {"messages": [{"id": "wamid.X"}]}))

    def request(self, method, url, json=None, params=None, timeout=None, headers=None):
        call = {"method": method, "url": url, "json": json, "params": params, "headers": headers or {}}
        self.calls.append(call)
        status, body = self.script(call)
        return Resp(status, body)

    @property
    def sends(self):
        return [c for c in self.calls if c["url"].endswith("/messages")]


def error(code, status=400, message="boom"):
    return status, {"error": {"message": message, "code": code}}


class FakeTG:
    enabled = True

    def __init__(self):
        self.admin = []

    def send_admin(self, text):
        self.admin.append(text)


def client(meta=None, recipients=NUMBERS, **kw):
    kw.setdefault("pause", 0)
    return WhatsApp(token=TOKEN, phone_id=PHONE_ID, recipients=recipients, session=meta or FakeMeta(), **kw)


def entry(company="Example Industries Limited", event="drhp_filed", first_seen=None, **kw):
    base = {"company": company, "event": event, "segment": "sme", "exchange": "BSE SME", "filed_on": "2026-11-25",
            "first_seen": (first_seen or NOW).isoformat(timespec="seconds"),
            "sources": {"chittorgarh": "https://chit.test/example"}, "official_url": "", "documents": [],
            "info": {"sector": "Engineering", "bankers": "Example Capital Private Limited"},
            "message_id": 5, "alerted": True, "baseline": False}
    base.update(kw)
    return base


def state_with(*entries):
    state = st.empty_state()
    for i, e in enumerate(entries):
        state["seen"][f"{e['company'].lower()}|{e['event']}|{i}"] = e
    return state


# ---------------------------------------------------------------- message layout -----------------------------------

def test_message_follows_the_layout_of_the_note():
    e = entry(documents=[{"label": "DRHP (PDF)", "url": "https://bse.test/drhp.pdf"}])
    text = render("drhp", params_for(e, SITE))
    wanted = ["New DRHP Filed!", "Company: Example Industries Limited", "Date: 25 Nov 2026", "Sector: Engineering",
              "Merchant banker: Example Capital Private Limited", "DRHP: https://bse.test/drhp.pdf",
              "Nuvont website: https://owner.github.io/Radar-Tracker/#v=filings&r=all&q=Example%20Industries%20Limited"]
    positions = [text.index(w) for w in wanted]
    assert positions == sorted(positions), text
    assert "not investment advice" in text


def test_missing_facts_are_written_plainly_never_left_empty():
    e = entry(filed_on="", info={}, sources={})
    values = params_for(e, "")
    assert values[1] == "26 Nov 2026 (detected)"
    assert values[2] == "Not stated yet" and values[3] == "Not stated yet"
    assert values[4] == "Not available yet" and values[5] == "Not available yet"
    assert all(v.strip() for v in values)


def test_document_link_preference_and_unsafe_links_are_dropped():
    both = entry(documents=[{"label": "x", "url": "https://bse.test/doc.pdf"}], official_url="https://sebi.test/p")
    assert params_for(both, SITE)[4] == "https://bse.test/doc.pdf"
    page = entry(official_url="https://sebi.test/p")
    assert params_for(page, SITE)[4] == "https://sebi.test/p"
    bad = entry(official_url="javascript:alert(1)", sources={"x": "data:text/html,boom"})
    assert params_for(bad, SITE)[4] == "Not available yet"


def test_template_values_are_one_clean_line():
    e = entry(company="Evil\nLine\tBreak   Ltd", info={"sector": "A\n\nB", "bankers": "X" * 400})
    values = params_for(e, SITE)
    for v in values:
        assert "\n" not in v and "\t" not in v and "     " not in v
    assert values[0] == "Evil Line Break Ltd" and len(values[3]) <= 200


def test_other_events_use_the_update_layout_with_the_event_first():
    values = params_for(entry(event="addendum"), SITE)
    assert len(values) == 7 and values[0] == "DRHP addendum" and values[1] == "Example Industries Limited"
    assert len(params_for(entry(), SITE)) == 6


def test_recipient_numbers_are_normalised_and_deduplicated():
    nums, bad = parse_recipients("+91 98765 43210, 09123456789;\n919876543210, 12345, ,+1 415 555 0100")
    assert nums == ["919876543210", "919123456789", "14155550100"] and bad == 1


def test_join_link_accepts_only_whatsapp_links():
    assert join_link({"WHATSAPP_JOIN_URL": "https://wa.me/919876543210?text=Hi"}).startswith("https://wa.me/")
    assert join_link({"WHATSAPP_JOIN_URL": "https://evil.test/wa.me/1"}) == ""
    assert join_link({"WHATSAPP_JOIN_URL": "javascript:alert(1)"}) == ""
    assert join_link({"WHATSAPP_JOIN_NUMBER": "+91 98765 43210"}).startswith("https://wa.me/919876543210?text=")
    assert join_link({}) == ""


# ---------------------------------------------------------------- delivery -----------------------------------------

def test_each_recipient_gets_one_template_message_and_nothing_repeats():
    meta, state = FakeMeta(), state_with(entry())
    wa = client(meta)
    info = wa.deliver(state, NOW, SITE)
    assert len(meta.sends) == 2 and info["last_run"]["sent"] == 2
    first = meta.sends[0]
    assert first["url"] == f"https://graph.facebook.com/v23.0/{PHONE_ID}/messages"
    assert first["headers"]["Authorization"] == f"Bearer {TOKEN}"
    body = first["json"]
    assert body["messaging_product"] == "whatsapp" and body["type"] == "template" and body["to"] == "919876543210"
    assert body["template"]["name"] == "new_drhp_filed" and body["template"]["language"] == {"code": "en"}
    params = body["template"]["components"][0]["parameters"]
    assert [p["type"] for p in params] == ["text"] * 6 and params[0]["text"] == "Example Industries Limited"
    wa.deliver(state, NOW + timedelta(hours=1), SITE)
    assert len(meta.sends) == 2  # not sent again


def test_state_and_diagnostics_hold_no_phone_numbers_or_token():
    state = state_with(entry())
    info = client().deliver(state, NOW, SITE)
    blob = json.dumps(state) + json.dumps(info)
    for secret in (TOKEN, PHONE_ID, "9876543210", "9123456789", "919876543210"):
        assert secret not in blob
    assert len(state["seen"][next(iter(state["seen"]))]["wa"]["ok"]) == 2


def test_only_fresh_alerted_filings_after_switch_on_are_sent():
    old = entry("Old Co Limited", first_seen=NOW - timedelta(hours=30))
    baseline = entry("Baseline Co Limited", baseline=True)
    backfill = entry("Backfill Co Limited", backfill=True)
    quiet = entry("Quiet Co Limited", alerted=False)
    sebi_only = entry("Other Co Limited", event="somethingelse")
    fresh = entry("Fresh Co Limited")
    meta = FakeMeta()
    client(meta).deliver(state_with(old, baseline, backfill, quiet, sebi_only, fresh), NOW, SITE)
    sent = {c["json"]["template"]["components"][0]["parameters"][0]["text"] for c in meta.sends}
    assert sent == {"Fresh Co Limited"}


def test_filings_from_before_whatsapp_was_switched_on_are_not_sent_late():
    state = state_with(entry("Early Co Limited", first_seen=NOW - timedelta(hours=3)))
    state["wa"] = {"since": NOW.isoformat(timespec="seconds")}
    meta = FakeMeta()
    client(meta).deliver(state, NOW, SITE)
    assert meta.sends == []


def test_events_use_their_own_template_and_a_missing_one_blocks_only_that_kind():
    state = state_with(entry("Draft Co Limited"), entry("Amended Co Limited", event="addendum"))
    meta = FakeMeta()
    client(meta, template_update="").deliver(state, NOW, SITE)
    assert {c["json"]["template"]["name"] for c in meta.sends} == {"new_drhp_filed"}
    meta2 = FakeMeta()
    client(meta2).deliver(state_with(entry("Amended Co Limited", event="addendum")), NOW, SITE)
    first = meta2.sends[0]["json"]["template"]
    assert first["name"] == "filing_update" and len(first["components"][0]["parameters"]) == 7


def test_nothing_is_sent_or_stored_when_unconfigured():
    meta, state = FakeMeta(), state_with(entry())
    for wa in (WhatsApp(session=meta), WhatsApp(token=TOKEN, phone_id=PHONE_ID, session=meta)):
        info = wa.deliver(state, NOW, SITE)
        assert "last_run" not in info
    assert meta.calls == [] and "wa" not in state and "wa" not in state["seen"][next(iter(state["seen"]))]
    assert WhatsApp(session=meta).status()["configured"] is False


def test_the_state_is_saved_after_each_alert():
    saves = []
    state = state_with(entry("A Co Limited"), entry("B Co Limited"))
    client().deliver(state, NOW, SITE, checkpoint=lambda: saves.append(1))
    assert len(saves) == 2


def test_a_per_run_cap_defers_the_rest_without_using_up_retries():
    state = state_with(entry("A Co Limited"), entry("B Co Limited"))
    meta = FakeMeta()
    wa = client(meta, max_per_run=3)
    wa.deliver(state, NOW, SITE)
    assert len(meta.sends) == 3
    wa.deliver(state, NOW + timedelta(hours=1), SITE)
    assert len(meta.sends) == 4
    assert all(e.get("wa", {}).get("tries", 0) == 0 for e in state["seen"].values())


# ---------------------------------------------------------------- errors -------------------------------------------

def test_an_unreachable_number_is_given_up_quietly_while_the_others_are_sent():
    def script(call):
        return error(131026) if call["json"]["to"] == "919876543210" else (200, {"messages": [{"id": "w"}]})
    tg, state, meta = FakeTG(), state_with(entry()), FakeMeta(script)
    wa = client(meta)
    info = wa.deliver(state, NOW, SITE, admin=tg)
    assert info["last_run"]["sent"] == 1 and info["last_run"]["undeliverable"] == 1
    wa.deliver(state, NOW + timedelta(hours=1), SITE, admin=tg)
    assert len(meta.sends) == 2  # the dead number is not tried again
    assert tg.admin == []  # an unreachable number is normal and not worth a note


def test_a_temporary_error_is_retried_next_run_then_given_up_after_the_limit():
    state, meta = state_with(entry()), FakeMeta(lambda call: error(131000))
    wa = client(meta, recipients="9876543210")
    for hour in range(MAX_TRIES + 2):
        wa.deliver(state, NOW + timedelta(minutes=10 * hour), SITE)
    e = next(iter(state["seen"].values()))
    assert len(meta.sends) == MAX_TRIES and e["wa"]["tries"] == MAX_TRIES and len(e["wa"]["dead"]) == 1


def test_a_recovering_send_after_a_temporary_error_succeeds_once():
    flaky = {"fail": True}

    def script(call):
        return error(131000) if flaky["fail"] else (200, {"messages": [{"id": "w"}]})
    state, meta = state_with(entry()), FakeMeta(script)
    wa = client(meta, recipients="9876543210")
    wa.deliver(state, NOW, SITE)
    flaky["fail"] = False
    info = wa.deliver(state, NOW + timedelta(hours=1), SITE)
    wa.deliver(state, NOW + timedelta(hours=2), SITE)
    assert info["last_run"]["sent"] == 1 and len(meta.sends) == 2


def test_throttling_pauses_the_whole_run():
    state, meta = state_with(entry("A Co Limited"), entry("B Co Limited")), FakeMeta(lambda call: error(130429, 429))
    info = client(meta).deliver(state, NOW, SITE)
    assert len(meta.sends) == 1 and info["last_run"]["errors"] == {"130429": 1}


def test_network_failure_is_a_pause_and_leaks_no_url():
    def script(call):
        raise requests.ConnectionError(f"failed for {call['url']} token {TOKEN}")
    state, meta = state_with(entry()), FakeMeta(script)
    wa = client(meta)
    info = wa.deliver(state, NOW, SITE)
    assert info["last_run"]["errors"] == {"network": 1} and TOKEN not in json.dumps(info)
    assert len(meta.sends) == 1


def test_a_bad_token_stops_everything_tells_the_admin_once_and_reports_recovery():
    ok = {"on": False}

    def script(call):
        return (200, {"messages": [{"id": "w"}]}) if ok["on"] else error(190, 401, "token expired")
    tg, state, meta = FakeTG(), state_with(entry()), FakeMeta(script)
    wa = client(meta)
    info = wa.deliver(state, NOW, SITE, admin=tg)
    assert len(meta.sends) == 1 and info["down"] == {"all": "190"}
    assert len(tg.admin) == 1 and "access token" in tg.admin[0] and "Telegram alerts are not affected" in tg.admin[0]
    assert TOKEN not in tg.admin[0]
    wa.deliver(state, NOW + timedelta(hours=1), SITE, admin=tg)
    assert len(tg.admin) == 1  # the same problem is not announced every hour
    assert next(iter(state["seen"].values()))["wa"]["tries"] == 0  # a config problem does not use up retries
    ok["on"] = True
    info = wa.deliver(state, NOW + timedelta(hours=2), SITE, admin=tg)
    assert info["last_run"]["sent"] == 2 and "down" not in info
    assert len(tg.admin) == 2 and "recovered" in tg.admin[1]


def test_a_template_problem_blocks_only_that_template():
    def script(call):
        if call["json"]["template"]["name"] == "new_drhp_filed":
            return error(132001, 400, "Template name does not exist in en")
        return 200, {"messages": [{"id": "w"}]}
    tg = FakeTG()
    state = state_with(entry("Draft Co Limited"), entry("Amended Co Limited", event="addendum"))
    meta = FakeMeta(script)
    info = client(meta).deliver(state, NOW, SITE, admin=tg)
    names = [c["json"]["template"]["name"] for c in meta.sends]
    assert names.count("new_drhp_filed") == 1 and names.count("filing_update") == 2
    assert info["down"] == {"drhp": "132001"} and "drhp template" in tg.admin[0]


def test_meta_error_text_in_the_admin_note_has_no_long_numbers():
    tg = FakeTG()
    meta = FakeMeta(lambda c: error(131009, 400, "Parameter invalid for 919876543210 and 109876543210987"))
    meta.script = lambda c: (400, {"error": {"code": 131009, "message": "x",
                                             "error_data": {"details": "bad value for 919876543210"}}})
    client(meta).deliver(state_with(entry()), NOW, SITE, admin=tg)
    assert tg.admin and "919876543210" not in tg.admin[0]


def test_a_crash_inside_whatsapp_never_escapes():
    class Boom(FakeMeta):
        def request(self, *a, **k):
            raise RuntimeError("unexpected")
    info = client(Boom()).deliver(state_with(entry()), NOW, SITE)
    assert info["error"] == "RuntimeError"


# ---------------------------------------------------------------- with Telegram and the page -----------------------

def test_telegram_alerting_is_untouched_when_whatsapp_fails():
    class TG(FakeTG):
        def __init__(self):
            super().__init__()
            self.channel = []

        def send_channel(self, text, reply_to=None):
            self.channel.append(text)
            return 100 + len(self.channel)
    tg, state = TG(), st.empty_state()
    seed = Record("bse_sme", "Seed Ltd", "drhp_filed", "sme", "BSE SME", "https://x/seed")
    fresh = Record("bse_sme", "Fresh Co Limited", "drhp_filed", "sme", "BSE SME", "https://x/fresh",
                   info={"sector": "Engineering", "bankers": "Example Capital"})
    radar_run.run_once([("bse_sme", lambda f: SourceResult("bse_sme", True, [seed]))], None, tg, state, NOW, {"sme"})
    later = NOW + timedelta(hours=1)
    radar_run.run_once([("bse_sme", lambda f: SourceResult("bse_sme", True, [seed, fresh]))], None, tg, state,
                       later, {"sme"})
    assert len(tg.channel) == 1
    meta = FakeMeta(lambda c: error(190, 401))
    client(meta).deliver(state, later, SITE, admin=tg)
    assert len(tg.channel) == 1
    entry_ = next(e for e in state["seen"].values() if e["company"] == "Fresh Co Limited")
    assert entry_["alerted"] and entry_["message_id"] == 101
    meta_ok = FakeMeta()
    client(meta_ok).deliver(state, later, SITE, admin=tg)
    sent = meta_ok.sends[0]["json"]["template"]["components"][0]["parameters"]
    assert sent[0]["text"] == "Fresh Co Limited" and sent[2]["text"] == "Engineering"


def test_the_page_gets_the_join_button_only_when_a_whatsapp_link_is_set(tmp_path):
    state = st.empty_state()
    out = tmp_path / "index.html"
    build_site(state, str(out), NOW, whatsapp_join="https://wa.me/919876543210?text=Hi")
    html = out.read_text(encoding="utf-8")
    assert '"whatsapp":{"join":"https://wa.me/919876543210?text=Hi"}' in html
    assert "Get WhatsApp alerts" in html and "Share on WhatsApp" in html
    build_site(state, str(out), NOW)
    assert '"whatsapp":null' in out.read_text(encoding="utf-8")
    build_site(state, str(out), NOW, whatsapp_join="javascript:alert(1)")
    assert '"whatsapp":null' in out.read_text(encoding="utf-8")


def test_template_text_matches_what_the_readme_tells_the_user_to_create():
    assert wa_mod.HEADER["drhp"] == "New DRHP Filed!"
    for kind, count in (("drhp", 6), ("update", 7)):
        body = wa_mod.BODY[kind]
        assert [f"{{{{{i}}}}}" in body for i in range(1, count + 1)] == [True] * count
        assert f"{{{{{count + 1}}}}}" not in body
        assert not body.startswith("{{") and not body.rstrip().endswith("}}")  # Meta forbids a variable at either end
        assert len(wa_mod.SAMPLES[kind]) == count


# ---------------------------------------------------------------- set-up helper ------------------------------------

def test_template_creation_sends_the_exact_text_with_examples_for_meta_review():
    meta = FakeMeta(lambda c: (200, {"id": "1", "status": "PENDING"}))
    wa = client(meta, business_id="555000111")
    assert wa.create_template("drhp").ok and wa.create_template("update").ok
    first = meta.calls[0]
    assert first["url"].endswith("/555000111/message_templates")
    payload = first["json"]
    assert payload["name"] == "new_drhp_filed" and payload["category"] == "UTILITY" and payload["language"] == "en"
    header, body, footer = payload["components"]
    assert header == {"type": "HEADER", "format": "TEXT", "text": "New DRHP Filed!"}
    assert body["text"] == wa_mod.BODY["drhp"] and len(body["example"]["body_text"][0]) == 6
    assert footer["text"] == wa_mod.FOOTER
    assert len(meta.calls[1]["json"]["components"][1]["example"]["body_text"][0]) == 7
    assert client(FakeMeta()).create_template("drhp").code == "no_business_id"


def test_template_status_reads_the_matching_template_only():
    data = {"data": [{"name": "other", "status": "REJECTED", "language": "en"},
                     {"name": "new_drhp_filed", "status": "APPROVED", "language": "en"}]}
    wa = client(FakeMeta(lambda c: (200, data)), business_id="1")
    assert wa.template_status("new_drhp_filed") == "APPROVED"
    assert wa.template_status("nope") == "MISSING"
    assert client(FakeMeta()).template_status("new_drhp_filed") == "UNKNOWN"  # no business account id given


def test_send_test_goes_to_the_first_recipient_only_and_checks_report_without_secrets(capsys):
    from radar import whatsapp_check as chk
    meta = FakeMeta()
    tg = FakeTG()
    tg.admin_chat = "1"
    assert chk.send_test(client(meta), tg) == 0
    assert {c["json"]["to"] for c in meta.sends} == {"919876543210"} and len(meta.sends) == 2
    assert "SAMPLE" in meta.sends[0]["json"]["template"]["components"][0]["parameters"][0]["text"]
    out = capsys.readouterr().out
    assert TOKEN not in out and "9876543210" not in out
    bad = FakeMeta(lambda c: error(131030, 400, "not allowed"))
    assert chk.send_test(client(bad), tg) == 1
    assert "allowed list" in capsys.readouterr().out
