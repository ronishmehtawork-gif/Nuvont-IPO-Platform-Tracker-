"""Parser tests use SYNTHETIC pages written to look plausible. They prove the logic, not that the
live sites look like this. Real snapshots from the first GitHub run are used to tighten the parsers."""
from radar.sources import bse_sme, chittorgarh, rss, sebi

BSE_HTML = """
<table>
 <tr><th>Sr No</th><th>Company</th><th>Date</th><th>Document</th></tr>
 <tr><td>1</td><td>Example Industries Limited</td><td>20/11/2026</td>
     <td><a href="/docs/example_drhp.pdf">Download</a></td></tr>
 <tr><td>2</td><td>Sample Foods Pvt Ltd</td><td>18/11/2026</td>
     <td><a href="https://www.bsesme.com/docs/sample.pdf">View</a></td></tr>
 <tr><td>3</td><td>No link here</td><td>01/11/2026</td><td></td></tr>
</table>"""


def test_bse_rows():
    recs = bse_sme.parse(BSE_HTML, "https://www.bsesme.com/PublicIssues/SMEIPODRHP.aspx")
    assert [r.company for r in recs] == ["Example Industries Limited", "Sample Foods Pvt Ltd"]
    assert recs[0].url == "https://www.bsesme.com/docs/example_drhp.pdf"
    assert recs[0].filed_on == "2026-11-20"
    assert recs[0].official and recs[0].segment == "sme" and recs[0].exchange == "BSE SME"


SEBI_INDEX = '<a href="/sebiweb/x?smid=10">Draft Offer Documents filed with SEBI</a><a href="/other">Other Documents</a>'
SEBI_LIST = """
<ul>
 <li><a href="/filings/public-issues/nov-2026/example-foods-limited-sme-ipo_100.html">Example Foods Limited – SME IPO</a> Nov 20, 2026</li>
 <li><a href="/filings/public-issues/nov-2026/big-co-limited_101.html">Big Co Limited</a> Nov 19, 2026</li>
 <li><a href="/filings/public-issues.html">Public Issues</a></li>
</ul>"""


def test_sebi_two_step_and_segments():
    assert sebi.find_listing_url(SEBI_INDEX) == "https://www.sebi.gov.in/sebiweb/x?smid=10"
    recs = sebi.parse_listing(SEBI_LIST)
    assert len(recs) == 2
    assert (recs[0].company, recs[0].segment, recs[0].filed_on) == (
        "Example Foods Limited", "sme", "2026-11-20")
    assert recs[1].segment == "mainboard"


CHIT_HTML = """
<a href="/news-detail/ipo-news/upcoming-ipo/intergen-energy-ipo/1/">Intergen Energy Ltd. Files IPO Draft Papers with the BSE SME</a>
<a href="/news-detail/ipo-news/ipo-closed/x/2/">Vaibhav Vyapaar IPO Anchor Round: Mobilises Rs 11.88 crore</a>
<a href="/news-detail/ipo-news/upcoming-ipo/b/3/">Biocon Electric Ltd. Advances IPO Plans, Files DRHP with SEBI</a>
<a href="/about">About us</a>"""


def test_chittorgarh_keeps_only_drhp_and_approval_headlines():
    recs = chittorgarh.parse(CHIT_HTML)
    assert [r.company for r in recs] == ["Intergen Energy Ltd.", "Biocon Electric Ltd."]
    assert not recs[0].official
    assert recs[0].url.startswith("https://www.chittorgarh.com/news-detail/")


RSS_XML = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Srit India files DRHP for an IPO</title><link>https://example.com/a</link></item>
<item><title>Upcoming IPO in July 2026 Calendar</title><link>https://example.com/b</link></item>
</channel></rss>"""

ATOM_XML = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>RKB Global submits DRHP for an IPO</title><link href="https://example.com/c"/></entry></feed>"""


def test_rss_and_atom():
    recs = rss.parse_feed(RSS_XML, "rss_x")
    assert [r.company for r in recs] == ["Srit India"]
    assert rss.parse_feed(ATOM_XML, "rss_y")[0].company == "RKB Global"
