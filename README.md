# IPO Filing Radar

Built by the research desk at afc | ashmavir Financial Consultants Pvt. Ltd.

Watches official and news sources for new SME / mainboard IPO filings (DRHP and related updates) and posts
alerts to a Telegram channel and, optionally, to WhatsApp. Runs free on GitHub Actions, hourly from about 7 AM to 7 PM IST (resting overnight), so nothing depends on a laptop.

## What it watches

| Source | Role |
|---|---|
| BSE SME DRHP page | Official, but its site never answers GitHub's servers, so it is off by default. Set `RADAR_ENABLE_BSE_SME=1` when running from a machine with an Indian IP |
| SEBI "Draft Offer Documents" | Official. Mainboard filings; alerts carry direct PDF links read from the filing page |
| IPO Platform "DRHP filed" lists (SME and mainboard) | Reads company, exchange (BSE SME / NSE Emerge), filing date, status and the exchange-hosted DRHP file link. Covers NSE Emerge and BSE SME. A status change to "approved" sends an approval alert |
| Chittorgarh IPO news | News trigger only. Alert text is written by this tool and links to the source |
| IPOCentral RSS | News trigger |
| NSE Emerge page and BSE notices API | Not used. Both refuse GitHub's servers (HTTP 403 / no answer) and this tool does not work around blocks. The NSE adapter stays off unless `RADAR_ENABLE_NSE=1` |

A company seen first in news and later in an official source gets a reply under the first alert with the official link.

## One-time setup

1. **Bot.** In Telegram, open @BotFather, send `/newbot`, copy the token. Keep the token private.
2. **Channel.** Create a Telegram channel, add the bot as an administrator with permission to post.
3. **Admin chat.** Message the bot once from your own account. This chat receives health reports and error notices.
   Find your chat ID by opening `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and reading `chat.id`.
4. **GitHub secrets.** Repo, Settings, Secrets and variables, Actions, New repository secret:
   - `TELEGRAM_BOT_TOKEN`: the token from step 1
   - `TELEGRAM_CHANNEL`: `@your_channel_username` for a public channel, or the numeric ID that starts with `-100`
   - `TELEGRAM_ADMIN_CHAT_ID`: the number from step 3
5. **Optional variables** (Settings, Secrets and variables, Actions, Variables):
   - `ALERT_SEGMENTS`: default `sme,unknown,mainboard` (all filings alert). Set it to `sme,unknown` to drop mainboard alerts
   - `ALERT_MAX_AGE_DAYS`: default `45`. A filing dated older than this is saved quietly and never alerted (see below)
   - `RADAR_SITE_URL`: your GitHub Pages address, so alert digests can link to the searchable list
6. **Web page (optional).** Settings, Pages, deploy from branch `main`, folder `/docs`.
7. **First run.** Actions tab, "radar", Run workflow. The admin chat gets a source report.

## WhatsApp alerts (optional)

A second channel beside Telegram, built the same way and sending the same alerts. It stays off until the secrets below
are set, and nothing about Telegram changes either way. It uses Meta's official WhatsApp Business Cloud API.

**What a subscriber receives** (the layout from the notification note):

```
New DRHP Filed!

Company: Example Industries Limited
Date: 25 Nov 2026
Sector: Engineering
Merchant banker: Example Capital Private Limited
DRHP: https://.../example-drhp.pdf
Nuvont website: https://.../#v=filings&r=all&q=Example%20Industries%20Limited

This is an automated alert from afc | ashmavir based on public filings. It is not investment advice.
afc | ashmavir IPO Filing Radar
```

Other events (DRHP refiled, approval, addendum, issue news) use a second template with the same lines and an
`Update:` line on top. A fact that is not known yet reads `Not stated yet`. The website line opens that company's
row on the Filings page.

**How it connects to the website.** The page shows a **Get WhatsApp alerts** button (when `WHATSAPP_JOIN_NUMBER` or
`WHATSAPP_JOIN_URL` is set) and every filing has a **Share on WhatsApp** link that writes the same layout. Messages
link back to the page, and the button links to the WhatsApp number people message to ask to be added.

### Set-up

1. **Meta account.** At developers.facebook.com create an app of type Business and add the WhatsApp product. Meta gives
   a free test number that can message up to five numbers you verify, enough to try everything. For real use, add your
   own number to a WhatsApp Business Account and complete business verification.
2. **Two ids.** On the app's WhatsApp, API Setup page copy the **Phone number ID** and the **WhatsApp Business Account ID**.
3. **A token that does not expire.** In Meta Business Settings, Users, System users, add a system user, assign it the
   WhatsApp account with full control, and generate a token with the permissions `whatsapp_business_messaging` and
   `whatsapp_business_management`. The 24-hour token on the API Setup page stops working after a day.
4. **GitHub secrets** (Settings, Secrets and variables, Actions):
   - `WHATSAPP_TOKEN`: the token from step 3
   - `WHATSAPP_PHONE_ID`: the Phone number ID
   - `WHATSAPP_BUSINESS_ID`: the Business Account ID (only the set-up helper uses it)
   - `WHATSAPP_RECIPIENTS`: the subscribers' numbers, separated by commas or new lines, with country code
     (`+91 98765 43210`). A bare 10-digit number is read as Indian.
5. **Variables** (all optional): `WHATSAPP_JOIN_NUMBER` (the number people message to join; it appears on the page, so
   use the business number), or `WHATSAPP_JOIN_URL` for a `wa.me` link of your own, `WHATSAPP_TEMPLATE_DRHP`
   (default `new_drhp_filed`), `WHATSAPP_TEMPLATE_UPDATE` (default `filing_update`), `WHATSAPP_API_VERSION` (default `v23.0`).
6. **Templates.** Actions, **whatsapp-check**, Run workflow:
   - `preview` sends both layouts, filled with sample facts, to the Telegram admin chat. No Meta account needed.
   - `create_templates` submits both templates to Meta. The text is fixed in `radar/whatsapp.py`
     (`HEADER`, `BODY`, `FOOTER`), category Utility, language English. Meta answers in minutes to a day.
   - `status` shows whether the token works and whether each template is `APPROVED`.
   - `send_test` sends both templates, with sample facts, to the first recipient only.
7. Once `status` says `APPROVED`, the next hourly run sends real alerts.

Changing any template word means a new template and a new approval, so agree the wording first. If Meta says a
template has too many variables for its length, add a sentence of fixed text to `BODY`.

### How delivery behaves

- It sends only alerts Telegram has just announced, once per recipient. Filings found before WhatsApp was switched on,
  baseline and backfill entries, and anything older than 24 hours are never sent.
- A recipient who cannot receive it (not on WhatsApp, or not on the test number's list) is skipped quietly. A
  temporary Meta error is retried on later runs for up to six runs, then dropped.
- A bad or expired token, a locked account or a payment problem stops WhatsApp and sends **one** note to the Telegram
  admin chat, and one more when it recovers. A template that is not approved yet blocks only that template. Alerts
  from the last 24 hours are sent once it is fixed.
- Throttling by Meta pauses the run and continues next hour. Messages per run are capped at 200.
- Progress is saved after every alert, so a killed run does not repeat messages.
- Phone numbers and the token are never printed or stored. The public state file keeps only a short keyed hash per
  recipient. If the token is replaced, alerts from the last 24 hours that were only partly delivered may be sent again.
- `data/diagnostics.json` has a `whatsapp` section: counts sent, retried and undeliverable, error codes, and which
  template is down.
- The catch-up workflow stays Telegram only, because it announces older filings.

### Subscribers and consent

Meta only allows business-started messages to people who agreed to them. The button on the page sends people to your
WhatsApp number with a ready message asking to join. Add their number to `WHATSAPP_RECIPIENTS` once you have that
message. GitHub Actions cannot receive WhatsApp replies on its own, so adding and removing numbers is manual, and a
person who asks to stop should be removed from the secret the same day.

### Cost

Meta charges for each delivered template message, by category and country. An alert to 20 subscribers, 5 times a
day, is 100 messages a day, so check Meta's current India rate card before going live. If Meta decides a template is
Marketing instead of Utility, it costs more per message. The app account also needs a payment method added in Meta
Business Settings.

## How a run behaves

- **First run** saves everything currently listed as a baseline and sends no channel alerts. The admin chat gets a
  report of which sources worked.
- **A source that starts working later** is baselined silently the same way, so you never get a flood of old items.
- **Only fresh filings alert.** Aggregators such as IPO Platform keep adding older companies to their lists, so
  "not seen before" does not mean "just filed". A filing dated more than `ALERT_MAX_AGE_DAYS` ago is stored with
  `backfill: true`, shown on the dashboard, and never sent. An approval alerts only when a company the radar already
  tracks gets approved, not when it first appears already approved.
- **More than 12 new items** in one run are cut to 12 alerts plus one digest.
- **A source failing 3 runs in a row** sends one admin notice, then one more when it recovers.
- **Daily check** (after 9 am IST) tells the admin chat the tool is alive.
- **If Telegram delivery fails**, the item is retried on the next run instead of being lost.

## If a source fails

The **catch-up** workflow (Actions tab, Run workflow, enter a start date) announces filings dated on or after
that day which the silent first-run baseline stored without an alert. Each one is sent once and never repeated.

Run the **connectivity** workflow (Actions tab, Run workflow). It prints only an HTTP status and timing for each
source, so you can tell "the site blocks this server" apart from "the parser is wrong". BSE SME in particular
may refuse connections from GitHub's US servers; that needs a different host, not a parser change.

## Tuning the parsers

The parsers were written without seeing the live pages. Each run keeps what it fetched under `snapshots/` and
uploads it as a workflow artifact for 3 days. If a source reports "0 items" or fails, download the artifact from the
run page and the parser for that source can be fixed against the real page.

## Rules this tool follows

- It does not bypass blocks, CAPTCHAs or logins. A blocked source is reported, not worked around.
- One polite request per source per run, with an honest user agent (set `RADAR_USER_AGENT` to change it).
- Chittorgarh's terms restrict reusing its content, so it is used only as a trigger: the alert wording is this
  tool's own, with a link to the original. Check each site's terms before widening use.
- Information only. Not investment advice and not a recommendation to subscribe to any issue.

## Local test

```
pip install -r requirements.txt pytest
python -m pytest -q
python -m radar.run   # without a token it prints messages instead of sending them
```

## Dashboard

`docs/index.html` is rebuilt on every run and served by GitHub Pages. It opens on the last 90 days, with filters for
period, segment, exchange, status and sector, a sector heatmap, charts of filings over time, listing venues, sectors,
lead managers and cities, and a searchable table with document links and a CSV download. The page is one static file:
it makes no network calls when opened. With a WhatsApp join link set, the header shows **Get WhatsApp alerts**, and
each filing has a **Share on WhatsApp** link (see above).

The page has five tabs, in this order. **IPO Desk** opens first. **Filings** is the logbook view described above. The
other three are built from market data.

### IPO Desk, Sector heatmap, Sector flow and Commodities

| Tab | What it shows | Source | Refreshed |
|---|---|---|---|
| IPO Desk | Open, upcoming and recently closed issues as cards or a table. Each shows the grey market premium, price band, issue size (in crores), lot size, minimum investment, key dates (opens, closes, allotment, refunds start, demat credit, listing), lead managers (merchant bankers), registrar, sector and exchange. A calendar of the next 14 days of dates sits on top. Filters: segment, status, exchange, sector, lead manager, key dates (closing in 3 days, opening in 7, allotment in 7, listing in 7), premium, search, and sort | ipowatch.in (table and issue pages), with sector and lead managers filled from the Filings data when an issue page lacks them | Every hourly `radar` run |
| Sector heatmap | Median 1D, 1W, 1M, 3M, 6M, 1Y, YTD and 2Y return of the stocks in each NSE industry, with the stocks themselves one click away | NSE Nifty 500 list and Yahoo Finance daily closes | `market` workflow, 16:37 and 19:07 IST on weekdays |
| Sector flow | Estimated net buying pressure per sector over 1, 5 and 20 sessions, in crores, for the NSE F&O stocks: net flow, strength (net as a share of turnover), turnover, advancers and decliners, a 20-session daily sparkline, and the stocks in each sector. Turnover is closing price times volume. It counts as inflow on a day the stock closes up and outflow on a day it closes down, so it is an estimate from prices and volumes, not a record of what any fund traded | NSE F&O lot file and Yahoo Finance closes and volumes | `market` workflow |
| Commodities | 27 futures in five groups (precious metals, energy, industrial metals, grains and oilseeds, softs and livestock) with price, 1D, 1W, 1M, 3M, 1Y and YTD change, a 52-week range and a sparkline. Gauges for USD/INR, the dollar index, the US 10-year yield and India VIX. Gold and silver in rupees at international parity (before duty and GST, so not the MCX rate) | Yahoo Finance | Every hourly `radar` run |

Market breadth was removed from the page and its backend. Old links to `#v=ipos` open the IPO Desk.

How the backend protects the page (`radar/market.py`, `radar/ipowatch.py`):

- Nothing replaces the saved data until every check passes: the NSE list has at least 400 stocks, at least 85% of it
  has a fresh close, the latest session is not stale, and the new data is not older than the saved data.
- A stock with a one-day move over 50%, a return beyond 50x, or no close on the latest session is left out and listed
  in `data/diagnostics.json`. It never fails the whole update.
- Trading sessions are read from the stock prices themselves, so a late index feed cannot hold the page back.
- If the price site starts refusing requests (more than 30% of the first 30 or more answers are 429 or 403), the job
  stops instead of pushing through, and keeps the last good data.
- Issue sizes quoted in lakhs or thousands are converted to crores. Per-share prices, premiums and minimum investment
  stay in rupees. A premium above 5 times the price is dropped as a parsing slip.
- `data/diagnostics.json` is a public, secret-free health report: counts, dates and a few example errors for each job.
  Read it first when a tab looks wrong.
- Issue-page dates are cross-checked: allotment within 7 days of close, refunds and demat credit within 10, listing
  within 14, and each step not before the one before it. A date that fails is dropped, never shown. If the issue page
  and the table disagree on the open or close date, the table wins and the conflict is counted in the diagnostics.
- Sector flow needs at least 15 of the last 20 sessions of volume for a stock, drops a stock with a day above
  Rs 50,000 crore as a data slip, and is left out altogether when fewer than half of the F&O stocks are usable.
- Commodities are published only when at least half of the symbols pass: 60 or more daily bars, a last bar not older
  than 6 days, no daily move above 45% in the last 10 sessions (older jumps from contract rolls stay in the history), and a plausible price. Gold, silver, oil and USD/INR also have price ranges.
- The grey market premium is unofficial and shown with that warning. Nothing here is a recommendation.

Run the market job by hand from the Actions tab: choose **market**, then **Run workflow**. Data files: `data/market.json`,
`data/fno.json` (cached F&O list), `data/ipos.json`, `data/commodities.json`, `data/diagnostics.json`.

## Security

- **Secrets** live only in GitHub Actions secrets and are never printed. The repo and its full history were scanned for
  tokens and chat ids and hold none. Keep it that way: the repo is public, so its Actions logs are public too.
- **Dashboard hardening.** A Content Security Policy in the page allows only its own one script (matched by hash),
  blocks every outside request and form, and sends no referrer. Names and links from outside sources are written as
  plain text, only `http(s)` links are kept, and links open with `rel="noopener noreferrer"`.
- **Workflows** run with the least permission they need (`contents: read`, and `contents: write` only where state is
  committed). Third-party actions are pinned to exact commit SHAs, and Dependabot proposes updates weekly.
- **The bot** should be an administrator of the channel with only the "Post messages" right.
- **WhatsApp.** The token and recipient list are Actions secrets only. The page carries nothing but the join link
  (a `wa.me` address, checked before it is used). Recipients are never written to the repo.
- **Public page.** GitHub Pages on a public repo is visible to anyone with the link. The page is marked `noindex`, but
  keep the repo private (Pages then needs a paid plan) if the content should not be public.
