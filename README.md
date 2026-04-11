# Points Deal Finder

Automated award-flight deal finder and daily email digest for travelers who want better signal than raw seats.aero alerts.

This project layers your real point balances, trip windows, transfer bonuses, routing preferences, and layover tolerance on top of [seats.aero](https://seats.aero) availability so the output is not just "award space exists," but "these are the best business-class options you can realistically book right now."

## Why This Exists

seats.aero is excellent at surfacing raw award availability, but it does not know:

- which trips you actually care about
- which transferable currencies you hold
- which transfer bonuses are active
- which routes are too painful to bother with
- which deals are worth emailing versus ignoring

Points Deal Finder adds that layer of personalization and sends a compact digest instead of a firehose.

## What It Does

- Queries seats.aero for business-class award availability across your configured trips
- Searches outbound and return windows independently for round-trip planning
- Calculates the cheapest transfer path from your actual balances
- Incorporates active transfer bonuses from current-bonus pages plus manual overrides
- Scores deals on cost, routing quality, airline quality, urgency, and value floor
- Flags long layovers with hotel-cost and transit context
- Groups results into a styled HTML email digest delivered through Resend
- Supports `active: false` so booked trips stay in config without cluttering the digest

## How It Works

1. Load your trip definitions, balances, and routing preferences from `config.yaml`.
2. Query seats.aero cached availability for matching routes and dates.
3. Pull trip details for the best candidates, within a request budget.
4. Merge live transfer bonuses with any manual bonus overrides.
5. Score and rank the deals using routing, transfer cost, airline quality, and value filters.
6. Add layover analysis for long stops.
7. Render and send the daily digest email.

## Requirements

- Python 3.11+
- A seats.aero Pro account with API access
- A Resend account if you want live email delivery

You can still use the local preview flow without sending email.

## Quick Start

```bash
git clone https://github.com/norangio/travel-points.git
cd travel-points
python -m venv .venv
source .venv/bin/activate
pip install .
cp config.example.yaml config.yaml
```

Set the required environment variables:

- `SEATS_AERO_API_KEY`
- `RESEND_API_KEY`

Optional environment variables:

- `EMAIL_FROM_ADDRESS` — verified Resend sender address; defaults to `onboarding@resend.dev`
- `EMAIL_FROM_NAME` — sender display name
- `SEATS_AERO_REQUEST_DELAY_SECONDS` — minimum spacing between seats.aero requests; default `1.0`
- `SEATS_AERO_MAX_RETRIES` — retry cap for seats.aero requests; default `4`
- `SEATS_AERO_MAX_REQUESTS_PER_RUN` — hard cap for HTTP requests per run; default `800`
- `SEATS_AERO_MAX_TRIP_DETAILS_PER_SEARCH` — max `/trips/{id}` lookups per route search; default `6`
- `TRANSFER_BONUS_SCRAPERS_ENABLED` — enable bonus scraping; default `true`
- `TRANSFER_BONUS_SCRAPER_TIMEOUT_SECONDS` — scraper timeout in seconds; default `15.0`
- `MANUAL_RUN_RECIPIENTS` — override recipients for manual workflow runs
- `EMAIL_RECIPIENTS_OVERRIDE` — override recipients for any run

Run the pipeline locally:

```bash
python -m src.main
```

## Example Config

`config.example.yaml` is the starting point. A minimal trip looks like this:

```yaml
balances:
  chase_ur: 185000
  capital_one: 120000

origins: [SAN, LAX, SFO, SNA]
cabin: business
travelers: 2

trips:
  - name: "Asia Spring 2027"
    active: true
    destinations:
      - region: asia
        preferred_airports: [NRT, HND, ICN, SIN, BKK, HKG]
    outbound:
      earliest: "2027-03-01"
      latest: "2027-03-15"
    return:
      earliest: "2027-04-01"
      latest: "2027-05-31"
    flexibility_days: 5
    priority: high
```

Configuration tips:

- Set `active: false` on trips you already booked but want to keep around.
- Use `priority: high` for must-book trips so they render above opportunistic searches.
- Add an `email_note` for short context under a trip heading in the digest.
- Keep manual `transfer_bonuses` entries for edge cases or scraper misses.

## Deployment

The repo is designed to run as a systemd timer on any Linux host you control.

- `travel-points.service` — oneshot unit that runs `python -m src.main`
- `travel-points.timer` — daily schedule (`*-*-* 00:00:00` UTC)
- `deploy.sh` — local script: pushes to GitHub, SSHes to your host, reinstalls deps, reloads timer
- `deploy/server-deploy.sh` — server-side installer (called by `deploy.sh`)

On the server, place these files under `/opt/travel-points/`:
- `config.yaml` — your personal config (gitignored, edited in place on the server)
- `.env` — `SEATS_AERO_API_KEY`, `RESEND_API_KEY`

The deploy script reads the SSH target from a gitignored local file
(`.deploy-env`) so the repo stays free of personal infra details. See that
file's template in `deploy.sh`.

Common operations:
- Trigger a run now: `systemctl start travel-points.service`
- Tail logs: `journalctl -u travel-points -f`
- Pause scheduled runs: `systemctl stop travel-points.timer`
- Ad-hoc test run without polluting state: `TRAVEL_POINTS_MANUAL=1 /opt/travel-points/venv/bin/python -m src.main`

## Local Preview

You can render the digest locally without calling seats.aero or sending email:

```bash
.venv/bin/python -m src.email.preview
```

That writes HTML and text preview files to a temp directory so you can inspect the layout before enabling the scheduled workflow.

## Project Structure

```text
src/
├── main.py                 # Pipeline orchestrator
├── config.py               # YAML + env config loader
├── models.py               # Core data models
├── state.py                # Cross-run history / freshness tracking
├── sources/
│   ├── seats_aero.py       # seats.aero API client
│   └── transfer_bonuses.py # Bonus loader + current-bonus scrapers
├── scoring/
│   ├── engine.py           # Composite deal scoring
│   ├── transfer_paths.py   # Effective points-cost calculator
│   └── airline_quality.py  # Airline product-tier lookups
├── layover/
│   └── analyzer.py         # Hotel + transit analysis for long layovers
├── email/
│   ├── builder.py          # Email content builder
│   ├── sender.py           # Resend integration
│   └── templates/          # HTML + text templates
└── data/
    ├── transfer_partners.yaml
    ├── airline_products.yaml
    └── layover_cities.yaml
```

## Current Limitations

- seats.aero API access is required; this project is not useful without it.
- Transfer-bonus scraping is best-effort and depends on third-party page structure.
- Cash-price / cents-per-point validation is still basic.
- The digest is optimized for business-class redemption scanning, not general award-search use cases.

## Roadmap

- Better cash-price / CPP enrichment
- Stronger deduplication and ranking refinement
- More opportunistic-search modes
- Alternative delivery channels beyond email

## Development

Install with dev dependencies:

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```
