from __future__ import annotations

import html
import re
import sys
import time
from datetime import datetime

import requests

from .models import EVENT_LABELS, label

API = "https://api.telegram.org/bot{token}/{method}"


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def attr(text: str) -> str:
    """For use inside href="...": quotes must be escaped too."""
    return html.escape(text or "", quote=True)


def _plain(text: str) -> str:
    """Telegram-HTML to plain text, used only if Telegram rejects the formatting."""
    text = re.sub(r"<a [^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", r"\2 (\1)", text, flags=re.S)
    return html.unescape(re.sub(r"</?[a-z][^>]*>", "", text))


class Telegram:
    """Posts to a public channel and a private admin chat. Without a token it only prints."""

    def __init__(self, token: str, channel: str, admin_chat: str, pause: float = 1.2):
        self.token = token
        self.channel = channel
        self.admin_chat = admin_chat
        self.pause = pause
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def send_channel(self, text: str, reply_to: int | None = None) -> int | None:
        return self._send(self.channel, text, reply_to)

    def send_admin(self, text: str) -> int | None:
        return self._send(self.admin_chat, text)

    def _send(self, chat_id: str, text: str, reply_to: int | None = None) -> int | None:
        if not self.enabled or not chat_id:
            print(f"[dry-run -> {chat_id or 'unset'}]\n{text}\n", file=sys.stderr)
            return None
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_to:
            payload["reply_parameters"] = {
                "message_id": reply_to,
                "allow_sending_without_reply": True,
            }
        url = API.format(token=self.token, method="sendMessage")
        for _ in range(3):
            try:
                r = self.session.post(url, json=payload, timeout=20)
            except requests.RequestException as e:
                # Never print the exception text: it can contain the URL, and the URL holds the token.
                print(f"[telegram] network error: {type(e).__name__}", file=sys.stderr)
                return None
            if r.status_code == 429:
                wait = r.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(min(int(wait) + 1, 60))
                continue
            if r.ok:
                time.sleep(self.pause)
                return r.json()["result"]["message_id"]
            desc = ""
            try:
                desc = r.json().get("description", "")
            except ValueError:
                pass
            if r.status_code == 400 and "parse entities" in desc and payload.get("parse_mode"):
                # Never lose an alert over formatting: send the same content as plain text.
                payload.pop("parse_mode")
                payload["text"] = _plain(text)
                continue
            print(f"[telegram] HTTP {r.status_code}: {desc}", file=sys.stderr)
            return None
        return None


ICONS = {"drhp_filed": "🆕", "drhp_refiled": "🔁", "approval": "✅", "issue": "🚀", "addendum": "📎"}

NEXT_STEP = {
    "drhp_filed": "Next: review by {who}. Public comments are usually open for 21 days.",
    "drhp_refiled": "An updated draft has been filed and replaces the earlier one.",
    "approval": "Approval received. The company can now file its final prospectus and set the issue dates.",
    "issue": "Issue news such as dates, price band or subscription. Open the report for details.",
    "addendum": "An addendum to the draft offer document has been filed. It updates the existing DRHP.",
}


def _sources_line(entry: dict) -> str:
    return ", ".join(label(s) for s in entry["sources"])


def _where(entry: dict) -> str:
    exchange, seg = entry.get("exchange") or "", entry["segment"]
    if seg == "mainboard":
        return f"Mainboard · {exchange}" if exchange else "Mainboard"
    if seg == "unknown":
        return f"Segment not confirmed · {exchange}" if exchange else "Segment not confirmed"
    return exchange or "SME"


def _date_line(iso: str, now: datetime, what: str = "DRHP filed") -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return f"{what} {esc(iso)}"
    text = f"{d.day} {d.strftime('%b %Y')}"
    days = (now.date() - d).days
    ago = {0: " (today)", 1: " (yesterday)"}.get(days, f" ({days} days ago)" if days > 1 else "")
    return f"{what} {text}{ago}"


def _page_label(url: str) -> str:
    return "SEBI filing page" if "sebi.gov.in" in url else "Official page"


def format_alert(entry: dict, now: datetime) -> str:
    event = entry["event"]
    info = entry.get("info") or {}
    docs = entry.get("documents") or []
    url = entry.get("official_url")
    first = next(iter(entry["sources"].values()))

    lines = [f"{ICONS.get(event, '🔔')} <b>{EVENT_LABELS[event]}</b> · {esc(_where(entry))}", "",
             f"<b>{esc(entry['company'])}</b>"]
    tagline = " · ".join(x for x in (info.get("sector"), info.get("city")) if x)
    if tagline:
        lines.append(f"<i>{esc(tagline)}</i>")
    facts = []
    if entry.get("filed_on"):
        what = "Addendum filed" if event == "addendum" else "DRHP filed"
        facts.append("📅 " + _date_line(entry["filed_on"], now, what))
    if info.get("bankers"):
        facts.append("🏦 Lead manager: " + esc(info["bankers"]))
    if facts:
        lines += [""] + facts

    links = [f'📥 <a href="{attr(d["url"])}">Download · {esc(d["label"])}</a>' for d in docs]
    if url and docs:
        links.append(f'🔗 <a href="{attr(url)}">{esc(_page_label(url))}</a>')
    elif url:
        links.append(f'🔗 <a href="{attr(url)}">Open the official document</a>')
    elif docs:
        links.append(f'🔗 <a href="{attr(first)}">More details</a>')
    else:
        links.append(f'🔗 <a href="{attr(first)}">Open the report</a>')
    lines += [""] + links
    if not url and not docs:
        lines.append("Official document link will follow once it is found.")

    who = {"sme": "the exchange", "mainboard": "SEBI and the exchange"}.get(entry["segment"], "the regulator")
    lines += ["", f"<blockquote>{esc(NEXT_STEP[event].format(who=who))}</blockquote>"]
    stamp = f"{now.day} {now.strftime('%b')}, {now.hour % 12 or 12}:{now.strftime('%M %p')}"
    lines.append(f"<i>via {esc(_sources_line(entry))} · detected {stamp} IST</i>")
    return "\n".join(lines)


def format_official_update(entry: dict) -> str:
    docs = entry.get("documents") or []
    url = entry.get("official_url")
    head = "🔗 <b>Official document found</b>" if url else "📥 <b>Download link found</b>"
    lines = [head, f"<b>{esc(entry['company'])}</b>", ""]
    lines += [f'📥 <a href="{attr(d["url"])}">Download · {esc(d["label"])}</a>' for d in docs]
    if url:
        text = _page_label(url) if docs else "Open the official document"
        lines.append(f'🔗 <a href="{attr(url)}">{esc(text)}</a>')
    return "\n".join(lines)


_TAG = {"drhp_filed": "DRHP", "drhp_refiled": "Refiled", "approval": "Approved", "issue": "Issue", "addendum": "Addendum"}


def format_digest(entries: list, site_url: str = "") -> str:
    lines = [f"📚 <b>{len(entries)} more filings detected</b>"]
    for title, seg in (("SME", "sme"), ("Mainboard", "mainboard"), ("Segment not confirmed", "unknown")):
        group = [e for e in entries if e["segment"] == seg][:20]
        if not group:
            continue
        lines += ["", f"<b>{title}</b>"]
        for e in group:
            extra = " · ".join(x for x in (e.get("exchange"), _TAG.get(e["event"])) if x)
            lines.append(f"• {esc(e['company'])} ({esc(extra)})")
    if len(entries) > 60:
        lines.append(f"...and more in the full list")
    if site_url:
        lines += ["", f'🔗 <a href="{attr(site_url)}">See the full list</a>']
    return "\n".join(lines)
