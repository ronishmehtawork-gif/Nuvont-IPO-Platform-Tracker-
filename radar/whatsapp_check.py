"""Manual WhatsApp set-up helper, run from the 'whatsapp-check' workflow.

  preview           send both message layouts, filled with sample facts, to the private Telegram admin chat.
                    Needs no Meta account, so the layout can be agreed before anything is set up.
  status            check the token and phone number ID, and the approval status of both templates.
  create_templates  submit both templates to Meta for approval (the exact text is in radar/whatsapp.py).
  send_test         send both templates, with SAMPLE facts, to the first recipient only.

The public workflow log only gets short yes/no lines. Anything more goes to the private admin chat."""
from __future__ import annotations

import os
import sys

from .telegram import Telegram, esc
from .whatsapp import BODY, HEADER, SAMPLES, WhatsApp, join_link, render


def _admin(tg: Telegram, text: str) -> None:
    if tg.enabled and tg.admin_chat:
        tg.send_admin(text)


def preview(tg: Telegram) -> int:
    if not (tg.enabled and tg.admin_chat):
        print("Telegram admin secrets are missing, so the preview cannot be sent.", file=sys.stderr)
        return 1
    for kind, title in (("drhp", "New DRHP filed"), ("update", "Other filing events")):
        _admin(tg, f"<b>WhatsApp preview: {esc(title)}</b>\n<i>Sample facts. This is the text WhatsApp users will see.</i>"
                   f"\n\n<pre>{esc(render(kind, SAMPLES[kind]))}</pre>")
    print("Preview sent to the admin chat.")
    return 0


def status(wa: WhatsApp, tg: Telegram) -> int:
    lines, ok = [], True
    if not wa.configured:
        print("WHATSAPP_TOKEN and WHATSAPP_PHONE_ID are not both set.", file=sys.stderr)
        return 1
    res = wa.verify()
    print("token and phone number ID:", "ok" if res.ok else f"FAILED ({res.reason})")
    lines.append("• Phone number: " + (esc(res.detail) if res.ok else "FAILED, " + esc(res.reason)))
    ok &= res.ok
    print(f"recipients: {len(wa.recipients)} valid, {wa.invalid} not usable")
    lines.append(f"• Recipients: {len(wa.recipients)} valid, {wa.invalid} not usable")
    for kind in ("drhp", "update"):
        name = wa.template[kind]
        state = wa.template_status(name) if name else "NOT SET"
        print(f"template {kind}: {state}")
        lines.append(f"• Template {esc(name or '-')} ({kind}): {esc(state)}")
        if kind == "drhp":
            ok &= state in ("APPROVED", "UNKNOWN")
    print("website join link:", "set" if join_link() else "not set")
    lines.append("• Website join button: " + ("on" if join_link() else "off (WHATSAPP_JOIN_NUMBER or WHATSAPP_JOIN_URL not set)"))
    _admin(tg, "<b>WhatsApp status</b>\n" + "\n".join(lines))
    return 0 if ok else 1


def create_templates(wa: WhatsApp, tg: Telegram) -> int:
    if not wa.business_id:
        print("WHATSAPP_BUSINESS_ID (the WhatsApp Business Account ID) is not set.", file=sys.stderr)
        return 1
    failed, lines = False, []
    for kind in ("drhp", "update"):
        if not wa.template[kind]:
            continue
        res = wa.create_template(kind)
        print(f"template {wa.template[kind]}:", f"submitted ({res.detail})" if res.ok else f"FAILED ({res.reason})")
        lines.append(f"• {esc(wa.template[kind])}: " + (f"submitted, status {esc(res.detail)}" if res.ok
                                                       else "FAILED, " + esc(res.reason)
                                                       + (f" ({esc(res.detail)})" if res.detail else "")))
        failed |= not res.ok
    _admin(tg, "<b>WhatsApp templates</b>\n" + "\n".join(lines) + "\nMeta usually answers within minutes to a day.")
    return 1 if failed else 0


def send_test(wa: WhatsApp, tg: Telegram) -> int:
    if not wa.enabled:
        print("Token, phone number ID and at least one recipient are needed.", file=sys.stderr)
        return 1
    to, failed, lines = wa.recipients[0], False, []
    for kind in ("drhp", "update"):
        if not wa.template[kind]:
            continue
        res = wa.send_template(to, kind, SAMPLES[kind])
        print(f"test {kind}:", "sent" if res.ok else f"FAILED ({res.reason})")
        lines.append(f"• {kind}: " + ("sent" if res.ok else "FAILED, " + esc(res.reason)
                                      + (f" ({esc(res.detail)})" if res.detail else "")))
        failed |= not res.ok
    _admin(tg, "<b>WhatsApp test message</b>\nSent to the first recipient only.\n" + "\n".join(lines))
    return 1 if failed else 0


def main() -> int:
    action = (os.getenv("WA_ACTION") or "preview").strip()
    tg = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHANNEL", ""),
                  os.getenv("TELEGRAM_ADMIN_CHAT_ID", ""))
    wa = WhatsApp.from_env()
    if action == "preview":
        return preview(tg)
    if action == "status":
        return status(wa, tg)
    if action == "create_templates":
        return create_templates(wa, tg)
    if action == "send_test":
        return send_test(wa, tg)
    print(f"Unknown action: {action}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
