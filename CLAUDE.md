# Points Deal Finder — Project Notes

## Architecture

- **Orchestrator**: `src/main.py` — daily pipeline: load config → fetch bonuses → query seats.aero → score deals → analyze layovers → build email → send → save state
- **Config**: `src/config.py` — YAML config (balances, trips, preferences) + pydantic-settings for env secrets
- **Data models**: `src/models.py` — TransferBonus, TransferPath, AwardAvailability, ScoredDeal, LayoverAnalysis, etc.
- **seats.aero client**: `src/sources/seats_aero.py` — async httpx client for Cached Search + Get Trips endpoints
- **Transfer bonuses**: `src/sources/transfer_bonuses.py` — Phase 1: loaded from config.yaml; Phase 2: scraped from FrequentMiler/TPG
- **Scoring**: `src/scoring/engine.py` (composite 0-100 score), `transfer_paths.py` (effective cost calc), `airline_quality.py` (product tier lookups)
- **Layover analysis**: `src/layover/analyzer.py` — for layovers >4h, looks up hotel costs (3-star+ near airport & city center) and transit options from `src/data/layover_cities.yaml`
- **Email**: `src/email/builder.py` (Jinja2 rendering), `sender.py` (Resend API), templates in `src/email/templates/`
- **State**: `src/state.py` — JSON-based dedup across runs via GitHub Actions cache

## Static Data Files

- `src/data/transfer_partners.yaml` — credit card program → airline loyalty program transfer map with rates and seats.aero source names
- `src/data/airline_products.yaml` — business class product ratings (1-10) and tier classifications (preferred/neutral/deprioritized)
- `src/data/layover_cities.yaml` — major hub airports with 3-star+ hotel costs, transit options, and tips

## Email Format

Matches the Morning Brief email styling from email-reports:
- Calibri font, #0066cc blue headers, fluid 600px max-width, inline styles only
- MSO conditionals for Outlook desktop
- Sections: Transfer Bonus Alerts → Deal Cards (scored, ranked) → Layover Analysis (for long layovers) → Balances Footer
- Both HTML and plain text versions

## Configuration

- `config.yaml` — user config (balances, trips, origins, routing filters, email recipients) — gitignored
- `config.example.yaml` — template with example values
- `.env` — secrets (SEATS_AERO_API_KEY, RESEND_API_KEY) — gitignored
- GitHub Actions secrets: SEATS_AERO_API_KEY, RESEND_API_KEY

## Deployment — GitHub Actions

- **Workflow**: `.github/workflows/daily-digest.yml`
- **Schedule**: 3:00 UTC daily (7:00 PM PST) via cron, plus manual `workflow_dispatch`
- **State persistence**: GitHub Actions cache (deal IDs for cross-day dedup)
- **Required secrets**: `SEATS_AERO_API_KEY`, `RESEND_API_KEY`

## Running Locally

```bash
cd travel-points
pip install .
python -m src.main
```

## Current Phase: Phase 1 (Foundation)

Implemented:
- [x] Project scaffolding, config loader, data models
- [x] seats.aero API client (Cached Search + Get Trips)
- [x] Basic scoring engine with effective cost calculation
- [x] Manual transfer bonus input (YAML-based)
- [x] Layover analysis for long layovers (>4h) — hotel costs + transit
- [x] Email template + Resend integration (matching Morning Brief style)
- [x] GitHub Actions cron (7 PM PST)

Not yet implemented:
- [ ] Transfer bonus scrapers (FrequentMiler, TPG, AwardWallet) — Phase 2
- [ ] Cash price / CPP calculation — Phase 3
- [ ] Opportunistic scanning — Phase 4
- [ ] Slack/push notifications — Phase 4

## Git Commits

- Use conventional commit format: `type(optional-scope): description`
- Valid types: feat, fix, docs, style, refactor, test, chore, perf, ci, build
- Keep subject line under 72 characters, present tense, lowercase
