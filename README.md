# IPO Filing Radar

Built by the research desk at afc | ashmavir Financial Consultants Pvt. Ltd.

Watches official and news sources for new SME / mainboard IPO filings (DRHP and related updates) and posts
alerts to a Telegram channel. Runs free on GitHub Actions, hourly from about 7 AM to 7 PM IST (resting overnight), so nothing depends on a laptop.

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
it makes no network calls when opened.

The page has four tabs. **Filings** is the view above. The other three are built from market data and are described
below.

### Sector heatmap, IPO pipeline and market breadth

| Tab | What it shows | Source | Refreshed |
|---|---|---|---|
| Sector heatmap | Median 1D, 1W, 1M, 3M, 6M, 1Y, YTD and 2Y return of the stocks in each NSE industry, with the stocks themselves one click away | NSE Nifty 500 list and Yahoo Finance daily closes | `market` workflow, 16:37 and 19:07 IST on weekdays |
| Market breadth | Session by session: stocks up or down 4%, 5 and 10 day thrust ratios, 25% quarter and month moves, 13% moves over 34 days, share above the 40-day average, Nifty 50 | Same closes | Same |
| IPO pipeline | Open, upcoming and recently closed issues with grey market premium, price band, issue size (in crores), lot size, minimum investment and dates | ipowatch.in | Every hourly `radar` run |

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
- The grey market premium is unofficial and shown with that warning. Nothing here is a recommendation.

Run it by hand from the Actions tab: choose **market**, then **Run workflow**.

## Security

- **Secrets** live only in GitHub Actions secrets and are never printed. The repo and its full history were scanned for
  tokens and chat ids and hold none. Keep it that way: the repo is public, so its Actions logs are public too.
- **Dashboard hardening.** A Content Security Policy in the page allows only its own one script (matched by hash),
  blocks every outside request and form, and sends no referrer. Names and links from outside sources are written as
  plain text, only `http(s)` links are kept, and links open with `rel="noopener noreferrer"`.
- **Workflows** run with the least permission they need (`contents: read`, and `contents: write` only where state is
  committed). Third-party actions are pinned to exact commit SHAs, and Dependabot proposes updates weekly.
- **The bot** should be an administrator of the channel with only the "Post messages" right.
- **Public page.** GitHub Pages on a public repo is visible to anyone with the link. The page is marked `noindex`, but
  keep the repo private (Pages then needs a paid plan) if the content should not be public.
