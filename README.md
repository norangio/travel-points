# Points Deal Finder

Automated business class award flight deal finder & daily email digest.

Layers intelligence on top of [seats.aero](https://seats.aero) Pro to answer: **"What's the best business class deal I can actually book right now with the points I have?"**

## What It Does

- Queries seats.aero for business class award availability across your defined trips
- Calculates the cheapest transfer path from your actual point balances (Chase UR, Capital One, United)
- Factors in active transfer bonuses from current-bonus pages plus any manual overrides in `config.yaml`
- Scores deals on cost, airline quality, routing, bonus urgency, and CPP value
- Analyzes long layovers with hotel costs and transit options
- Sends a styled HTML email digest every evening via Resend

## Setup

1. Copy `config.example.yaml` to `config.yaml` and fill in your balances, trips, and preferences
2. Set environment variables (or create `.env`):
   - `SEATS_AERO_API_KEY` — seats.aero Pro API key
   - `RESEND_API_KEY` — Resend email API key
   - `EMAIL_FROM_ADDRESS` — verified Resend sender address (defaults to `onboarding@resend.dev`)
   - `EMAIL_FROM_NAME` — sender display name
   - `SEATS_AERO_REQUEST_DELAY_SECONDS` — minimum spacing between API calls (default `1.0`)
   - `SEATS_AERO_MAX_REQUESTS_PER_RUN` — hard cap for seats.aero HTTP requests per digest (default `900`)
   - `SEATS_AERO_MAX_TRIP_DETAILS_PER_SEARCH` — max `/trips/{id}` lookups per route search (default `6`)
   - `TRANSFER_BONUS_SCRAPERS_ENABLED` — fetch current bonuses from Frequent Miler / TPG / AwardWallet (default `true`)
   - `TRANSFER_BONUS_SCRAPER_TIMEOUT_SECONDS` — timeout for bonus source fetches (default `15.0`)
3. Install: `pip install .`
4. Run manually: `python -m src.main`

## GitHub Actions

The digest runs on a single GitHub Actions cron at `01:00 UTC`, which is `6:00 PM PDT`
and `5:00 PM PST`. Required secrets:
- `SEATS_AERO_API_KEY`
- `RESEND_API_KEY`

Recommended GitHub Actions variables:
- `EMAIL_FROM_ADDRESS` — use a verified sender if you want delivery beyond the Resend account owner
- `EMAIL_FROM_NAME`
- `MANUAL_RUN_RECIPIENTS` — optional comma-separated list for `workflow_dispatch` runs; if unset, manual runs default to the first recipient in `config.yaml`
- `EMAIL_RECIPIENTS_OVERRIDE` — optional comma-separated override for any run
- `SEATS_AERO_REQUEST_DELAY_SECONDS`
- `SEATS_AERO_MAX_REQUESTS_PER_RUN`
- `SEATS_AERO_MAX_TRIP_DETAILS_PER_SEARCH`
- `TRANSFER_BONUS_SCRAPERS_ENABLED`
- `TRANSFER_BONUS_SCRAPER_TIMEOUT_SECONDS`

If `EMAIL_FROM_ADDRESS` is left unset, the workflow falls back to `onboarding@resend.dev`, which Resend treats as a test sender and typically only delivers to the account owner.

Transfer bonus scraping uses the current-bonus pages from [Frequent Miler](https://frequentmiler.com/current-point-transfer-bonuses/), [The Points Guy](https://thepointsguy.com/loyalty-programs/current-transfer-bonuses/), and [AwardWallet](https://awardwallet.com/news/credit-card-transfer-bonuses/). The scraper is best-effort; if a page fails or changes structure, the digest still runs and falls back to your manual `transfer_bonuses` entries.

## Local Email Preview

You can render the digest locally without hitting seats.aero or sending email:

```bash
.venv/bin/python -m src.email.preview
```

That writes HTML and text preview files to the system temp directory so you can inspect the layout before running the workflow.

## Project Structure

```
src/
├── main.py                 # Pipeline orchestrator
├── config.py               # YAML + env config loader
├── models.py               # Data models
├── state.py                # Cross-run deduplication
├── sources/
│   ├── seats_aero.py       # seats.aero API client
│   └── transfer_bonuses.py # Bonus loader + current-bonus scrapers
├── scoring/
│   ├── engine.py           # Composite deal scoring
│   ├── transfer_paths.py   # Effective points cost calculator
│   └── airline_quality.py  # Airline product tier lookups
├── layover/
│   └── analyzer.py         # Hotel + transit analysis for long layovers
├── email/
│   ├── builder.py          # Email content builder (Jinja2)
│   ├── sender.py           # Resend integration
│   └── templates/          # HTML + text email templates
└── data/
    ├── transfer_partners.yaml   # Credit card → airline transfer map
    ├── airline_products.yaml    # Business class product ratings
    └── layover_cities.yaml      # Airport hotel + transit data
```

## Build Phases

- **Phase 1** (current): Foundation, seats.aero integration, basic scoring, manual bonuses, email
- **Phase 2**: Transfer bonus scrapers and validation
- **Phase 3**: Airline quality polish, CPP/cash price lookups, dedup refinement
- **Phase 4**: Opportunistic scanning, Slack alerts, bonus pattern analysis
