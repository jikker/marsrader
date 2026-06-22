# MarsRadar Data

Auto-generated, bilingual (EN / 繁中) digest of **Elon Musk · Tesla · SpaceX · xAI / X** news,
refreshed every 2 hours by Grok (xAI Live Search). Powers the **MarsRadar** iOS app.

> Unofficial fan project. Not affiliated with Elon Musk or any of his companies.
> Summaries are original; each item links back to its original source (fair use).

## Files
- `digests/YYYY-MM-DD.json` — one file per day, items grouped into 5 categories.
- `index.json` — list of available dates.
- `latest.json` — newest day (app reads this first).

## How it updates
`backend/elon_digest.py` runs on a 2-hour cron, then commits the new JSON. Two backends:

- **`cli` (default, no API key)** — calls the local **Grok Build CLI** (`grok -p ...`), which uses your
  Grok subscription. Run it via local cron (`backend/run.sh`) on a machine where `grok` is installed and
  logged in. See `backend/crontab.example`.
- **`api`** — calls the xAI REST API (needs repo secret `XAI_API_KEY` from https://console.x.ai). This is
  what `.github/workflows/digest.yml` uses, because GitHub Actions has no Grok login.

See the parent project README for full setup.

## Category keys
`elon_personal` · `tesla` · `spacex` · `xai_x_platform` · `other`
