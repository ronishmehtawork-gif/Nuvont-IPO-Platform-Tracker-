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
    assert "connect-src 'none'" in html and "default-src 'none'" in html
    assert "__DATA__" not in html and "__SCRIPT_HASH__" not in html
    assert len(re.findall(r"<script>", html)) == 1  # the data block is type=application/json, not executable
