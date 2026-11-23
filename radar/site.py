"""Build the static Nuvont IPO & Sector Heatmap dashboard into docs/index.html."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import EVENT_LABELS, label


def _pick_url(entry: dict) -> tuple[str, bool, str]:
    docs = entry.get("documents") or []
    if docs and docs[0].get("url"):
        return docs[0]["url"], True, docs[0].get("label") or "Official document"
    if entry.get("official_url"):
        return entry["official_url"], True, "Official filing"
    sources = entry.get("sources") or {}
    if sources:
        src, url = next(iter(sources.items()))
        return url, False, f"Report ({label(src)})"
    return "", False, ""


def _build_rows(state: dict) -> list[dict]:
    rows = []
    for key, e in state.get("seen", {}).items():
        url, official, link_label = _pick_url(e)
        info = e.get("info") or {}
        rows.append({
            "id": key,
            "company": e.get("company", ""),
            "event": e.get("event", "drhp_filed"),
            "event_label": EVENT_LABELS.get(e.get("event", ""), e.get("event", "")),
            "segment": e.get("segment", "unknown"),
            "exchange": e.get("exchange") or e.get("segment", "").upper(),
            "filed_on": e.get("filed_on") or "",
            "first_seen": (e.get("first_seen") or "")[:10],
            "sector": info.get("sector") or "General",
            "city": info.get("city") or "",
            "bankers": info.get("bankers") or "",
            "url": url,
            "official": official,
            "link_label": link_label,
        })
    rows.sort(key=lambda r: (r["filed_on"] or r["first_seen"], r["company"]), reverse=True)
    return rows


def _safe_json(obj: object) -> str:
    # Escape '</' so company names like 'Evil </script> Ltd' never break the single <script> tag.
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build_site(state: dict, out_path: str, now: datetime) -> None:
    updated = now.strftime("%d %b %Y, %I:%M %p IST")
    payload = {
        "updated": updated,
        "ipos": _build_rows(state),
        "sectors": state.get("sectors") or {"updated": updated, "groups": {}},
        "health": state.get("health") or {},
    }
    html = _TEMPLATE.replace("__PAYLOAD__", _safe_json(payload)).replace("__UPDATED__", updated)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nuvont IPO &amp; Sector Tracker</title>
<style>
:root{--bg:#f8fafc;--card:#ffffff;--fg:#0f172a;--mut:#64748b;--line:#e2e8f0;--acc:#4f46e5;--pos-bg:#ecfdf5;--pos-fg:#047857;--neg-bg:#fef2f2;--neg-fg:#b91c1c}
*{box-sizing:border-box}
body{font:14px/1.5 system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--fg);margin:0}
header{background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:20}
.wrap{max-width:1280px;margin:0 auto;padding:0 16px}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px}
.brand h1{font-size:18px;margin:0;font-weight:700}
.badge{font-size:12px;padding:3px 9px;border-radius:999px;background:#eef2ff;color:var(--acc);font-weight:600}
.live{font-size:12px;color:var(--pos-fg);background:var(--pos-bg);padding:4px 10px;border-radius:999px;font-weight:600}
.tabs{display:flex;gap:6px}
.tab{border:1px solid var(--line);background:var(--card);color:var(--mut);padding:7px 14px;border-radius:8px;cursor:pointer;font-weight:600}
.tab.active{background:var(--fg);color:#fff;border-color:var(--fg)}
main{padding:20px 0 48px}
.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
input,select{font:inherit;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
input{flex:1;min-width:220px}
.pills{display:flex;gap:6px;flex-wrap:wrap}
.pill{border:1px solid var(--line);background:var(--card);padding:6px 12px;border-radius:999px;cursor:pointer;font-size:13px;font-weight:600;color:var(--mut)}
.pill.active{background:var(--fg);color:#fff;border-color:var(--fg)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:10px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.card-top{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12px}
.tag{padding:2px 8px;border-radius:6px;background:#f1f5f9;color:#334155;font-weight:600}
.status{font-weight:600;color:#d97706}
.status.approval{color:var(--pos-fg)}
.title{font-size:15px;font-weight:700;margin:0;line-height:1.3}
.panel{background:var(--pos-bg);border-radius:10px;padding:10px 12px}
.panel.muted{background:#f1f5f9;color:var(--mut)}
.panel .big{font-size:15px;font-weight:700;color:var(--pos-fg)}
.panel.muted .big{color:#475569}
.meta{display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px}
.meta div span{display:block;color:var(--mut);font-size:11px}
.meta div b{font-weight:600;color:var(--fg)}
.card-foot{display:flex;justify-content:space-between;align-items:center;margin-top:auto;padding-top:8px;border-top:1px solid var(--line);font-size:12px}
a{color:var(--acc);text-decoration:none;font-weight:600}
a:hover{text-decoration:underline}
.sec-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}
.sec-card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.sec-card h3{margin:0 0 10px;font-size:13px;letter-spacing:.04em;color:var(--mut)}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:6px 4px;text-align:right;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left;font-weight:600}
.cell{border-radius:6px;padding:4px 6px;display:inline-block;min-width:48px;text-align:center;font-weight:700}
.pos{background:var(--pos-bg);color:var(--pos-fg)}
.neg{background:var(--neg-bg);color:var(--neg-fg)}
.neu{background:#f1f5f9;color:var(--mut)}
.hide{display:none}
.task-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:8px}
footer{margin-top:32px;padding:16px 0;border-top:1px solid var(--line);color:var(--mut);font-size:12px}
</style>
</head>
<body>
<header>
  <div class="wrap top">
    <div class="brand">
      <h1>Nuvont IPO Platform Tracker</h1>
      <span class="badge" id="count-badge">0 filings</span>
      <span class="live">Auto-updated &middot; __UPDATED__</span>
    </div>
    <div class="tabs">
      <button class="tab active" data-tab="ipos">IPO &amp; DRHP Watch</button>
      <button class="tab" data-tab="sectors">Sector Heatmap</button>
      <button class="tab" data-tab="team">Team Tracker</button>
    </div>
  </div>
</header>

<main class="wrap">
  <section id="view-ipos">
    <div class="bar">
      <input id="q" placeholder="Search company, sector, city, or banker..." aria-label="Search">
      <div class="pills" id="seg-pills">
        <button class="pill active" data-seg="">All</button>
        <button class="pill" data-seg="mainboard">Mainboard</button>
        <button class="pill" data-seg="sme">SME</button>
      </div>
      <select id="evt">
        <option value="">All events</option>
        <option value="drhp_filed">DRHP Filed</option>
        <option value="approval">Approved</option>
        <option value="drhp_refiled">DRHP Refiled</option>
        <option value="addendum">Addendum</option>
      </select>
    </div>
    <div class="grid" id="ipo-grid"></div>
  </section>

  <section id="view-sectors" class="hide">
    <div class="bar">
      <span style="color:var(--mut)">NSE sector index returns auto-refreshed on every hourly GitHub Actions run.</span>
    </div>
    <div class="sec-grid" id="sec-grid"></div>
  </section>

  <section id="view-team" class="hide">
    <div class="bar">
      <input id="task-input" placeholder="Add a team deliverable or IPO research task...">
      <button class="tab active" id="task-add">Add Task</button>
    </div>
    <div id="task-list"></div>
  </section>

  <footer>Information only, collected from public exchange, SEBI, and IPO sources. Not investment advice.</footer>
</main>

<script>
const PAYLOAD = __PAYLOAD__;
const HORIZONS = ["1D","1W","1M","3M","6M","1Y","3Y"];
let activeSeg = "";

document.getElementById("count-badge").textContent = PAYLOAD.ipos.length + " in pipeline";

document.querySelectorAll(".tab[data-tab]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab[data-tab]").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    ["ipos","sectors","team"].forEach(id => {
      document.getElementById("view-" + id).classList.toggle("hide", btn.dataset.tab !== id);
    });
  });
});

document.querySelectorAll("#seg-pills .pill").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#seg-pills .pill").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    activeSeg = btn.dataset.seg;
    renderIPOs();
  });
});

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function renderIPOs() {
  const q = document.getElementById("q").value.toLowerCase();
  const evt = document.getElementById("evt").value;
  const grid = document.getElementById("ipo-grid");
  grid.textContent = "";
  const list = PAYLOAD.ipos.filter(r => {
    if (activeSeg && r.segment !== activeSeg) return false;
    if (evt && r.event !== evt) return false;
    if (!q) return true;
    return (r.company + " " + r.sector + " " + r.city + " " + r.bankers).toLowerCase().includes(q);
  }).slice(0, 120);

  list.forEach(r => {
    const card = el("div", "card");
    const top = el("div", "card-top");
    top.appendChild(el("span", "status " + r.event, "• " + r.event_label));
    top.appendChild(el("span", "tag", r.exchange || r.segment.toUpperCase()));
    card.appendChild(top);

    card.appendChild(el("h2", "title", r.company));

    const panel = el("div", "panel" + (r.event === "approval" ? "" : " muted"));
    panel.appendChild(el("div", "big", r.sector));
    panel.appendChild(el("div", "", r.city ? "HQ: " + r.city : "Filed: " + (r.filed_on || r.first_seen)));
    card.appendChild(panel);

    const meta = el("div", "meta");
    const d1 = el("div"); d1.appendChild(el("span", "", "Filed / Seen")); d1.appendChild(el("b", "", r.filed_on || r.first_seen));
    const d2 = el("div"); d2.appendChild(el("span", "", "Segment")); d2.appendChild(el("b", "", r.segment.toUpperCase()));
    meta.appendChild(d1); meta.appendChild(d2);
    card.appendChild(meta);

    if (r.bankers) {
      const bk = el("div", "meta");
      const d3 = el("div"); d3.style.gridColumn = "1 / -1";
      d3.appendChild(el("span", "", "Lead Manager(s)"));
      d3.appendChild(el("b", "", r.bankers));
      bk.appendChild(d3);
      card.appendChild(bk);
    }

    const foot = el("div", "card-foot");
    foot.appendChild(el("span", "", r.official ? "Official filing" : "News source"));
    if (/^https?:\\/\\//.test(r.url)) {
      const a = el("a", "", r.link_label + " ↗");
      a.href = r.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      foot.appendChild(a);
    }
    card.appendChild(foot);
    grid.appendChild(card);
  });
}

function renderSectors() {
  const grid = document.getElementById("sec-grid");
  grid.textContent = "";
  const groups = (PAYLOAD.sectors && PAYLOAD.sectors.groups) || {};
  const keys = Object.keys(groups);
  if (!keys.length) {
    grid.appendChild(el("div", "sec-card", "Sector data will populate automatically after the next radar.yml workflow run."));
    return;
  }
  keys.forEach(g => {
    const card = el("div", "sec-card");
    card.appendChild(el("h3", "", g));
    const tbl = el("table");
    const thead = el("thead");
    const hr = el("tr");
    hr.appendChild(el("th", "", "Index"));
    HORIZONS.forEach(h => hr.appendChild(el("th", "", h)));
    thead.appendChild(hr); tbl.appendChild(thead);

    const tb = el("tbody");
    groups[g].forEach(row => {
      const tr = el("tr");
      tr.appendChild(el("td", "", row.name));
      HORIZONS.forEach(h => {
        const td = el("td");
        const v = row.returns ? row.returns[h] : null;
        const cls = v === null || v === undefined ? "neu" : (v >= 0 ? "pos" : "neg");
        const txt = v === null || v === undefined ? "—" : ((v > 0 ? "+" : "") + v.toFixed(1) + "%");
        td.appendChild(el("span", "cell " + cls, txt));
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    card.appendChild(tbl);
    grid.appendChild(card);
  });
}

const STORE_KEY = "nuvont_team_tasks_v1";
let tasks = JSON.parse(localStorage.getItem(STORE_KEY) || "null") || [
  { text: "Review new SME & Mainboard DRHP filings", done: false },
  { text: "Check Sector Heatmap leaders before IPO allocation", done: false }
];

function renderTasks() {
  localStorage.setItem(STORE_KEY, JSON.stringify(tasks));
  const box = document.getElementById("task-list");
  box.textContent = "";
  tasks.forEach((t, i) => {
    const row = el("div", "task-row");
    const left = el("label", "");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = t.done; cb.style.marginRight = "8px";
    cb.addEventListener("change", () => { tasks[i].done = cb.checked; renderTasks(); });
    left.appendChild(cb);
    const span = el("span", "", t.text);
    if (t.done) span.style.textDecoration = "line-through";
    left.appendChild(span);
    row.appendChild(left);
    const del = el("button", "pill", "Remove");
    del.addEventListener("click", () => { tasks.splice(i, 1); renderTasks(); });
    row.appendChild(del);
    box.appendChild(row);
  });
}

document.getElementById("task-add").addEventListener("click", () => {
  const inp = document.getElementById("task-input");
  if (!inp.value.trim()) return;
  tasks.unshift({ text: inp.value.trim(), done: false });
  inp.value = "";
  renderTasks();
});

document.getElementById("q").addEventListener("input", renderIPOs);
document.getElementById("evt").addEventListener("change", renderIPOs);
renderIPOs();
renderSectors();
renderTasks();
</script>
</body>
</html>
"""
