"""Build the afc | ashmavir IPO Filing Radar dashboard into docs/index.html.

The page is one static file: the data travels inside it as JSON and a small script draws the filters, charts and
tables in the browser. Nothing is fetched at view time, so the page makes no network calls of its own."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from .models import label

TEMPLATE = Path(__file__).with_name("dashboard.html")
STATUS_ORDER = ["filed", "refiled", "approved", "addendum", "issue"]
STATUS_OF = {
    "drhp_filed": "filed", "drhp_refiled": "refiled", "approval": "approved", "addendum": "addendum", "issue": "issue",
}


def _safe_url(url: str) -> str:
    return url if isinstance(url, str) and url.startswith(("https://", "http://")) else ""


def _best_name(names: list[str]) -> str:
    return max(names, key=lambda n: (not n.isupper(), n.lower().endswith("limited"), len(n)))


def _doc_title(doc_label: str, url: str) -> str:
    low = f"{doc_label} {url}".lower()
    if "abridged" in low:
        return "Abridged prospectus (PDF)"
    if "addend" in low:
        return "Addendum (PDF)"
    if ".zip" in low or "zip" in low:
        return "DRHP (ZIP)"
    if ".pdf" in low or "pdf" in low:
        return "DRHP (PDF)"
    return "Document"


def _page_link(entries: list[dict]) -> tuple[str, str]:
    for e in entries:
        if _safe_url(e.get("official_url", "")):
            url = e["official_url"]
            return ("SEBI page" if "sebi.gov.in" in url else "Exchange page"), url
    for e in entries:
        for src, url in (e.get("sources") or {}).items():
            if _safe_url(url):
                return label(src), url
    return "", ""


def _bankers(text: str) -> list[str]:
    """IPO Platform lists the first two managers and then '+N more'; keep the names, drop the tail."""
    text = re.sub(r"\s*\+\s*\d+\s*more\s*$", "", text or "").strip()
    names = []
    for part in text.split(","):
        name = re.sub(r"\s+(Private Limited|Pvt\.? Ltd\.?|Limited|Ltd\.?)$", "", part.strip(), flags=re.I)
        if name:
            names.append(name)
    return names


def _segment(entries: list[dict]) -> str:
    segs = {e.get("segment") for e in entries}
    if "mainboard" in segs:
        return "M"
    if "sme" in segs:
        return "S"
    return "U"


def build_companies(state: dict) -> list[dict]:
    """One row per company: its filings, approval status, documents and listing details, merged across sources."""
    groups: dict[str, list[dict]] = {}
    for key, entry in state.get("seen", {}).items():
        groups.setdefault(key.split("|")[0], []).append(entry)

    rows = []
    for entries in groups.values():
        statuses = {STATUS_OF[e["event"]] for e in entries if e.get("event") in STATUS_OF}
        info: dict = {}
        for e in entries:
            for k, v in (e.get("info") or {}).items():
                if v and not info.get(k):
                    info[k] = v
        exchanges = [e.get("exchange") for e in entries if e.get("exchange")]
        # "SEBI" is where a filing was found, not where the shares will list, so it never counts as an exchange.
        exchange = next((x for x in exchanges if x != "SEBI"), "")
        docs, seen_urls = [], set()
        for e in entries:
            for d in e.get("documents") or []:
                url = _safe_url(d.get("url", ""))
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    docs.append([_doc_title(d.get("label", ""), url), url])
        docs.sort(key=lambda d: (d[0].startswith("Abridged"), d[0].startswith("Addendum")))
        page_label, page_url = _page_link(entries)
        dates = [e["filed_on"] for e in entries if e.get("filed_on")]
        seen_on = [(e.get("first_seen") or "")[:10] for e in entries if e.get("first_seen")]
        fresh = [(e.get("first_seen") or "")[:10] for e in entries
                 if not e.get("backfill") and (not e.get("baseline") or e.get("alerted"))]
        seg = _segment(entries)
        rows.append({
            "n": _best_name([e.get("company", "") for e in entries if e.get("company")] or ["Unknown"]),
            "g": seg,
            "x": exchange,
            "d": max(dates) if dates else (max(seen_on) if seen_on else ""),
            "st": [s for s in STATUS_ORDER if s in statuses],
            "sec": info.get("sector", ""),
            "c": info.get("city", ""),
            "b": _bankers(info.get("bankers", "")),
            "docs": docs[:3],
            "p": [page_label, page_url] if page_url else [],
            "new": max(fresh) if fresh else "",
        })
    rows.sort(key=lambda r: (r["d"], r["n"]), reverse=True)
    return rows


def _health(state: dict) -> list[dict]:
    out = []
    for name, h in sorted((state.get("health") or {}).items()):
        if h.get("last_ok"):  # a source that has never answered is switched off, not broken
            out.append({"name": label(name), "ok": not h.get("failures")})
    return out


def _safe_json(obj: object) -> str:
    # '</' and the two JS line separators can end a <script> block early; escape them so a company name
    # like 'Evil </script> Ltd' stays plain text.
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _script_hash(html: str) -> str:
    """CSP hash of the one executable script, so the page can forbid every other script."""
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    digest = hashlib.sha256(m.group(1).encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode()


def build_site(state: dict, out_path: str, now: datetime, market: dict | None = None, ipos: dict | None = None) -> None:
    updated = now.strftime("%d %b %Y, %I:%M %p IST").lstrip("0")
    payload = {
        "generated": now.isoformat(timespec="seconds"),
        "updated": updated,
        "companies": build_companies(state),
        "sectors": state.get("sectors") or {"updated": updated, "groups": {}},
        "health": _health(state),
        "alerts": sum(1 for e in state.get("seen", {}).values() if e.get("alerted")),
        "market": market,
        "ipos": (ipos or {}).get("items") if ipos else None,
    }
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__SCRIPT_HASH__", _script_hash(html))
    html = html.replace("__DATA__", _safe_json(payload))
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
