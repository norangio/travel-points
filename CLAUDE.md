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
- **State**: `src/state.py` — deal history tracking (first_seen dates, NOT suppression). Manual `workflow_dispatch` triggers skip state saves to avoid polluting history during testing

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

- `config.yaml` — user config (balances, trips, origins, routing filters, email recipients) — **committed** (repo is private)
- `config.example.yaml` — template with comments explaining format
- `.env` — secrets (SEATS_AERO_API_KEY, RESEND_API_KEY) — gitignored
- GitHub Actions secrets: SEATS_AERO_API_KEY, RESEND_API_KEY

## Trip Config Format

Trips use separate `outbound` + `return` date windows. The system searches both directions independently and labels deals in the email:

```yaml
trips:
  - name: "Portugal/Spain Summer 2026"
    destinations:
      - region: europe
        preferred_airports: [BCN, LIS, MAD, LHR, CDG]
    outbound:
      earliest: "2026-06-04"
      latest: "2026-06-11"
    return:
      earliest: "2026-06-18"
      latest: "2026-06-28"
    flexibility_days: 3
```

- Outbound: searches origin airports → destination airports
- Return: searches destination airports → origin airports (reversed)
- Legacy `date_range` format still works (outbound only)

## Deployment — GitHub Actions

- **Workflow**: `.github/workflows/daily-digest.yml`
- **Schedule**: 3:00 UTC daily (7:00 PM PST) via cron, plus manual `workflow_dispatch`
- **State persistence**: GitHub Actions cache (deal history with first_seen dates)
- **Manual triggers**: `workflow_dispatch` does NOT save state — safe to test anytime without affecting history
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
- [x] Round-trip search (outbound + return with separate date windows)
- [x] Deal history tracking (first_seen dates, freshness badges: NEW / Day N)
- [x] Direction labels (Outbound / Return) in email
- [x] Manual trigger safety (workflow_dispatch skips state writes)

Not yet implemented:
- [ ] Transfer bonus scrapers (FrequentMiler, TPG, AwardWallet) — Phase 2
- [ ] Cash price / CPP calculation — Phase 3
- [ ] Opportunistic scanning — Phase 4
- [ ] Slack/push notifications — Phase 4

## Git Commits

- Use conventional commit format: `type(optional-scope): description`
- Valid types: feat, fix, docs, style, refactor, test, chore, perf, ci, build
- Keep subject line under 72 characters, present tense, lowercase
