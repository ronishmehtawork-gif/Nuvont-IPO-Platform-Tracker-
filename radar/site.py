from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import EVENT_LABELS, label

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SME DRHP Radar</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e1dc;--acc:#185fa5}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececea;--mut:#a3a29c;--line:#34332f;--acc:#85b7eb}}
body{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:1.25rem;max-width:980px;margin-inline:auto}
h1{font-size:22px;font-weight:500;margin:0 0 .25rem}p.sub{color:var(--mut);margin:0 0 1rem}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:1rem}
input,select{font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
input{flex:1;min-width:200px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:500;font-size:13px}a{color:var(--acc)}.m{color:var(--mut);font-size:13px}
footer{margin-top:1.5rem;color:var(--mut);font-size:13px}
</style></head><body>
<h1>SME DRHP Radar</h1>
<p class="sub">Draft offer documents spotted on exchange, regulator and IPO sites. Updated __UPDATED__ IST.</p>
<div class="bar"><input id="q" placeholder="Search company" aria-label="Search company">
<select id="seg" aria-label="Segment"><option value="">All segments</option><option value="sme">SME</option><option value="mainboard">Mainboard</option><option value="unknown">Not confirmed</option></select></div>
<table><thead><tr><th>Company</th><th>Event</th><th>Where</th><th>First seen</th><th>Link</th></tr></thead><tbody id="rows"></tbody></table>
<footer>Information only, collected from public pages. Links go to the official document when one has been found. Not investment advice. Always check the exchange.</footer>
<script>
const DATA=__DATA__;
const rows=document.getElementById('rows');
function cell(tr,text){const td=document.createElement('td');td.textContent=text;tr.appendChild(td);return td}
function render(){
  const q=document.getElementById('q').value.toLowerCase(),seg=document.getElementById('seg').value;
  rows.textContent='';
  DATA.filter(r=>(!seg||r.segment===seg)&&r.company.toLowerCase().includes(q)).forEach(r=>{
    const tr=document.createElement('tr');
    cell(tr,r.company);cell(tr,r.event);cell(tr,r.where);cell(tr,r.first_seen);
    const td=document.createElement('td');
    if(/^https?:\\/\\//.test(r.url)){const a=document.createElement('a');a.href=r.url;a.rel='noopener noreferrer';a.textContent=r.official?'Official document':'Report ('+r.source+')';td.appendChild(a)}
    tr.appendChild(td);rows.appendChild(tr)});
}
document.getElementById('q').addEventListener('input',render);
document.getElementById('seg').addEventListener('change',render);
render();
</script></body></html>
"""


def build_site(state: dict, out_path: str, now: datetime, limit: int = 500) -> None:
    entries = sorted(state["seen"].values(), key=lambda e: e["first_seen"], reverse=True)[:limit]
    rows = []
    for e in entries:
        first_source = next(iter(e["sources"]))
        rows.append(
            {
                "company": e["company"],
                "event": EVENT_LABELS.get(e["event"], e["event"]),
                "segment": e["segment"],
                "where": e["exchange"] or e["segment"],
                "first_seen": e["first_seen"][:10],
                "url": e["official_url"] or e["sources"][first_source],
                "official": bool(e["official_url"]),
                "source": label(first_source),
            }
        )
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.replace("__DATA__", data).replace("__UPDATED__", now.strftime("%d %b %Y, %I:%M %p"))
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(page, encoding="utf-8")
