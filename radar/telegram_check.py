"""Find out why posting to the channel fails, without printing anything private.

The Actions log of a public repo is public, so this prints only yes/no facts and Telegram's own error text.
Anything that identifies the channel (its id and name) is sent to the private admin chat instead."""
from __future__ import annotations

import os
import sys

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def call(token: str, method: str, **params) -> dict:
    try:
        r = requests.post(API.format(token=token, method=method), json=params, timeout=20)
        return r.json()
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "description": f"network error: {type(e).__name__}"}  # never the URL: it holds the token


def shape(value: str) -> str:
    v = value.strip()
    if not v:
        return "empty"
    if v.startswith("@"):
        return "a @username"
    if v.startswith("-100") and v[4:].isdigit():
        return "a numeric channel id (-100...)"
    if v.lstrip("-").isdigit():
        return "a numeric id WITHOUT the -100 prefix" if not v.startswith("-100") else "numeric"
    if "t.me" in v or "http" in v:
        return "a link, not an id"
    return "something else"


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    channel = os.getenv("TELEGRAM_CHANNEL", "")
    admin = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
    me = call(token, "getMe")
    print("bot token works:", bool(me.get("ok")))
    print("TELEGRAM_CHANNEL looks like:", shape(channel))
    chat = call(token, "getChat", chat_id=channel)
    print("getChat on TELEGRAM_CHANNEL:", "ok" if chat.get("ok") else f"FAILED ({chat.get('description')})")
    if chat.get("ok"):
        c = chat["result"]
        print("it is a:", c.get("type"))
        mem = call(token, "getChatMember", chat_id=channel, user_id=me["result"]["id"])
        r = mem.get("result", {}) if mem.get("ok") else {}
        print("bot status there:", r.get("status", f"unknown ({mem.get('description')})"),
              "| can post:", r.get("can_post_messages", "n/a"))

    # Channels the bot has actually been added to or seen a post in. Sent to the private admin chat only.
    upd = call(token, "getUpdates", allowed_updates=["channel_post", "my_chat_member"], timeout=0)
    seen = {}
    for u in upd.get("result", []) if upd.get("ok") else []:
        c = (u.get("channel_post") or u.get("my_chat_member") or {}).get("chat") or {}
        if c.get("type") == "channel":
            seen[c["id"]] = c.get("title", "")
    print("channels the bot has seen recently:", len(seen))
    if admin:
        if seen:
            body = "\n".join(f"{t or '(no title)'}  ->  {i}" for i, t in seen.items())
            text = ("Channels this bot can see (name -> id). TELEGRAM_CHANNEL must be exactly the id:\n\n" + body)
        else:
            text = ("The bot has not seen any channel recently. Post one message in the channel "
                    "(or remove and re-add the bot as admin) and run this check again.")
        call(token, "sendMessage", chat_id=admin, text=text)
        print("details sent to the admin chat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
