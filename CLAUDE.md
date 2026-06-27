# Points Deal Finder — Project Notes

## Architecture

- **Orchestrator**: `src/main.py` — daily pipeline: load config → fetch bonuses → query seats.aero → score deals → analyze layovers → build email → send → save state
- **Config**: `src/config.py` — YAML config (balances, trips, preferences) + pydantic-settings for env secrets
- **Data models**: `src/models.py` — TransferBonus, TransferPath, AwardAvailability, ScoredDeal, LayoverAnalysis, etc.
- **seats.aero client**: `src/sources/seats_aero.py` — async httpx client for Cached Search + Get Trips endpoints
- **Transfer bonuses**: `src/sources/transfer_bonuses.py` — loads manual bonuses plus best-effort scrapes from Frequent Miler, TPG, and AwardWallet
- **Scoring**: `src/scoring/engine.py` (composite 0-100 score), `transfer_paths.py` (effective cost calc), `airline_quality.py` (product tier lookups)
- **Layover analysis**: `src/layover/analyzer.py` — for layovers >4h, looks up hotel costs (3-star+ near airport & city center) and transit options from `src/data/layover_cities.yaml`
- **Email**: `src/email/builder.py` (Jinja2 rendering), `sender.py` (Resend API), templates in `src/email/templates/`
- **State**: `src/state.py` — deal history tracking (first_seen dates, NOT suppression). Ad-hoc manual runs with `TRAVEL_POINTS_MANUAL=1` skip state saves to avoid polluting history during testing

## Static Data Files

- `src/data/transfer_partners.yaml` — credit card program → airline loyalty program transfer map with rates and seats.aero source names
- `src/data/airline_products.yaml` — business class product ratings (1-10) and tier classifications (preferred/neutral/deprioritized)
- `src/data/layover_cities.yaml` — major hub airports with 3-star+ hotel costs, transit options, and tips

## Email Format

Matches the flat Morning Brief editorial styling from email-reports:
- Helvetica/Arial body, dark masthead, restrained blue/gray accents, fluid 640px max-width, inline styles only
- No `<style>` blocks, CSS media queries, or class-dependent styling in production email templates
- MSO conditionals for Outlook desktop
- Sections: Transfer Bonus Alerts → Editorial deal list with compact detail lines → Search Coverage → Balances → Transfer Partner Reference
- Deal list: each deal = route/points headline, date/airline/program subline, direction/stops/seats/affordability/freshness detail line, seats.aero link, plus optional alt paths and layover analysis
- Within each trip section, deal rows render from lowest required points/person to highest required points/person
- Score display removed (scoring engine not yet fleshed out)
- Deal cards removed — all info consolidated into the table
- Transfer bonus labels are expanded for readability (for example, `Avios` is shown as `Avios (British Airways / Iberia / Aer Lingus)`)
- Both HTML and plain text versions

## Configuration

- `config.yaml` — user config (balances, trips, origins, routing filters, email recipients) — **gitignored** (copy from `config.example.yaml`)
- `config.example.yaml` — template with comments explaining format
- `.env` — secrets (SEATS_AERO_API_KEY, RESEND_API_KEY) — gitignored
- `.deploy-env` — local deploy target config (`SERVER`, `REMOTE`) — gitignored

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

## Running Locally

```bash
cd travel-points
pip install .
python -m src.main
```

Email preview without API/email side effects:

```bash
.venv/bin/python -m src.email.preview
```

## seats.aero API Field Reference

The seats.aero Partner API uses specific field names. These were discovered via diagnostic logging:

**Cached Search (`/search`) — raw availability:**
- Keys: `AvailabilityTrips, CreatedAt, Date, FAirlines, ID, JAirlines, JAirlinesRaw, JAvailable, JMileageCost, JRemainingSeats, Route, RouteID, Source, ...`
- Per-cabin prefixed fields: `J` = business, `F` = first, `W` = premium, `Y` = economy (e.g. `JMileageCost`, `JAirlines`, `JRemainingSeats`)
- `Route` is a nested object with: `OriginAirport, DestinationAirport, OriginRegion, DestinationRegion, Distance, Source`
- `JAirlines` = comma-separated carrier codes (e.g. `"QR"`, `"AC, LH"`)

**Trips (`/trips/{id}`) — trip detail:**
- Keys: `Aircraft, ArrivesAt, AvailabilityID, AvailabilitySegments, Cabin, Carriers, Connections, CreatedAt, DepartsAt, DestinationAirport, FlightNumbers, ID, MileageCost, OriginAirport, RemainingSeats, RouteID, Source, Stops, TotalDuration, TotalSegmentDistance, TotalTaxes, UpdatedAt`
- Returns `{"data": [...]}` — a **list** of trip options (take `data[0]`)
- `Carriers` = comma-separated string of operating carriers
- `AvailabilitySegments` (**NOT** "Segments") = list of segment dicts
- `TotalDuration` = total travel time in **minutes**
- `Connections` = number of connections (int)

**Segment objects** (inside `AvailabilitySegments`) — confirmed from live API:
- Keys: `AircraftCode, AircraftName, ArrivesAt, AvailabilityID, AvailabilityTripID, Cabin, CreatedAt, DepartsAt, DestinationAirport, Distance, Duration, FareClass, FlightNumber, ID, Order, OriginAirport, RouteID, Source, UpdatedAt`
- **No `Carrier` key** in segments — carrier info is only at the trip-level `Carriers` field
- Time format: ISO 8601 with timezone (e.g. `2026-06-10T10:00:00+00:00`)

**Rate limiting**: the client now defaults to 1.0s spacing, respects `Retry-After` on 429s, caps trip-detail lookups per route search, and logs a structured `SEATS_AERO_USAGE` summary each run.
**Transfer bonus scraping**: current-bonus pages are fetched at runtime from Frequent Miler, The Points Guy, and AwardWallet; manual `config.yaml` bonuses still merge in and cover edge cases.

## March 16 Findings

- Historical GitHub Actions runs confirmed the previous issue was the seats.aero **daily** quota, not a short-window per-second limit.
- seats.aero resets at **midnight UTC**; on March 16, 2026 the logs showed two separate UTC days each hitting exactly 1,000 successful calls before 429s started.
- The current code reduces usage by prefiltering raw hits, capping trip-detail lookups per route, and enforcing a per-run HTTP request ceiling.

## Transfer Partners

- `src/data/transfer_partners.yaml` — maps Chase UR (11 partners), Capital One (21 partners), United MileagePlus
- Sourced from official Chase/Capital One pages (URLs in YAML comments)
- Key: Capital One can transfer to **Qatar** (1:1), Emirates is **4:3** (rate: 0.75), EVA/JAL are also 4:3
- Chase does NOT transfer to Emirates or Qatar directly
- `seats_aero_source` links each partner to what seats.aero calls that program (e.g. `"avios"`, `"flyingblue"`, `"qatar"`)

## seats.aero API Documentation

- **Getting Started**: https://developers.seats.aero/reference/getting-started-p
- Pro API requires `Partner-Authorization` header with API key
- Daily quota: 1,000 API calls per calendar day, reset at midnight UTC
- The client spaces requests, respects `Retry-After`, and retries 429s with backoff
- Transfer bonus sources:
  - https://frequentmiler.com/current-point-transfer-bonuses/
  - https://thepointsguy.com/loyalty-programs/current-transfer-bonuses/
  - https://awardwallet.com/news/credit-card-transfer-bonuses/

## Current Phase: Phase 2 (Bonus Scraping + Server Deployment Complete)

Implemented:
- [x] Project scaffolding, config loader, data models
- [x] seats.aero API client (Cached Search + Get Trips)
- [x] Basic scoring engine with effective cost calculation
- [x] Manual transfer bonus input (YAML-based)
- [x] Layover analysis for long layovers (>4h) — hotel costs + transit
- [x] Email template + Resend integration (matching Morning Brief style)
- [x] Self-hosted systemd timer deployment (`travel-points.timer` at `00:00 UTC`)
- [x] Round-trip search (outbound + return with separate date windows)
- [x] Deal history tracking (first_seen dates, freshness badges: NEW / Day N)
- [x] Direction labels (Outbound / Return) in email
- [x] Manual trigger safety (`TRAVEL_POINTS_MANUAL=1` skips state writes)
- [x] Trip detail parsing with correct seats.aero field names (AvailabilitySegments, Carriers, TotalDuration)
- [x] Transfer partners updated to match actual Chase UR + Capital One partner lists (including Qatar)
- [x] JAirlines fallback for airline carrier extraction when trip detail unavailable
- [x] Email shows only deal score (0-100), removed confusing airline rating (x/10)
- [x] Local email preview renderer for layout checks without running the workflow
- [x] Quick Look table at the top of the email for route/date/airline/points scanning
- [x] Human-readable transfer bonus labels in email output (for example, Avios family programs)
- [x] Transfer bonus scrapers (Frequent Miler, TPG, AwardWallet) — best-effort runtime fetch

Not yet implemented:
- [ ] Cash price / CPP calculation — Phase 3
- [ ] Opportunistic scanning — Phase 4
- [ ] Slack/push notifications — Phase 4

## Still To Do (next session)

- [ ] Clean up one-shot diagnostic logging (`_LOGGED_RAW_KEYS`, `_LOGGED_TRIP_KEYS` flags in `seats_aero.py`) — useful during development but should be removed or put behind a DEBUG flag eventually
- [ ] Segment-level `Carrier` is NOT in the API response — carrier info only exists at trip-level `Carriers` field. Consider parsing `FlightNumber` (e.g. "QR740") to extract per-segment carrier codes
- [ ] After the first scheduled post-fix run, inspect `SEATS_AERO_USAGE` in `journalctl` logs to confirm real-world request counts under the new caps
- [ ] After the first scheduled post-fix run, check the live delivered email in Gmail/Outlook for final rendering quirks versus the local preview
- [ ] Consider adding more airline products to `src/data/airline_products.yaml` if new carriers show up as "Unknown"
- [ ] The scoring engine weights may need tuning based on real-world deal quality
- [ ] Run is slower now by design because the default spacing is 1.0s and trip-detail lookups are capped; tune the env vars if the logs show plenty of quota headroom

## Adding Screenshots to the README

Screenshots referenced by `README.md` live in `docs/images/`. Workflow when the
user sends a screenshot (either pastes it into Claude Code or gives a file path):

1. Read the file with the Read tool to confirm contents and spot any sensitive
   data (real balances, recipient emails, API tokens visible in browser, etc.)
   — if anything sensitive is visible, ask the user to redact before committing.
2. Copy the file into `docs/images/<kebab-case-name>.png` via Bash `cp`.
3. Update `README.md` to reference it with an HTML `<img>` tag so width is
   controlled:

   ```html
   <img src="docs/images/daily-digest.png" alt="Daily digest email — deal table" width="640" />
   ```

4. Commit both the image and the README change together, conventional commit:
   `docs(readme): add <thing> screenshot`.

Rules:
- Alt text is required — describe what the image shows.
- Use PNG for UI screenshots, JPG for photos.
- Keep files under ~500 KB — resize or re-compress oversized screenshots
  before committing. The repo is public and binary bloat is permanent.
- **NEVER commit screenshots containing real personal data** (balances, email
  recipients, API keys, trip destinations tied to specific dates, etc.) —
  the repo is public. If unsure, redact or regenerate from a test config.

## Git Commits

- Use conventional commit format: `type(optional-scope): description`
- Valid types: feat, fix, docs, style, refactor, test, chore, perf, ci, build
- Keep subject line under 72 characters, present tense, lowercase
