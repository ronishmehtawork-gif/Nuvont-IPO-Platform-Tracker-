"""Lead managers (merchant bankers): one clean name per firm, and the IPO ledger behind the Bankers directory.

Two layers feed the directory:
  * Filings: every DRHP the radar has found names its lead managers. That is counted in the browser from the same
    company list the Filings page uses, so it is always as fresh as the radar.
  * IPO record: the issues that pass through the IPO Desk (issue size in crores, issue price, lead managers) are kept in
    data/bankers_record.json as they are seen, so money raised builds up automatically. Listing-day and since-listing
    results need a listing price, which no free public source gives the radar, so they are read from an optional file the
    desk can fill: data/bankers_seed.csv (columns below). Nothing is estimated or invented: with no listing price, the
    page says so.

Money rules: issue size is kept in crores. A seed file must give issue_size_cr already in crores; prices are per share
and stay in rupees."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

SEED_COLUMNS = ["company", "segment", "lead_managers", "issue_size_cr", "issue_price", "listing_date",
                "listing_price", "current_price"]

_SUFFIX = re.compile(r"\s+(private limited|pvt\.? ltd\.?|limited|ltd\.?)$", re.I)
_ACRONYMS = {
    "jm": "JM", "hdfc": "HDFC", "clsa": "CLSA", "idbi": "IDBI", "hsbc": "HSBC", "bofa": "BofA", "smc": "SMC",
    "isk": "ISK", "pnb": "PNB", "bob": "BOB", "dam": "DAM", "llp": "LLP", "incred": "InCred", "ski": "SKI",
    "iifl": "IIFL", "icici": "ICICI", "sbi": "SBI", "pl": "PL", "gyr": "GYR", "svk": "SVK", "boi": "BOI",
}
# Firms that changed their registered name: the old and new name are one lead manager.
_ALIASES = {
    "iifl securities": "IIFL Capital Services",
    "idfc securities": "DAM Capital Advisors",
    "guiness corporate advisors": "Oneview Corporate Advisors",
    "360 one wam": "360 ONE WAM",
}
_EMPTY = {"", "n/a", "na", "-", "none", "nil", "not available"}


def canon(name: str) -> str:
    """'Dam Capital Advisors Limited (Formerly Idfc Securities Limited)' -> 'DAM Capital Advisors'. '' for no name."""
    s = re.sub(r"\([^)]*\)", " ", name or "")
    s = re.sub(r"\s+", " ", s).strip(" ,.-")
    for _ in range(2):
        s = _SUFFIX.sub("", s).strip(" ,.-")
    if s.lower() in _EMPTY:
        return ""
    if s.lower() in _ALIASES:
        return _ALIASES[s.lower()]
    if s.isupper():  # 'ERUDORE CAPITAL' and 'Erudore Capital' are one firm
        s = s.title()
    words = []
    for w in s.split(" "):
        words.append(_ACRONYMS.get(w.lower(), w))
    return " ".join(words)


def canon_list(names) -> list[str]:
    out: list[str] = []
    for n in names or []:
        c = canon(n)
        if c and c not in out:
            out.append(c)
    return out


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\b(limited|ltd|pvt|private)\b", "", (name or "").lower()))


def _num(v):
    try:
        x = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return x if x == x and x > 0 else None


def load_ledger(path: str) -> dict:
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, dict) and isinstance(data.get("ipos"), list):
                return data
        except ValueError:
            pass
    return {"since": "", "ipos": []}


def update_ledger(items: list[dict], path: str, today: str) -> dict:
    """Add or refresh each issue the IPO Desk shows. A listing price is never written here; only the seed file has one."""
    ledger = load_ledger(path)
    if not ledger["since"]:
        ledger["since"] = today
    index = {_key(r["n"]): r for r in ledger["ipos"] if r.get("n")}
    for it in items or []:
        name = it.get("n") or ""
        if not name:
            continue
        row = index.get(_key(name))
        if row is None:
            row = {"n": name}
            ledger["ipos"].append(row)
            index[_key(name)] = row
        row["sg"] = it.get("sg") or row.get("sg", "")
        bks = canon_list(it.get("bk"))
        if bks:
            row["bk"] = bks
        for src, dst in (("sz", "sz"), ("hi", "ip"), ("ls", "ls"), ("o", "o")):
            v = it.get(src)
            if v not in (None, "", 0):
                row[dst] = v
        row["seen"] = today
    ledger["ipos"].sort(key=lambda r: (r.get("o") or "", r["n"]))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n")
    return ledger


def read_seed(path: str) -> tuple[list[dict], int]:
    """Rows from the optional seed CSV and how many rows were skipped for missing or bad values."""
    p = Path(path)
    if not p.exists():
        return [], 0
    rows, skipped = [], 0
    with p.open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            name = (r.get("company") or "").strip()
            seg = (r.get("segment") or "").strip().lower()
            bks = canon_list(re.split(r"[;|]", r.get("lead_managers") or ""))
            ip, lp = _num(r.get("issue_price")), _num(r.get("listing_price"))
            if not name or seg not in ("sme", "mainboard", "m", "s") or not bks or ip is None:
                skipped += 1
                continue
            row = {"n": name, "sg": "S" if seg in ("sme", "s") else "M", "bk": bks, "ip": ip, "src": "seed"}
            for src, dst in (("issue_size_cr", "sz"), ("current_price", "cp")):
                v = _num(r.get(src))
                if v is not None:
                    row[dst] = v
            if lp is not None:
                row["lp"] = lp
            ld = (r.get("listing_date") or "").strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ld):
                row["ls"] = ld
            rows.append(row)
    return rows, skipped


def record(ledger_path: str, seed_path: str) -> dict:
    """The IPO record the page reads: the ledger, with any seed rows laid over it by company name."""
    ledger = load_ledger(ledger_path)
    seed, skipped = read_seed(seed_path)
    merged = {_key(r["n"]): dict(r, src="desk") for r in ledger["ipos"] if r.get("n") and r.get("bk")}
    for r in seed:
        merged[_key(r["n"])] = dict(merged.get(_key(r["n"]), {}), **r)
    ipos = sorted(merged.values(), key=lambda r: (r.get("ls") or r.get("o") or "", r["n"]))
    return {"since": ledger.get("since", ""), "ipos": ipos, "seed": len(seed), "skipped": skipped}
