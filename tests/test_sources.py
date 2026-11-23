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


BSE_REAL = """<table class="mGrid gridwth" id="ContentPlaceHolder1_gvData"><caption>SME IPO</caption><thead>
<tr class="innertable_header"><th scope="col">Company</th><th scope="col">Draft Red Herring Prospectus / Draft Prospectus</th>
<th scope="col">Red Herring Prospectus</th><th scope="col">Prospectus</th><th scope="col">Basis of allotment advertisement</th></tr></thead>
<tr class="TTRow"><td class="TTRow_left"><a class="tablebluelink">Zadley India Limited</a></td><td>
<a id="ContentPlaceHolder1_gvData_hyDRHP_0" aria-label="Download PDF Zadley India Limited" href="/download/323118/SME_IPO%20InPrinciple/DRHP_Zadley_16112026_20261116190532.zip" target="_blank">16/11/2026</a></td><td> </td><td> </td><td> </td></tr>
<tr class="TTRow"><td class="TTRow_left"><a class="tablebluelink">Uranus Stone Products Limited</a></td><td>
<a id="ContentPlaceHolder1_gvData_hyDRHP_1" href="/download/373397/SME_IPO%20InPrinciple/UranusDRHP_20261106200603.zip" target="_blank">06/11/2026</a></td><td> </td><td> </td><td> </td></tr>
<tr class="TTRow"><td class="TTRow_left"><a class="tablebluelink">Jamnagar Agro Industries Limited</a></td><td>
<a id="ContentPlaceHolder1_gvData_hyDRHP_2" href="/download/331369/SME_IPO%20InPrinciple/JAIDRHP_20261105045926.zip" target="_blank">05/11/2026</a></td><td> </td><td> </td><td> </td></tr>
</table>"""


def test_bse_real_table_layout():
    recs = bse_sme.parse(BSE_REAL, "https://www.bsesme.com/PublicIssues/SMEIPODRHP.aspx")
    assert [r.company for r in recs] == [
        "Zadley India Limited", "Uranus Stone Products Limited", "Jamnagar Agro Industries Limited"]
    assert recs[0].filed_on == "2026-11-16"
    assert recs[0].url == ("https://www.bsesme.com/download/323118/SME_IPO%20InPrinciple/"
                           "DRHP_Zadley_16112026_20261116190532.zip")
    assert recs[0].official and recs[0].segment == "sme" and recs[0].exchange == "BSE SME"


def test_bse_row_cap():
    rows = "".join(
        f'<tr class="TTRow"><td><a class="tablebluelink">Company {i:04d} Limited</a></td>'
        f'<td><a href="/d/{i}.zip">01/01/2026</a></td></tr>' for i in range(bse_sme.MAX_ROWS + 50))
    html = f'<table id="ContentPlaceHolder1_gvData">{rows}</table>'
    assert len(bse_sme.parse(html)) == bse_sme.MAX_ROWS


def test_bse_rows():
    recs = bse_sme.parse(BSE_HTML, "https://www.bsesme.com/PublicIssues/SMEIPODRHP.aspx")
    assert [r.company for r in recs] == ["Example Industries Limited", "Sample Foods Pvt Ltd"]
    assert recs[0].url == "https://www.bsesme.com/docs/example_drhp.pdf"
    assert recs[0].filed_on == "2026-11-20"
    assert recs[0].official and recs[0].segment == "sme" and recs[0].exchange == "BSE SME"


SEBI_INDEX = '<a href="/sebiweb/x?smid=10">Draft Offer Documents filed with SEBI</a><a href="/other">Other Documents</a>'
SEBI_LIST = """
<ul>
 <li><a href="/filings/public-issues/nov-2026/hunger-pangs-limited-drhp_100.html">Hunger Pangs Limited – DRHP Hunger Pangs Limited - Draft Red Herring Prospectus</a> Nov 12, 2026</li>
 <li><a href="/filings/public-issues/nov-2026/chartered-speed_101.html">Chartered Speed Limited_Addendum to the DRHP</a> Nov 19, 2026</li>
 <li><a href="/filings/public-issues/oct-2026/carlsberg_102.html">Carlsberg India Limited- UDRHP-1 Carlsberg India Limited - Updated Draft Red Herring</a> Oct 29, 2026</li>
 <li><a href="/filings/public-issues/oct-2026/intellius_103.html">INTELLIUS RECODE LIMITED - Notice to Investors</a> Oct 27, 2026</li>
 <li><a href="/filings/public-issues.html">Public Issues</a></li>
</ul>"""


def test_sebi_two_step_and_event_types():
    assert sebi.find_listing_url(SEBI_INDEX) == "https://www.sebi.gov.in/sebiweb/x?smid=10"
    recs = sebi.parse_listing(SEBI_LIST)
    got = [(r.company, r.event, r.filed_on) for r in recs]
    assert got == [
        ("Hunger Pangs Limited", "drhp_filed", "2026-11-12"),
        ("Chartered Speed Limited", "addendum", "2026-11-19"),
        ("Carlsberg India Limited", "drhp_refiled", "2026-10-29"),
    ]  # the "Notice to Investors" item is not a DRHP event and is skipped
    assert all(r.segment == "mainboard" and r.official for r in recs)


CHIT_HTML = """
<div class="nhp-nlist">
<a class="nhp-lrow" title="Nlc India Renewables Ltd. Files DRHP for IPO: Issue Size &amp; Key Details Inside" href="/news-detail/ipo/upcoming-ipo/nlc-india-renewables-ipo/23397/"><span class="nhp-lrow-num">01</span><div>Upcoming IPO</div><div>Last Modified: Nov 23, 2026, 12:00 PM IST</div><h3>Nlc India Renewables Ltd. Files DRHP for IPO: Issue Size &amp; Key Details Inside</h3><p>teaser text</p></a>
<a class="nhp-lrow" title="Datasoft Computer Services Ltd. Refiles Draft IPO Prospectus with NSE SME" href="/news-detail/ipo/upcoming-ipo/datasoft-computer-services-ipo/21644/"><span>04</span><h3>Datasoft Computer Services Ltd. Refiles Draft IPO Prospectus with NSE SME</h3></a>
<a class="nhp-lrow" title="Vaibhav Vyapaar IPO Anchor Round: Mobilises Rs 11.88 crore" href="/news-detail/ipo-news/ipo-closed/x/2/"><h3>Vaibhav Vyapaar IPO Anchor Round</h3></a>
<a href="/about">About us</a>
</div>"""


def test_chittorgarh_reads_the_headline_not_the_card_blob():
    recs = chittorgarh.parse(CHIT_HTML)
    assert [(r.company, r.event, r.segment, r.exchange) for r in recs] == [
        ("Nlc India Renewables Ltd.", "drhp_filed", "unknown", ""),
        ("Datasoft Computer Services Ltd.", "drhp_refiled", "sme", "NSE Emerge"),
    ]
    assert not recs[0].official
    assert recs[0].url == "https://www.chittorgarh.com/news-detail/ipo/upcoming-ipo/nlc-india-renewables-ipo/23397/"


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
