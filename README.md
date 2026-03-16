# Points Deal Finder

Automated business class award flight deal finder & daily email digest.

Layers intelligence on top of [seats.aero](https://seats.aero) Pro to answer: **"What's the best business class deal I can actually book right now with the points I have?"**

## What It Does

- Queries seats.aero for business class award availability across your defined trips
- Calculates the cheapest transfer path from your actual point balances (Chase UR, Capital One, United)
- Factors in active transfer bonuses (e.g., Chase → Avios 20% = 50k UR books a 60k Avios fare)
- Scores deals on cost, airline quality, routing, bonus urgency, and CPP value
- Analyzes long layovers with hotel costs and transit options
- Sends a styled HTML email digest every evening via Resend

## Setup

1. Copy `config.example.yaml` to `config.yaml` and fill in your balances, trips, and preferences
2. Set environment variables (or create `.env`):
   - `SEATS_AERO_API_KEY` — seats.aero Pro API key
   - `RESEND_API_KEY` — Resend email API key
3. Install: `pip install .`
4. Run manually: `python -m src.main`

## GitHub Actions

The digest runs daily at 7 PM PST via GitHub Actions cron. Required secrets:
- `SEATS_AERO_API_KEY`
- `RESEND_API_KEY`

## Project Structure

```
src/
├── main.py                 # Pipeline orchestrator
├── config.py               # YAML + env config loader
├── models.py               # Data models
├── state.py                # Cross-run deduplication
├── sources/
│   ├── seats_aero.py       # seats.aero API client
│   └── transfer_bonuses.py # Bonus loader (YAML → scrapers in Phase 2)
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
- **Phase 2**: Transfer bonus scrapers (FrequentMiler, TPG, AwardWallet)
- **Phase 3**: Airline quality polish, CPP/cash price lookups, dedup refinement
- **Phase 4**: Opportunistic scanning, Slack alerts, bonus pattern analysis
