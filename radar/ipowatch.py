"""IPO pipeline: open, upcoming and just-closed issues with the grey market premium (GMP).

Source: the public GMP table on ipowatch.in (company, GMP in rupees, price band, dates, type, status) and, for each
issue, the details on its own page (issue size, lot size, listing date). We read the facts and build our own cards;
nothing is copied as text. GMP is an unofficial grey-market quote, not an exchange price.

Money rules: issue size is stored in crores (lakhs, millions and billions are converted); GMP, price band and minimum
investment are per-share or per-lot rupee amounts and stay in rupees."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .market import IST

NAME = "ipowatch"
URL = "https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/"
MAX_DETAIL_FETCHES = 14
DETAIL_VERSION = 3          # bump when parse_details learns a new field, so cached pages are read again
KEEP_CLOSED_DAYS = 10
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

TYPES = {  # table text -> (segment, exchange)
    "mainboard": ("M", ""),
    "main board": ("M", ""),
    "main": ("M", ""),
    "nse emerge": ("S", "NSE Emerge"),
    "bse sme": ("S", "BSE SME"),
    "nse sme": ("S", "NSE Emerge"),
    "sme": ("S", ""),
}


class IpoError(Exception):
    pass


# ----------------------------------------------------------------------------- field parsers

def parse_money(text: str) -> float | None:
    """'+₹41', '₹ -5', '₹0' -> a signed rupee amount. '--', '', 'NA' -> None."""
    m = re.search(r"([+\-−–])?\s*(?:₹|Rs\.?)?\s*([+\-−–])?\s*(\d[\d,]*(?:\.\d+)?)", text or "")
    if not m:
        return None
    negative = (m.group(1) or m.group(2) or "") in "-−–" and bool(m.group(1) or m.group(2))
    value = float(m.group(3).replace(",", ""))
    return -value if negative else value


def parse_band(text: str) -> tuple[float | None, float | None]:
    numbers = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")]
    if not numbers:
        return None, None
    return min(numbers), max(numbers)


def _month_day(text: str) -> tuple[int, int] | None:
    m = re.search(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+([A-Za-z]{3})", text or "")
    if not m or m.group(2).lower() not in MONTHS:
        return None
    return MONTHS[m.group(2).lower()], int(m.group(1))


def parse_date_range(text: str, today: date) -> tuple[str, str]:
    """'19 Nov - 23 Nov' -> ('2026-11-19', '2026-11-23'). The year comes from today; a range that wraps the
    new year, or one read just after it, is placed in the right year."""
    parts = re.split(r"\s*[-–—]\s*|\s+to\s+", text or "", maxsplit=1)
    first = _month_day(parts[0]) if parts else None
    last = _month_day(parts[1]) if len(parts) > 1 else first
    if not first or not last:
        return "", ""
    year = today.year
    try:
        start = date(year, *first)
        if (start - today).days > 240:
            start = date(year - 1, *first)
        elif (today - start).days > 240:
            start = date(year + 1, *first)
        end = date(start.year, *last)
        if end < start:
            end = date(start.year + 1, *last)
    except ValueError:
        return "", ""
    return start.isoformat(), end.isoformat()


def parse_full_date(text: str) -> str:
    m = re.search(r"(\d{1,2})\s*(?:st|nd|rd|th)?[\s,\-]+([A-Za-z]{3})[A-Za-z]*[\s,\-]+(\d{4})", text or "")
    if m and m.group(2).lower() in MONTHS:
        try:
            return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
        except ValueError:
            return ""
    m = re.search(r"([A-Za-z]{3})[A-Za-z]*\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", text or "")  # 'Dec 3, 2026'
    if m and m.group(1).lower() in MONTHS:
        try:
            return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
        except ValueError:
            return ""
    return ""


def to_crore(amount: float, unit: str) -> float | None:
    """Convert to crores (project rule: lakhs, thousands, millions and billions all end up in crores)."""
    u = (unit or "").strip().lower().rstrip(".")
    if u in ("cr", "crore", "crores", "cror"):
        return round(amount, 2)
    if u in ("lakh", "lakhs", "lac", "lacs", "l"):
        return round(amount / 100, 4)
    if u in ("thousand", "k"):
        return round(amount / 10_000, 5)
    if u in ("million", "mn", "m"):
        return round(amount / 10, 4)
    if u in ("billion", "bn", "b"):
        return round(amount * 100, 2)
    return None


# ----------------------------------------------------------------------------- the GMP table

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _columns(headers: list[str]) -> dict[str, int]:
    cols: dict[str, int] = {}
    for i, h in enumerate(headers):
        h = h.upper()
        if "UPDATED" in h:
            cols.setdefault("updated", i)
        elif "NAME" in h:
            cols.setdefault("name", i)
        elif "GMP" in h:
            cols.setdefault("gmp", i)
        elif "PRICE" in h or "BAND" in h:
            cols.setdefault("band", i)
        elif "DATE" in h:
            cols.setdefault("dates", i)
        elif "TYPE" in h:
            cols.setdefault("type", i)
        elif "STATUS" in h:
            cols.setdefault("status", i)
    return cols


def parse_table(html: str, today: date, base: str = URL) -> list[dict]:
    """One dict per IPO row. Raises IpoError when no table with the expected headings is on the page."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    found_table = False
    for table in soup.find_all("table"):
        head = table.find("tr")
        if head is None:
            continue
        headers = [_clean(c.get_text(" ")) for c in head.find_all(["th", "td"])]
        cols = _columns(headers)
        if not {"name", "gmp", "status"} <= set(cols):
            continue
        found_table = True
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < len(headers):
                continue
            get = lambda key: _clean(cells[cols[key]].get_text(" ")) if key in cols else ""  # noqa: E731
            name = get("name")
            link = ""
            anchor = cells[cols["name"]].find("a", href=True)
            if anchor:
                url = urljoin(base, anchor["href"])
                host = urlparse(url).hostname or ""
                link = url if url.startswith("https://") and (host == "ipowatch.in" or host.endswith(".ipowatch.in")) else ""
            if not name:
                continue
            low, high = parse_band(get("band"))
            seg, exchange = TYPES.get(get("type").lower(), ("", ""))
            opens, closes = parse_date_range(get("dates"), today)
            status = get("status").lower()
            gmp_text = get("gmp")
            gmp = parse_money(gmp_text) if re.search(r"\d", gmp_text) else None
            item = {
                "n": name, "u": link, "sg": seg or "U", "x": exchange,
                "st": status if status in ("open", "upcoming", "closed") else "",
                "o": opens, "c": closes, "lo": low, "hi": high, "gmp": gmp,
                "up": parse_full_date(get("updated")),
            }
            items.append(item)
    if not found_table:
        raise IpoError("no table with IPO name, GMP and status columns was found")
    return items


def derive(item: dict) -> dict:
    """GMP as a percentage of the upper price band, estimated listing price and minimum investment (one lot)."""
    hi, gmp, lot = item.get("hi"), item.get("gmp"), item.get("lot")
    pct = None
    if hi and gmp is not None:
        if abs(gmp / hi) <= 5:
            pct = round(gmp / hi * 100, 2)
        else:
            item["gmp"] = None  # a premium of more than 5x the price is a parsing slip, not a quote
    item["pct"] = pct
    item["est"] = round(hi + item["gmp"], 2) if pct is not None else None
    item["inv"] = int(round(lot * hi)) if lot and hi else None
    return item


def validate_items(items: list[dict]) -> list[dict]:
    """Drop rows that cannot be right instead of showing them."""
    good = []
    for it in items:
        if it["lo"] is not None and it["hi"] is not None and it["lo"] > it["hi"]:
            continue
        if it["o"] and it["c"] and it["c"] < it["o"]:
            continue
        if not it["st"]:
            continue
        good.append(it)
    return good


def select(items: list[dict], today: date) -> list[dict]:
    """Open and upcoming issues, plus issues closed in the last few days. Open first, then upcoming, then closed."""
    keep = []
    cutoff = (today - timedelta(days=KEEP_CLOSED_DAYS)).isoformat()
    for it in items:
        if it["st"] == "closed" and not (it.get("ls", "") >= today.isoformat() or it["c"] >= cutoff):
            continue
        keep.append(it)
    opened = sorted((i for i in keep if i["st"] == "open"), key=lambda i: (i["c"] or "9999", i["n"]))
    coming = sorted((i for i in keep if i["st"] == "upcoming"), key=lambda i: (i["o"] or "9999", i["n"]))
    closed = sorted((i for i in keep if i["st"] == "closed"), key=lambda i: (i["c"], i["n"]), reverse=True)
    return (opened + coming + closed)[:40]


# ----------------------------------------------------------------------------- detail page

_STATUS_WORDS = {"pending", "active", "completed", "complete", "done", "upcoming", "live", "closed", "tba", "open", "opened"}
_TIMELINE_LABELS = {
    "ipo opens": "o2", "ipo closes": "c2", "basis of allotment": "al", "allotment": "al",
    "refunds": "rf", "initiation of refunds": "rf", "demat credit": "dc", "credit of shares to demat": "dc",
    "listing": "ls", "listing date": "ls",
}
_BANKER_SUFFIX = re.compile(r"[\s,]+(?:pvt\.?|private)?\s*(?:ltd\.?|limited)\.?$", re.I)


def clean_banker(name: str) -> str:
    """'Equirus Capital Pvt Ltd' -> 'Equirus Capital'. Keeps the name readable and makes the same bank match across issues."""
    name = _clean(name)
    for _ in range(2):
        name = _BANKER_SUFFIX.sub("", name).strip(" ,.")
    return name


def _timeline_dates(lines: list[str]) -> dict[str, str]:
    """Label, optional status word, then the date, in the page's own order. Anything unclear stays missing."""
    start = next((i for i, ln in enumerate(lines) if ln.lower().startswith("ipo timeline")), 0)
    out: dict[str, str] = {}
    for i in range(start, min(len(lines), start + 120)):
        low = lines[i].lower().rstrip(":")
        key = _TIMELINE_LABELS.get(low)
        rest = ""
        if key is None:
            for label, k in _TIMELINE_LABELS.items():
                if low.startswith(label + " ") or low.startswith(label + ":"):
                    key, rest = k, lines[i][len(label):].lstrip(" :")
                    break
        if key is None or key in out:
            continue
        found = parse_full_date(rest) if rest else ""
        j = i + 1
        while not found and j < min(len(lines), i + 4):
            word = lines[j].lower().strip(" :")
            if word in _STATUS_WORDS:
                j += 1
                continue
            found = parse_full_date(lines[j]) if len(lines[j]) <= 40 else ""
            break
        if found:
            out[key] = found
    return out


def _links_after(label, kind: str, stop: str, limit: int = 8) -> list[str]:
    """Anchor texts under /kind/ that follow a label, up to the next label."""
    names: list[str] = []
    for n, node in enumerate(label.next_elements):
        if n > 400 or len(names) >= limit:
            break
        if isinstance(node, str):
            if node.strip().lower() == stop:
                break
        elif node.name == "a" and f"/{kind}/" in (node.get("href") or ""):
            text = _clean(node.get_text(" "))
            if text and text not in names:
                names.append(text)
    return names


def check_dates(item: dict) -> dict:
    """Keep a date only if it sits where the issue process puts it. Wrong is worse than missing."""
    def d(key):
        try:
            return date.fromisoformat(item.get(key) or "")
        except ValueError:
            return None
    c, al, rf, dc, ls = (d(k) for k in ("c", "al", "rf", "dc", "ls"))
    after_close = lambda x, most: bool(x and c and 0 < (x - c).days <= most)  # noqa: E731
    if al and not after_close(al, 7):
        item["al"] = al = None
    if rf and not after_close(rf, 10):
        item["rf"] = rf = None
    if dc and not after_close(dc, 10):
        item["dc"] = dc = None
    if ls and not after_close(ls, 14):
        item["ls"] = ls = None
    if al and rf and rf < al:
        item["rf"] = None
    if al and dc and dc < al:
        item["dc"] = None
    if ls and ((rf and ls < rf) or (dc and ls < dc)):
        item["ls"] = None
    for key in ("al", "rf", "dc", "ls"):
        if item.get(key) is None:
            item.pop(key, None)
    return item


def parse_details(html: str) -> dict:
    """Issue size (crores), lot size, important dates, lead managers, registrar and sector from an issue's own page.
    Any field may be missing."""
    soup = BeautifulSoup(html, "html.parser")
    text = _clean(soup.get_text(" "))
    out: dict = {}
    m = re.search(r"Issue Size[^0-9₹]{0,40}(?:₹|Rs\.?)?\s*([\d,]+(?:\.\d+)?)\s*(Crores?|Cr\.?|Lakhs?|Lacs?|Million|Mn|Billion|Bn)?",
                  text, re.I)
    if m:
        value = to_crore(float(m.group(1).replace(",", "")), m.group(2) or "Cr")
        if value is not None and 0 < value < 100_000:
            out["sz"] = value
    m = re.search(r"Market Lot[^0-9]{0,30}([\d,]+)|Lot Size[^0-9]{0,30}([\d,]+)", text, re.I)
    if m:
        lot = int((m.group(1) or m.group(2)).replace(",", ""))
        if 1 <= lot <= 1_000_000:
            out["lot"] = lot
    m = re.search(r"Listing Date[^A-Za-z0-9]{0,10}([A-Za-z0-9 ,\-]{6,40})", text, re.I)
    if m:
        listed = parse_full_date(m.group(1))
        if listed:
            out["ls"] = listed

    lines = [ln for ln in (_clean(x) for x in soup.get_text("\n").split("\n")) if ln]
    out.update(_timeline_dates(lines))  # o2 / c2 are only used to cross-check the table's own open and close dates

    for a in soup.find_all("a", href=re.compile(r"/sectors/[^/]+/?$")):
        line = _clean(a.parent.get_text(" "))
        if "listed on" in line.lower():
            sector = _clean(a.get_text(" "))
            if sector:
                out["sc"] = sector
                venues = re.search(r"Listed on\s+([A-Za-z ,&]+?)\s*(?:[·•|]|\d|$)", line, re.I)
                if venues:
                    out["ex"] = re.sub(r"\s*,\s*", ", ", venues.group(1).strip(" ,"))
                break
    label = soup.find(string=re.compile(r"^\s*Lead Managers?\s*$", re.I))
    if label:
        bankers = []
        for name in _links_after(label, "lead-managers", "registrar"):
            cleaned = clean_banker(name)
            if cleaned and cleaned not in bankers:
                bankers.append(cleaned)
        if bankers:
            out["bk"] = bankers
    label = soup.find(string=re.compile(r"^\s*Registrar\s*$", re.I))
    if label:
        names = _links_after(label, "registrar", "lead managers", limit=1)
        if names:
            out["rg"] = clean_banker(names[0])
    return out


# ----------------------------------------------------------------------------- job

def refresh(fetcher, path: str, now: datetime) -> tuple[dict | None, dict]:
    """Fetch the table, add details (cached for a day), check, save. Returns (payload to show, diagnostics)."""
    today = now.astimezone(IST).date()
    previous = load(path)
    info: dict = {"ok": False, "note": ""}
    try:
        res = fetcher.get(NAME, URL)
        if not res.ok:
            raise IpoError(res.error or "no response")
        rows = validate_items(parse_table(res.text, today))
        info["rows"] = len(rows)
        cache = {i["u"]: i for i in (previous or {}).get("items", []) if i.get("u")}
        fetched = skipped = conflicts = 0
        for it in rows:
            old = cache.get(it["u"], {})
            for key in ("sz", "lot", "ls", "al", "rf", "dc", "bk", "rg", "sc", "ex", "dchecked", "dv"):
                if key in old:
                    it[key] = old[key]
        chosen = select(rows, today)
        fresh = lambda it: it.get("dchecked") == today.isoformat() and it.get("dv") == DETAIL_VERSION  # noqa: E731
        for it in chosen:
            if not it["u"] or fresh(it) or fetched >= MAX_DETAIL_FETCHES:
                skipped += bool(it["u"] and not fresh(it))
                continue
            page = fetcher.get("ipowatch_detail", it["u"])
            fetched += 1
            if page.ok:
                got = parse_details(page.text)
                o2, c2 = got.pop("o2", None), got.pop("c2", None)
                if (o2 and it["o"] and o2 != it["o"]) or (c2 and it["c"] and c2 != it["c"]):
                    conflicts += 1  # the table and the issue page disagree: keep the table, and say so
                it.update(got)
                it["dchecked"] = today.isoformat()
                it["dv"] = DETAIL_VERSION
        items = [derive(check_dates(it)) for it in select(chosen, today)]
        payload = {"source": NAME, "items": items}
        if not (previous and previous.get("items") == items):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        have = lambda k: sum(1 for i in items if i.get(k) is not None)  # noqa: E731
        info.update(ok=True, shown=len(items), with_gmp=have("gmp"), with_size=have("sz"), with_lot=have("lot"),
                    with_listing=sum(1 for i in items if i.get("ls")), with_allotment=sum(1 for i in items if i.get("al")),
                    with_bankers=sum(1 for i in items if i.get("bk")), with_sector=sum(1 for i in items if i.get("sc")),
                    date_conflicts=conflicts, detail_fetched=fetched, detail_skipped=skipped, note="saved")
        return payload, info
    except IpoError as e:
        info["note"] = f"kept previous data: {e}"
    except Exception as e:  # noqa: BLE001 - never stop the alert run for the pipeline page
        info["note"] = f"kept previous data: unexpected {type(e).__name__}: {str(e)[:160]}"
    return previous, info


def load(path: str) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("items"), list) else None
