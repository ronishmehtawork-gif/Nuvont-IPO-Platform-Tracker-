"""The dashboard is a static page, so its safety rests on the CSP hash and on how links and names are written."""
import base64
import hashlib
import re
from datetime import datetime

from radar import site
from radar.run import IST
from radar.site import build_companies, build_site


def entry(company, event, **kw):
    base = {"company": company, "event": event, "segment": "mainboard", "exchange": "NSE", "filed_on": "2026-11-20",
            "first_seen": "2026-11-20T10:00:00+05:30", "sources": {}, "official_url": "", "documents": [], "info": {},
            "alerted": False, "baseline": False}
    base.update(kw)
    return base


def test_one_row_per_company_with_merged_status_and_documents():
    state = {"seen": {
        "acme limited|drhp_filed": entry("ACME LIMITED", "drhp_filed", documents=[{"label": "DRHP", "url": "https://x.test/a.zip"}],
                                         info={"sector": "Textiles", "city": "Surat", "bankers": "Axis Capital, ICICI Securities +3 more"}),
        "acme limited|approval": entry("Acme Limited", "approval"),
        "acme limited|addendum": entry("Acme Limited", "addendum", exchange="SEBI",
                                       documents=[{"label": "Addendum", "url": "https://x.test/add.pdf"}]),
    }}
    rows = build_companies(state)
    assert len(rows) == 1
    row = rows[0]
    assert row["n"] == "Acme Limited" and row["st"] == ["filed", "approved", "addendum"]
    assert row["x"] == "NSE" and row["b"] == ["Axis Capital", "ICICI Securities"]
    assert [d[0] for d in row["docs"]] == ["DRHP (ZIP)", "Addendum (PDF)"]


def test_only_web_links_reach_the_page_and_sebi_is_not_an_exchange():
    state = {"seen": {"x ltd|drhp_filed": entry("X Ltd", "drhp_filed", exchange="SEBI", official_url="javascript:alert(1)",
                                                 documents=[{"label": "DRHP", "url": "javascript:alert(2)"}],
                                                 sources={"sebi": "data:text/html,boom"})}}
    row = build_companies(state)[0]
    assert row["docs"] == [] and row["p"] == [] and row["x"] == ""


def test_csp_hash_matches_the_only_executable_script(tmp_path):
    out = tmp_path / "index.html"
    build_site({"seen": {}}, str(out), datetime(2026, 11, 2, 12, 0, tzinfo=IST))
    html = out.read_text()
    code = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    digest = "sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode()
    assert f"script-src '{digest}'" in html
    assert "connect-src 'self'" in html and "default-src 'none'" in html  # the page may only ask its own site about new data
    assert "__DATA__" not in html and "__SCRIPT_HASH__" not in html
    assert len(re.findall(r"<script>", html)) == 1  # the data block is type=application/json, not executable


def test_market_and_ipo_data_travel_inside_the_page_without_breaking_it(tmp_path):
    market = {"asof": "2026-11-25", "universe": 1, "priced": 1, "horizons": ["1D"], "industries": ["Power"],
              "clusters": [{"n": "Power", "k": 1, "med": {"1D": 1.0}, "adv": 1.0}],
              "stocks": [["EVIL", "Evil </script><img src=x onerror=alert(1)> Ltd", 0, [1.0]]], "flow": None}
    ipos = {"items": [{"n": "Bad </script> IPO", "u": "https://ipowatch.in/x/", "sg": "M", "st": "open"}]}
    out = tmp_path / "index.html"
    build_site({"seen": {}}, str(out), datetime(2026, 11, 26, 12, 0, tzinfo=IST), market=market, ipos=ipos)
    html = out.read_text()
    assert html.count("</script>") == 2 and "Evil <\\/script>" in html and "Bad <\\/script> IPO" in html
    assert "__DATA__" not in html and "onerror" in html  # present only as inert JSON text inside the data block
    code = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    assert "innerHTML" not in code and "document.write" not in code and "eval(" not in code and "outerHTML" not in code


def test_ipos_get_sector_and_lead_managers_from_the_same_companys_filings_when_the_table_has_none():
    companies = [{"n": "Foodlink F&B Holdings Limited", "sec": "Food and beverages", "b": ["Beeline Capital"],
                  "docs": [["DRHP (PDF)", "https://sebi.gov.in/x.pdf"]]}]
    items = [{"n": "Foodlink F&B Holdings Ltd", "u": "https://ipowatch.in/a/", "st": "open"},
             {"n": "Foodlink F&B Holdings Ltd", "sc": "Packaged food", "bk": ["Own Banker"], "st": "open"},
             {"n": "Somebody Else", "st": "open"}]
    got = site.enrich_ipos(items, companies)
    assert got[0]["sc"] == "Food and beverages" and got[0]["bk"] == ["Beeline Capital"] and got[0]["dl"][1].endswith("x.pdf")
    assert got[1]["sc"] == "Packaged food" and got[1]["bk"] == ["Own Banker"]  # what the issue page said wins
    assert "sc" not in got[2] and "dl" not in got[2]
    assert "sc" not in items[0]  # the input is not changed


def test_three_sections_and_a_landing_gate_with_no_commodities_or_breadth(tmp_path):
    out = tmp_path / "index.html"
    flows = {"asof": "2026-11-25", "updated": "x", "days": [{"d": "2026-11-25", "fb": 1.0, "fs": 1.0, "fn": 0.0, "db": 1.0, "ds": 1.0, "dn": 0.0}]}
    build_site({"seen": {}}, str(out), datetime(2026, 11, 26, 12, 0, tzinfo=IST), flows=flows)
    html = out.read_text()
    tabs = re.findall(r'<button type="button" role="tab" id="tab-(\w+)"', html)
    assert tabs == ["desk", "filings", "sectors"]
    for pid in ("v-desk", "v-filings", "v-sectors", "gate", "logo3d"):
        assert f'id="{pid}"' in html
    for gone in ("v-flow", "v-commodities", "v-ipos"):
        assert f'id="{gone}"' not in html
    assert "breadth" not in html.lower()
    # Old bookmarks to the removed pages still land on a real page; nothing else mentions commodities.
    assert "commodities" not in re.sub(r"const LEGACY = [^\n]*", "", html)
    assert '"flows":{"asof":"2026-11-25"' in html
    assert '"commodities"' not in html


def test_the_page_builds_without_any_fii_dii_history(tmp_path):
    out = tmp_path / "index.html"
    build_site({"seen": {}}, str(out), datetime(2026, 11, 26, 12, 0, tzinfo=IST))
    assert '"flows":null' in out.read_text()


def test_page_script_has_no_inline_handlers_or_network_calls(tmp_path):
    out = tmp_path / "index.html"
    build_site({"seen": {}}, str(out), datetime(2026, 11, 26, 12, 0, tzinfo=IST))
    html = out.read_text()
    head, _, _ = html.partition('<script type="application/json"')
    assert not re.search(r"\son[a-z]+\s*=", head)  # no inline event attributes
    assert re.findall(r"<link[^>]*>", head) == ['<link rel="icon" type="image/png" href="assets/favicon.png">']  # only the local icon
    assert not re.search(r"<(img|script|iframe)[^>]*(src)=\"https?:", html, re.I)
    code = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    for banned in ("XMLHttpRequest", "WebSocket", "import(", "localStorage", "sendBeacon"):
        assert banned not in code, banned
    # The one network call is the page asking its own site, headers only, whether newer data has been published.
    assert code.count("fetch(") == 1 and 'fetch(location.pathname, { method: "HEAD"' in code
    assert not re.search(r"fetch\(\s*[\"']https?:", code)
