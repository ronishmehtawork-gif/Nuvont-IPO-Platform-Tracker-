from __future__ import annotations

import html
import sys
import time
from datetime import datetime

import requests

from .models import EVENT_LABELS, label

API = "https://api.telegram.org/bot{token}/{method}"


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


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
            print(f"[telegram] HTTP {r.status_code}: {desc}", file=sys.stderr)
            return None
        return None


def _sources_line(entry: dict) -> str:
    return ", ".join(label(s) for s in entry["sources"])


def format_alert(entry: dict, now: datetime) -> str:
    seg = {"sme": "", "mainboard": "Mainboard", "unknown": "Segment not confirmed"}[entry["segment"]]
    where = entry["exchange"] or seg or "SME"
    lines = [
        f"<b>{EVENT_LABELS[entry['event']]}</b>",
        f"Company: <b>{esc(entry['company'])}</b>",
        f"Where: {esc(where)}",
    ]
    if entry.get("filed_on"):
        lines.append(f"Filed: {esc(entry['filed_on'])}")
    lines.append(f"First seen on: {esc(_sources_line(entry))}")
    url = entry.get("official_url")
    docs = entry.get("documents") or []
    for d in docs:
        lines.append(f'<a href="{esc(d["url"])}">Download: {esc(d["label"])}</a>')
    first = next(iter(entry["sources"].values()))
    if url and docs:
        lines.append(f'<a href="{esc(url)}">{esc(_page_label(url))}</a>')
    elif url:
        lines.append(f'<a href="{esc(url)}">Open the official document</a>')
    elif docs:
        lines.append(f'<a href="{esc(first)}">More details</a>')
    else:
        lines.append(f'<a href="{esc(first)}">Open the report</a>')
        lines.append("Official document link will follow once it is found.")
    lines.append(f"<i>Detected {now.strftime('%d %b %Y, %I:%M %p')} IST</i>")
    return "\n".join(lines)


def _page_label(url: str) -> str:
    return "SEBI filing page" if "sebi.gov.in" in url else "Official page"


def format_official_update(entry: dict) -> str:
    docs = entry.get("documents") or []
    url = entry.get("official_url")
    lines = [
        "<b>Official document found</b>" if url else "<b>Download link found</b>",
        esc(entry["company"]),
    ]
    for d in docs:
        lines.append(f'<a href="{esc(d["url"])}">Download: {esc(d["label"])}</a>')
    if url:
        lines.append(f'<a href="{esc(url)}">{esc(_page_label(url)) if docs else "Open the official document"}</a>')
    return "\n".join(lines)


def format_digest(entries: list, site_url: str = "") -> str:
    lines = [f"<b>{len(entries)} more filings detected</b>"]
    for e in entries[:30]:
        lines.append(f"- {esc(e['company'])} ({esc(e['exchange'] or e['segment'])})")
    if len(entries) > 30:
        lines.append(f"...and {len(entries) - 30} more")
    if site_url:
        lines.append(f'<a href="{esc(site_url)}">See the full list</a>')
    return "\n".join(lines)
