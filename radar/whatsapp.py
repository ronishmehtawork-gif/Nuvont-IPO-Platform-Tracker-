"""WhatsApp alerts through the Meta WhatsApp Business Cloud API.

It mirrors the Telegram channel: the same detected filings, each sent once, and a delivery problem never
blocks the radar. WhatsApp only lets a business start a conversation with a template that Meta has approved,
so the layout from the notification note is stored as two templates (README has the exact text) and filled
with each filing's facts here:

    New DRHP Filed!
    Company / Date / Sector / Merchant banker / DRHP link / Nuvont website link

Nothing here ever prints or stores a phone number or a token: the state and diagnostics files are public.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

from .models import ALERT_EVENTS, EVENT_LABELS

DEFAULT_VERSION = "v23.0"
GRAPH = "https://graph.facebook.com/{version}/{path}"
LANGUAGE = "en"
TEMPLATE_DRHP = "new_drhp_filed"
TEMPLATE_UPDATE = "filing_update"
WINDOW = timedelta(hours=24)  # an alert older than this is history, not news
MAX_TRIES = 6  # runs that may retry one alert (about six hours)
MAX_PER_RUN = 200  # messages in one radar run
NOT_STATED = "Not stated yet"
SITE_LABEL = "Nuvont website"
JOIN_TEXT = "Hi, please add me to the IPO Filing Radar WhatsApp alerts."

HEADER = {"drhp": "New DRHP Filed!", "update": "Filing update"}
FOOTER = "afc | ashmavir IPO Filing Radar"
_CLOSING = ("This is an automated alert from afc | ashmavir based on public filings. "
            "It is not investment advice.")
BODY = {
    "drhp": ("Company: {{1}}\nDate: {{2}}\nSector: {{3}}\nMerchant banker: {{4}}\nDRHP: {{5}}\n"
             f"{SITE_LABEL}: {{{{6}}}}\n\n{_CLOSING}"),
    "update": ("Update: {{1}}\nCompany: {{2}}\nDate: {{3}}\nSector: {{4}}\nMerchant banker: {{5}}\n"
               f"Document: {{{{6}}}}\n{SITE_LABEL}: {{{{7}}}}\n\n{_CLOSING}"),
}
SAMPLES = {
    "drhp": ["SAMPLE Example Industries Limited", "26 Nov 2026", "Engineering", "Example Capital Private Limited",
             "https://www.sebi.gov.in/example", "https://example.github.io/radar/#v=filings"],
    "update": ["DRHP addendum", "SAMPLE Example Industries Limited", "26 Nov 2026", "Engineering",
               "Example Capital Private Limited", "https://www.sebi.gov.in/example",
               "https://example.github.io/radar/#v=filings"],
}

# Where a Meta error code leads. "dead": this recipient can never get it. "retry": try again next run.
# "stop": Meta is throttling us, so pause the whole run. "template": that template is the problem.
# "all": the token, account or number is the problem, so nothing can be sent until it is fixed.
TEMPLATE_CODES = {"132000", "132001", "132005", "132007", "132012", "132015", "132016"}
ALL_CODES = {"190", "10", "100", "131031", "131042", "133005", "133010", "368"}
STOP_CODES = {"4", "17", "32", "613", "80007", "130429", "131048"}
DEAD_CODES = {"131026", "131030", "131021", "131049", "131008", "131009"}
QUIET_CODES = {"131026", "131030", "131021", "131049"}  # normal for a few recipients; not worth a Telegram note
REASONS = {
    "190": "the access token is invalid or has expired",
    "10": "the token lacks WhatsApp permission",
    "100": "Meta rejected the request, check the phone number ID",
    "200": "the token lacks WhatsApp permission",
    "368": "Meta has temporarily blocked sending for this account",
    "131031": "the WhatsApp Business account is locked",
    "131042": "payment method problem on the WhatsApp Business account",
    "133005": "the two-step verification PIN does not match",
    "133010": "the phone number is not registered with WhatsApp Business",
    "132000": "the template parameter count does not match the approved template",
    "132001": "the template does not exist or is not approved in this language",
    "132005": "the template text is too long",
    "132007": "the template text breaks a WhatsApp format rule",
    "132012": "a template parameter has the wrong format",
    "132015": "Meta has paused the template",
    "132016": "Meta has disabled the template",
    "131026": "the recipient cannot receive this message",
    "131030": "the recipient is not on the allowed list of the test number",
    "131021": "the recipient is the sending number itself",
    "131049": "Meta chose not to deliver this message to that recipient",
    "131008": "a required template parameter is missing",
    "131009": "a template parameter value is not valid",
    "131056": "too many messages to the same recipient in a short time",
    "131000": "Meta had a temporary problem",
    "131016": "Meta's service is temporarily unavailable",
    "131057": "the WhatsApp Business account is in maintenance",
    "130429": "sending too fast for the account's throughput",
    "80007": "the account's rate limit was reached",
    "131048": "Meta limited sending after spam reports",
    "network": "could not reach Meta",
}


# ---------------------------------------------------------------- numbers, ids, text -------------------------------

def parse_recipients(raw: str) -> tuple[list[str], int]:
    """'+91 98765 43210, 9123456789' -> (['919876543210', '919123456789'], invalid_count). Duplicates are dropped."""
    numbers, invalid = [], 0
    for part in re.split(r"[,;\n]+", raw or ""):
        digits = re.sub(r"\D", "", part)
        if not digits:
            continue
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 10:
            digits = "91" + digits  # an Indian mobile number written without the country code
        if 11 <= len(digits) <= 15:
            if digits not in numbers:
                numbers.append(digits)
        else:
            invalid += 1
    return numbers, invalid


def clean(text: object, limit: int = 200) -> str:
    """One line, no tabs or runs of spaces (WhatsApp rejects those in a template value), never empty."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    return t or NOT_STATED


def safe_link(url: object) -> str:
    u = str(url or "").strip()
    return u if re.match(r"^https?://\S+$", u) and len(u) <= 500 else ""


def pretty_date(iso: str) -> str:
    try:
        d = datetime.strptime((iso or "")[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{d.day} {d.strftime('%b %Y')}"


def site_link(site_url: str, company: str) -> str:
    """Link to the company's row on the website's Filings page (opens with the search already filled in)."""
    base = (site_url or "").strip().split("#")[0]
    if not base.startswith(("http://", "https://")):
        return ""
    return f"{base}#v=filings&r=all&q={quote(company or '', safe='')}"


def kind_of(entry: dict) -> str:
    return "drhp" if entry.get("event") == "drhp_filed" else "update"


def params_for(entry: dict, site_url: str = "") -> list[str]:
    """The template values for one filing, in the order of the note."""
    info = entry.get("info") or {}
    docs = entry.get("documents") or []
    first_source = next(iter((entry.get("sources") or {}).values()), "")
    doc = next((u for u in (safe_link(docs[0]["url"]) if docs else "", safe_link(entry.get("official_url")),
                            safe_link(first_source)) if u), "")
    when = pretty_date(entry.get("filed_on", ""))
    if not when:
        seen = pretty_date(entry.get("first_seen", ""))
        when = f"{seen} (detected)" if seen else NOT_STATED
    values = [
        clean(entry.get("company"), 150),
        clean(when),
        clean(info.get("sector"), 100),
        clean(info.get("bankers"), 200),
        doc or "Not available yet",
        site_link(site_url, entry.get("company", "")) or "Not available yet",
    ]
    if kind_of(entry) == "update":
        values.insert(0, EVENT_LABELS.get(entry.get("event"), "Filing update"))
    return values


def render(kind: str, values: list[str]) -> str:
    """The message as a person will read it. Used for previews; WhatsApp builds the real one from the template."""
    body = BODY[kind]
    for i, v in enumerate(values, 1):
        body = body.replace("{{%d}}" % i, v)
    return f"{HEADER[kind]}\n\n{body}\n\n{FOOTER}"


def join_link(env=None) -> str:
    """Public link for the website's 'Get WhatsApp alerts' button. Empty when nothing valid is configured."""
    env = os.environ if env is None else env
    url = (env.get("WHATSAPP_JOIN_URL") or "").strip()
    if url:
        ok = re.match(r"^https://(wa\.me/|api\.whatsapp\.com/|chat\.whatsapp\.com/|(www\.)?whatsapp\.com/channel/)"
                      r"[^\s\"'<>]+$", url)
        return url if ok else ""
    digits = re.sub(r"\D", "", env.get("WHATSAPP_JOIN_NUMBER") or "")
    if len(digits) == 10:
        digits = "91" + digits
    return f"https://wa.me/{digits}?text={quote(JOIN_TEXT)}" if 11 <= len(digits) <= 15 else ""


# ---------------------------------------------------------------- the Meta API --------------------------------------

@dataclass
class Result:
    scope: str = "ok"  # ok | dead | retry | stop | template | all
    code: str = ""
    reason: str = ""
    detail: str = ""  # Meta's own words, only ever shown in the private admin chat
    message_id: str = ""

    @property
    def ok(self) -> bool:
        return self.scope == "ok"


def _scope(status: int, code: str) -> str:
    if code in TEMPLATE_CODES:
        return "template"
    if code in ALL_CODES or status in (401, 403) or (code.isdigit() and 200 <= int(code) <= 299):
        return "all"
    if code in STOP_CODES or status == 429:
        return "stop"
    if code in DEAD_CODES:
        return "dead"
    return "retry"  # includes unknown codes and 5xx; the retry limit ends it if it never clears


def _result_from(status: int, body: dict) -> Result:
    err = body.get("error") or {}
    code = str(err.get("code") or "") or f"http{status}"
    detail = ((err.get("error_data") or {}).get("details") or err.get("message") or "")
    detail = re.sub(r"\d{7,}", "…", str(detail))[:200]  # no phone numbers or ids, even in the admin chat
    reason = REASONS.get(code) or (REASONS["200"] if code.isdigit() and 200 <= int(code) <= 299 else f"Meta error {code}")
    return Result(_scope(status, code), code, reason, detail)


class WhatsApp:
    """Sends templates to a list of recipients. Without a token, phone number ID and recipients it does nothing."""

    def __init__(self, token="", phone_id="", recipients="", template_drhp=TEMPLATE_DRHP,
                 template_update=TEMPLATE_UPDATE, business_id="", version=DEFAULT_VERSION, pause=0.4,
                 max_per_run=MAX_PER_RUN, session=None):
        self.token = (token or "").strip()
        self.phone_id = re.sub(r"\D", "", phone_id or "")
        self.business_id = re.sub(r"\D", "", business_id or "")
        self.recipients, self.invalid = parse_recipients(recipients)
        self.template = {"drhp": (template_drhp or "").strip(), "update": (template_update or "").strip()}
        self.version = (version or DEFAULT_VERSION).strip()
        self.pause = pause
        self.max_per_run = max_per_run
        self.session = session or requests.Session()
        self._ids: dict[str, str] = {}

    @classmethod
    def from_env(cls, env=None) -> "WhatsApp":
        env = os.environ if env is None else env
        return cls(
            token=env.get("WHATSAPP_TOKEN", ""),
            phone_id=env.get("WHATSAPP_PHONE_ID", ""),
            recipients=env.get("WHATSAPP_RECIPIENTS", ""),
            template_drhp=env.get("WHATSAPP_TEMPLATE_DRHP") or TEMPLATE_DRHP,
            template_update=env.get("WHATSAPP_TEMPLATE_UPDATE") or TEMPLATE_UPDATE,
            business_id=env.get("WHATSAPP_BUSINESS_ID", ""),
            version=env.get("WHATSAPP_API_VERSION") or DEFAULT_VERSION,
        )

    @property
    def configured(self) -> bool:
        return bool(self.token and self.phone_id)

    @property
    def enabled(self) -> bool:
        return self.configured and bool(self.recipients)

    def rid(self, number: str) -> str:
        """Short keyed hash that stands in for a phone number in the public state file. The key is the token,
        so the numbers cannot be recovered from it by anyone who only sees the repository."""
        if number not in self._ids:
            self._ids[number] = hmac.new(self.token.encode(), number.encode(), hashlib.sha256).hexdigest()[:12]
        return self._ids[number]

    # -------- HTTP
    def _call(self, method: str, path: str, payload: dict | None = None, params: dict | None = None):
        url = GRAPH.format(version=self.version, path=path)
        try:
            r = self.session.request(method, url, json=payload, params=params, timeout=20,
                                     headers={"Authorization": f"Bearer {self.token}"})
        except requests.RequestException as e:
            # Never print the exception text: it can carry the request URL.
            print(f"[whatsapp] network error: {type(e).__name__}", file=sys.stderr)
            return None, {}
        try:
            body = r.json()
        except ValueError:
            body = {}
        return r.status_code, body if isinstance(body, dict) else {}

    def send_template(self, to: str, kind: str, values: list[str]) -> Result:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": self.template[kind],
                "language": {"code": LANGUAGE},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": v} for v in values]}],
            },
        }
        status, body = self._call("POST", f"{self.phone_id}/messages", payload)
        if status is None:
            return Result("stop", "network", REASONS["network"])
        if status < 300 and body.get("messages"):
            return Result("ok", message_id=str((body["messages"][0] or {}).get("id") or ""))
        return _result_from(status, body)

    # -------- checks and setup helpers (used by whatsapp_check.py)
    def verify(self) -> Result:
        status, body = self._call("GET", self.phone_id, params={"fields": "verified_name,quality_rating"})
        if status is None:
            return Result("stop", "network", REASONS["network"])
        if status < 300 and body.get("id"):
            return Result("ok", detail=f"{body.get('verified_name', '')} · quality {body.get('quality_rating', '?')}")
        return _result_from(status, body)

    def template_status(self, name: str) -> str:
        """APPROVED, PENDING, REJECTED, PAUSED..., 'MISSING', or 'UNKNOWN' when the account id is not set."""
        if not self.business_id:
            return "UNKNOWN"
        status, body = self._call("GET", f"{self.business_id}/message_templates",
                                  params={"name": name, "fields": "name,status,category,language"})
        if status is None or status >= 300:
            return "UNKNOWN"
        for t in body.get("data") or []:
            if t.get("name") == name and t.get("language", LANGUAGE).split("_")[0] == LANGUAGE:
                return str(t.get("status") or "UNKNOWN")
        return "MISSING"

    def create_template(self, kind: str) -> Result:
        if not self.business_id:
            return Result("all", "no_business_id", "WHATSAPP_BUSINESS_ID is not set")
        payload = {
            "name": self.template[kind],
            "language": LANGUAGE,
            "category": "UTILITY",
            "components": [
                {"type": "HEADER", "format": "TEXT", "text": HEADER[kind]},
                {"type": "BODY", "text": BODY[kind], "example": {"body_text": [SAMPLES[kind]]}},
                {"type": "FOOTER", "text": FOOTER},
            ],
        }
        status, body = self._call("POST", f"{self.business_id}/message_templates", payload)
        if status is None:
            return Result("stop", "network", REASONS["network"])
        if status < 300 and body.get("id"):
            return Result("ok", detail=str(body.get("status") or "PENDING"))
        return _result_from(status, body)

    # -------- delivery
    def status(self) -> dict:
        return {"configured": self.configured, "recipients": len(self.recipients),
                "templates": {"drhp": bool(self.template["drhp"]), "update": bool(self.template["update"])}}

    def deliver(self, state: dict, now: datetime, site_url: str = "", admin=None, checkpoint=None) -> dict:
        """Send every alert that still has recipients waiting. Never raises, and never touches Telegram's fields.
        checkpoint() is called after each alert so the caller can save the state."""
        try:
            return self._deliver(state, now, site_url, admin, checkpoint)
        except Exception as e:  # noqa: BLE001 - WhatsApp must never stop the radar
            print(f"[whatsapp] unexpected error: {type(e).__name__}", file=sys.stderr)
            return {**self.status(), "error": type(e).__name__}

    def _eligible(self, entry: dict, since: datetime, now: datetime) -> bool:
        if not entry.get("alerted") or entry.get("baseline") or entry.get("backfill"):
            return False
        if entry.get("event") not in ALERT_EVENTS:
            return False
        seen = _when(entry.get("first_seen"))
        return bool(seen and seen >= since and now - seen <= WINDOW)

    def _deliver(self, state, now, site_url, admin, checkpoint) -> dict:
        info = self.status()
        if not self.enabled:
            return info
        stamp = now.isoformat(timespec="seconds")
        ws = state.setdefault("wa", {})
        ws.setdefault("since", stamp)  # alerts from before WhatsApp was switched on are never sent late
        since = _when(ws["since"]) or now
        down = ws.setdefault("down", {})  # scope -> error code that stopped it, for the admin notes
        counts = {"eligible": 0, "sent": 0, "retry": 0, "undeliverable": 0, "gave_up": 0, "blocked": 0}
        errors: dict[str, int] = {}
        notes: dict[str, str] = {}
        blocked: set[str] = set()  # template kinds that failed this run
        delivered: set[str] = set()  # template kinds that got a message through this run
        stop = fatal = False
        budget = self.max_per_run

        for key, entry in sorted(state["seen"].items(), key=lambda kv: kv[1].get("first_seen", "")):
            if stop:
                break
            if not self._eligible(entry, since, now):
                continue
            kind = kind_of(entry)
            wa = entry.get("wa") or {}
            finished = set(wa.get("ok", [])) | set(wa.get("dead", []))
            pending = [(n, self.rid(n)) for n in self.recipients if self.rid(n) not in finished]
            if not pending:
                continue
            if kind in blocked or not self.template[kind]:
                counts["blocked"] += 1
                continue
            counts["eligible"] += 1
            wa = entry.setdefault("wa", {"ok": [], "dead": [], "tries": 0})
            values = params_for(entry, site_url)
            retry_left = False
            for number, rid in pending:
                if budget <= 0:
                    stop = True
                    break
                res = self.send_template(number, kind, values)
                budget -= 1
                if res.ok:
                    wa["ok"].append(rid)
                    delivered.add(kind)
                    counts["sent"] += 1
                    time.sleep(self.pause)
                    continue
                errors[res.code] = errors.get(res.code, 0) + 1
                if res.code not in QUIET_CODES:
                    notes[res.code] = f"{res.reason}" + (f" ({res.detail})" if res.detail else "")
                if res.scope == "dead":
                    wa["dead"].append(rid)
                    counts["undeliverable"] += 1
                elif res.scope == "retry":
                    retry_left = True
                    counts["retry"] += 1
                elif res.scope == "stop":
                    retry_left = True
                    counts["retry"] += 1
                    stop = True
                    break
                elif res.scope == "template":
                    blocked.add(kind)
                    down[kind] = res.code
                    break
                else:  # "all"
                    fatal = stop = True
                    down["all"] = res.code
                    break
            if retry_left and not fatal and kind not in blocked:
                wa["tries"] = wa.get("tries", 0) + 1
                if wa["tries"] >= MAX_TRIES:
                    left = [r for _, r in pending if r not in wa["ok"] and r not in wa["dead"]]
                    wa["dead"].extend(left)
                    counts["gave_up"] += len(left)
            if checkpoint:
                checkpoint()  # a killed run must not resend what already went out

        cleared = [scope for scope in list(down)
                   if (scope in delivered and scope not in blocked) or (scope == "all" and delivered and not fatal)]
        for scope in cleared:
            down.pop(scope)
        if cleared and not down and admin is not None and getattr(admin, "enabled", False):
            admin.send_admin("<b>WhatsApp delivery recovered</b>\nAlerts are going out again.")
        self._tell_admin(ws, down, notes, admin)
        info["last_run"] = {k: v for k, v in counts.items() if v or k in ("eligible", "sent")}
        if errors:
            info["last_run"]["errors"] = dict(sorted(errors.items()))
        if down:
            info["down"] = dict(sorted(down.items()))
        if self.invalid:
            info["invalid_recipients"] = self.invalid
        return info

    def _tell_admin(self, ws: dict, down: dict, notes: dict, admin) -> None:
        """One short note per new problem to the private admin chat, never repeated every run."""
        if admin is None or not getattr(admin, "enabled", False):
            return
        from .telegram import esc  # local import: telegram.py has no need of this module
        alerted = ws.setdefault("down_alerted", {})
        noted = ws.setdefault("noted", [])
        lines, reported = [], set()
        for scope, code in sorted(down.items()):
            reported.add(code)
            if alerted.get(scope) != code:
                alerted[scope] = code
                what = "all messages are" if scope == "all" else f"the {scope} template is"
                lines.append(f"• {what} blocked: {esc(notes.get(code) or REASONS.get(code, 'Meta error ' + code))}"
                             f" (code {esc(code)})")
        for scope in [s for s in alerted if s not in down]:
            alerted.pop(scope)
        for code, text in sorted(notes.items()):
            if code not in reported and code not in noted:
                noted.append(code)
                del noted[:-20]
                lines.append(f"• code {esc(code)}: {esc(text)}")
        if lines:
            text = "<b>WhatsApp delivery problem</b>\n" + "\n".join(lines[:5])
            text += "\nTelegram alerts are not affected."
            if any(c in down.values() for c in reported):
                text += " Alerts are retried for up to 24 hours once this is fixed."
            admin.send_admin(text)


def _when(iso: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(iso))
    except ValueError:
        return None
