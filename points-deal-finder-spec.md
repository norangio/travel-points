# Points Deal Finder — Project Specification

**Project:** Automated business class award flight deal finder & daily digest  
**Author:** norangio  
**Status:** Planning  
**Last Updated:** 2026-03-15

---

## 1. Problem Statement

seats.aero Pro provides solid raw award availability alerts, but falls short in three ways:

1. **No transfer bonus awareness** — it doesn't know that a Chase → Avios 20% bonus makes a 60k Avios fare effectively 50k Chase UR
2. **No personalized cost calculation** — it doesn't know what points you actually hold or can transfer to
3. **Low signal-to-noise** — it surfaces airlines you'd never fly, 18-hour layovers, and routes where you'd rather pay cash

This tool layers intelligence on top of seats.aero's availability data to answer the question: **"What's the best business class deal I can actually book right now with the points I have?"**

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    DAILY CRON (GitHub Actions)           │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ seats.aero   │  │ Transfer     │  │ Config        │  │
│  │ API Client   │  │ Bonus        │  │ Loader        │  │
│  │              │  │ Monitor      │  │               │  │
│  │ - Cached     │  │ - RSS/scrape │  │ - Balances    │  │
│  │   Search     │  │ - Parse      │  │ - Preferences │  │
│  │ - Get Trips  │  │   active     │  │ - Trip defs   │  │
│  │ - Filter J   │  │   promos     │  │ - Airline     │  │
│  │              │  │              │  │   tiers       │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬────────┘  │
│         │                 │                  │           │
│         └────────┬────────┴──────────────────┘           │
│                  ▼                                       │
│         ┌────────────────┐                               │
│         │ Scoring Engine │                               │
│         │                │                               │
│         │ - Effective    │                               │
│         │   points cost  │                               │
│         │ - Product      │                               │
│         │   quality      │                               │
│         │ - Route        │                               │
│         │   penalty      │                               │
│         │ - CPP floor    │                               │
│         └───────┬────────┘                               │
│                 ▼                                        │
│         ┌────────────────┐                               │
│         │ Deal Ranker &  │                               │
│         │ Email Builder  │                               │
│         │                │                               │
│         │ - Top N deals  │                               │
│         │ - HTML email   │                               │
│         │   template     │                               │
│         │ - Resend API   │                               │
│         └────────────────┘                               │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Data Sources

### 3.1 seats.aero Pro API

**Base URL:** `https://seats.aero/partnerapi/`  
**Auth:** `Partner-Authorization` header with API key  
**Rate Limits:** Daily cap, resets at midnight UTC  
**Restriction:** Personal, non-commercial use only

**Key Endpoints:**

| Endpoint | Purpose | Usage |
|---|---|---|
| `GET /search` (Cached Search) | Query award availability by origin/dest/dates/cabin | Primary search — filter to `business` cabin, your origin airports, target destinations |
| `GET /availability` (Bulk Availability) | Pull all availability for a mileage program | Useful for broad opportunistic scanning |
| `GET /trips/{id}` | Get flight-level detail from an availability object | **Critical** — this is where you get routing, layover times, operating carrier |
| `GET /routes` | List all routes for a mileage program | One-time mapping to know what's even bookable |

**Implementation Notes:**
- Cache route maps locally (update weekly) to avoid burning API calls
- Cached Search is the workhorse — query per origin-destination-daterange combo
- Always follow up with Get Trips to get actual routing detail (layover duration, operating carrier)
- seats.aero `source` = mileage program (e.g., `aeroplan`, `united`, `virginatlantic`)

**Relevant Sources for Your Holdings:**
| Your Points | Transferable To | seats.aero Source |
|---|---|---|
| Chase UR | United, Hyatt, BA/Avios, Air France/KLM, Singapore, Southwest, Aeroplan, etc. | `united`, `virginatlantic` (Avios), `aerlingus`, `singapore`, `aeroplan`, etc. |
| Capital One | Air France/KLM, Turkish, BA/Avios, Singapore, Avianca, Cathay, etc. | `flyingblue`, `turkishmiles`, `virginatlantic`, `singapore`, `avianca`, `cathay` |
| United Miles | Direct use | `united` |

### 3.2 Transfer Bonus Monitor

**Strategy:** RSS + periodic web scraping of canonical "current transfer bonuses" pages

**Primary Sources (ranked by reliability/update speed):**

| Source | URL | Method | Why |
|---|---|---|---|
| FrequentMiler | `frequentmiler.com/current-point-transfer-bonuses/` | Scrape (always-current page) | Maintained in real-time, covers all programs |
| The Points Guy | `thepointsguy.com/loyalty-programs/current-transfer-bonuses/` | Scrape + RSS | High-traffic, fast updates |
| AwardWallet | `awardwallet.com/news/credit-card-transfer-bonuses/` | Scrape | Clean tabular format, easy to parse |
| Upgraded Points | `upgradedpoints.com/news/current-credit-card-transfer-bonuses/` | Scrape | Good structured data |
| One Mile at a Time (OMAAT) | RSS feed | RSS | Nick Ewen covers bonuses quickly |

**Data Model — Transfer Bonus:**
```python
@dataclass
class TransferBonus:
    source_program: str          # "chase_ur", "capital_one"
    target_program: str          # "avios", "flying_blue"
    bonus_percentage: float      # 0.20 for 20% bonus
    effective_rate: float        # 1.2 (1:1.2)
    start_date: date
    end_date: date
    source_url: str
    verified: bool               # cross-referenced across 2+ sources
    notes: str                   # e.g., "bonus applied by Lifemiles, not Amex"
```

**Scraping Implementation:**
- Use `httpx` + `beautifulsoup4` for page scraping
- Each source gets its own parser class (pages change format)
- Cross-reference across 2+ sources before marking `verified=True`
- Run 2x daily (morning + evening) — bonuses launch/expire irregularly
- Store historical bonuses in JSON for pattern analysis

### 3.3 Configuration (YAML)

```yaml
# config.yaml

balances:
  chase_ur: 185000
  capital_one: 120000
  united_miles: 45000

origins:
  - SAN
  - LAX
  - SFO
  - SNA

cabin: business  # only business class

travelers: 2  # number of seats needed

# Defined trips with specific windows
trips:
  - name: "Europe Fall 2026"
    destinations:
      - region: europe
        preferred_airports: [LHR, CDG, FCO, BCN, LIS, AMS]
    date_range:
      earliest: "2026-09-15"
      latest: "2026-11-15"
    flexibility_days: 3  # +/- days around ideal dates
    priority: high

  - name: "Asia Spring 2027"
    destinations:
      - region: asia
        preferred_airports: [NRT, HND, ICN, SIN, BKK, HKG]
    date_range:
      earliest: "2027-03-01"
      latest: "2027-05-31"
    flexibility_days: 5
    priority: high

# Opportunistic scanning (always-on)
opportunistic:
  enabled: true
  regions: [europe, asia]
  max_points_per_person: 100000  # your "under 100k" threshold
  lookout_months: 12

# Airline quality tiers
airline_tiers:
  preferred:  # actively seek these out
    - ANA
    - Singapore Airlines
    - Cathay Pacific
    - Japan Airlines
    - Qatar Airways
    - EVA Air
    - Korean Air
    - Swiss
    - Lufthansa (First)
    - Air France (new J)
  neutral:  # show if the deal is good
    - United (Polaris)
    - Turkish Airlines
    - Aeroplan partners
    - British Airways (Club Suite only)
    - KLM
    - TAP Portugal
    - Iberia
    - SAS
    - Asiana
  deprioritized:  # only show if exceptional deal
    - Air India
    - China Southern
    - China Eastern
    - Philippine Airlines
    - Ethiopian Airlines
    - LATAM
    - Air Canada (non-Suite)

# Route quality filters
routing:
  max_connections: 1
  max_total_layover_hours: 6
  max_total_travel_hours: 24   # for Asia, may need to bump to 28
  allow_backtracking: false
  
# Value floor — don't burn points if cash is cheap
value_floor:
  min_cpp: 1.5  # cents per point — below this, just pay cash

# Email
email:
  recipients:
    - nick@example.com
    - krista@example.com
  daily_digest_time: "07:00"  # PT
  max_deals_per_email: 15
  include_transfer_bonus_summary: true
```

---

## 4. Scoring Engine

The scoring engine converts raw availability + transfer bonuses + preferences into a single **deal score** that determines email ranking.

### 4.1 Effective Points Cost

This is the killer calculation — what does this flight *actually* cost you?

```python
def effective_points_cost(
    award_cost: int,              # e.g., 60,000 Avios
    booking_program: str,         # e.g., "avios"
    balances: dict,               # your current balances
    active_bonuses: list,         # active TransferBonus objects
) -> dict:
    """
    Returns the cheapest way to pay for this award from your holdings.
    
    Example: 60k Avios fare
    - If Chase → Avios has 20% bonus: need 50,000 Chase UR
    - If Capital One → Avios at 1:1 (no bonus): need 60,000 Cap1
    - Best path: 50,000 Chase UR
    """
    paths = []
    
    for source_program, balance in balances.items():
        # Check if this program can transfer to the booking program
        transfer_rate = get_base_transfer_rate(source_program, booking_program)
        if transfer_rate is None:
            continue
        
        # Apply any active transfer bonus
        bonus = find_active_bonus(source_program, booking_program, active_bonuses)
        effective_rate = transfer_rate * (1 + bonus.bonus_percentage) if bonus else transfer_rate
        
        # Points needed from this source
        points_needed = math.ceil(award_cost / effective_rate)
        
        # Can you afford it?
        affordable = points_needed <= balance
        
        # For 2 travelers
        points_needed_total = points_needed * 2
        affordable_both = points_needed_total <= balance
        
        paths.append({
            "source": source_program,
            "points_needed_per_person": points_needed,
            "points_needed_total": points_needed_total,
            "has_active_bonus": bonus is not None,
            "bonus_detail": bonus,
            "affordable_one": affordable,
            "affordable_both": affordable_both,
            "effective_rate": effective_rate,
        })
    
    # Also check direct redemption (e.g., United miles for United flights)
    # ...
    
    return sorted(paths, key=lambda p: p["points_needed_per_person"])
```

### 4.2 Composite Deal Score

```python
def deal_score(
    effective_cost: int,        # best-path points per person
    airline_tier: str,          # "preferred" | "neutral" | "deprioritized"
    total_travel_hours: float,
    num_connections: int,
    max_layover_hours: float,
    cash_price: float | None,   # if available from Google Flights or similar
    has_transfer_bonus: bool,
    bonus_expiry_days: int | None,
) -> float:
    """
    Higher score = better deal. Weights are configurable.
    """
    score = 0.0
    
    # Cost component (0-40 points) — lower cost = higher score
    # Normalized against the 100k threshold
    cost_score = max(0, (100_000 - effective_cost) / 100_000) * 40
    score += cost_score
    
    # Airline quality (0-25 points)
    tier_scores = {"preferred": 25, "neutral": 15, "deprioritized": 5}
    score += tier_scores.get(airline_tier, 10)
    
    # Routing quality (0-20 points)
    if num_connections == 0:
        route_score = 20
    elif num_connections == 1 and max_layover_hours <= 3:
        route_score = 15
    elif num_connections == 1:
        route_score = 10
    else:
        route_score = 0
    score += route_score
    
    # Transfer bonus urgency (0-10 points)
    if has_transfer_bonus:
        if bonus_expiry_days and bonus_expiry_days <= 7:
            score += 10  # expiring soon — act now
        else:
            score += 5
    
    # CPP value (0-5 points) — only if cash price is known
    if cash_price and effective_cost > 0:
        cpp = (cash_price * 100) / effective_cost  # cents per point
        if cpp >= 3.0:
            score += 5  # excellent value
        elif cpp >= 2.0:
            score += 3
        elif cpp < 1.5:
            score -= 10  # just pay cash
    
    return round(score, 1)
```

---

## 5. Pipeline Flow (Daily Execution)

### Step 1: Load Config & State
- Parse `config.yaml` for balances, trips, preferences
- Load previous day's results for deduplication (don't re-alert the same seat)

### Step 2: Fetch Transfer Bonuses
- Scrape 3-5 bonus pages
- Parse into `TransferBonus` objects
- Cross-reference for verification
- Compare to yesterday's bonuses → flag new/expiring bonuses

### Step 3: Query seats.aero
For each defined trip + opportunistic scan:
- Build search queries: each (origin, destination, date_range, cabin=J) combo
- Call Cached Search API
- For results that pass basic filters (cabin, connections), call Get Trips for routing detail
- **Rate limit management:** prioritize high-priority trips, then opportunistic

### Step 4: Score & Rank
- For each availability hit:
  - Calculate effective points cost across all your balance → transfer paths
  - Look up airline tier
  - Extract routing quality from trip data
  - Compute composite deal score
- Deduplicate against previous alerts
- Sort by score, take top N

### Step 5: Build & Send Email
- Render HTML email template with:
  - **Transfer Bonus Summary** (top of email): new/active/expiring bonuses relevant to your cards
  - **Top Deals** (main section): ranked deals with score breakdown
  - **Deal Cards** showing: route, airline, dates, points cost, transfer path, layover info, booking link
- Send via Resend API through existing GitHub Actions workflow

### Step 6: Persist State
- Save today's results to JSON (for dedup tomorrow)
- Log API usage (track seats.aero rate limit consumption)

---

## 6. Project Structure

```
points-deal-finder/
├── .github/
│   └── workflows/
│       └── daily-digest.yml          # GitHub Actions cron
├── src/
│   ├── __init__.py
│   ├── main.py                       # Orchestrator
│   ├── config.py                     # YAML loader + validation
│   ├── models.py                     # Dataclasses (Award, TransferBonus, Deal, etc.)
│   │
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── seats_aero.py             # seats.aero API client
│   │   ├── transfer_bonuses.py       # Bonus monitor orchestrator
│   │   └── parsers/
│   │       ├── __init__.py
│   │       ├── frequentmiler.py      # FrequentMiler scraper
│   │       ├── tpg.py                # The Points Guy scraper
│   │       ├── awardwallet.py        # AwardWallet scraper
│   │       └── base.py               # Abstract parser interface
│   │
│   ├── scoring/
│   │   ├── __init__.py
│   │   ├── engine.py                 # Composite scoring
│   │   ├── transfer_paths.py         # Effective cost calculator
│   │   └── airline_quality.py        # Airline/product tier lookups
│   │
│   ├── email/
│   │   ├── __init__.py
│   │   ├── builder.py                # Email content builder
│   │   ├── sender.py                 # Resend integration (match existing)
│   │   └── templates/
│   │       └── daily_digest.html     # Jinja2 email template
│   │
│   └── data/
│       ├── transfer_partners.yaml    # Static: which programs transfer where
│       └── airline_products.yaml     # Static: airline J product ratings
│
├── config.yaml                       # Your personal config (gitignored)
├── config.example.yaml               # Template
├── state/
│   └── last_run.json                 # Previous results for dedup
├── tests/
│   ├── test_scoring.py
│   ├── test_transfer_paths.py
│   └── test_parsers.py
├── pyproject.toml
└── README.md
```

---

## 7. Key Data Files

### 7.1 Transfer Partner Map (`transfer_partners.yaml`)

```yaml
# Which credit card programs transfer to which airline programs
# and at what base rate

chase_ur:
  partners:
    united: { rate: 1.0, instant: true }
    avios: { rate: 1.0, instant: true }      # BA, Aer Lingus, Iberia
    flying_blue: { rate: 1.0, instant: true } # Air France/KLM
    singapore: { rate: 1.0, instant: true }
    aeroplan: { rate: 1.0, instant: true }
    hyatt: { rate: 1.0, instant: true }       # hotel, but useful
    southwest: { rate: 1.0, instant: true }
    virgin_atlantic: { rate: 1.0, instant: true }
    marriott: { rate: 1.0, instant: false }
    ihg: { rate: 1.0, instant: true }

capital_one:
  partners:
    flying_blue: { rate: 1.0, instant: true }
    turkish: { rate: 1.0, instant: true }
    avios: { rate: 1.0, instant: true }
    singapore: { rate: 1.0, instant: true }
    avianca: { rate: 1.0, instant: true }
    cathay: { rate: 1.0, instant: true }
    finnair: { rate: 1.0, instant: true }
    etihad: { rate: 1.0, instant: true }
    emirates: { rate: 1.0, instant: true }
    qantas: { rate: 1.0, instant: true }
    tap: { rate: 1.0, instant: true }
    wyndham: { rate: 1.0, instant: true }

united_miles:
  partners:
    united: { rate: 1.0, instant: true, direct: true }
    # United miles can book Star Alliance partners directly
```

### 7.2 Airline Product Quality (`airline_products.yaml`)

```yaml
# Business class product ratings (1-10)
# These drive the airline_tier in scoring
# Updated periodically based on actual product reviews

products:
  ANA:
    product_name: "THE Room / Inspiration of Japan"
    rating: 9.5
    notes: "772 THE Room is elite. 787 staggered is still excellent."
    tier: preferred
  
  Singapore Airlines:
    product_name: "1-2-1 J / Regional"
    rating: 9.0
    tier: preferred
  
  Cathay Pacific:
    product_name: "Aria Suite / Regional"
    rating: 9.0
    notes: "Aria Suite on A350 is outstanding. Older herringbone still solid."
    tier: preferred
  
  Japan Airlines:
    product_name: "Apex Suite / Sky Suite III"
    rating: 8.5
    tier: preferred
  
  Qatar Airways:
    product_name: "Qsuite"
    rating: 9.5
    notes: "Best J product in the world. Doubles as buddy suite for couples."
    tier: preferred
  
  EVA Air:
    product_name: "Royal Laurel"
    rating: 8.0
    tier: preferred
  
  Korean Air:
    product_name: "Prestige Suite / Prestige Class"
    rating: 8.0
    notes: "New Prestige Suite is great. Older angled flat is rough."
    tier: preferred
  
  United Airlines:
    product_name: "Polaris"
    rating: 7.0
    notes: "Adequate. Window-and-door Polaris on 787-10 and 777s is decent."
    tier: neutral
  
  Turkish Airlines:
    product_name: "Business Class"
    rating: 7.5
    notes: "Great soft product. Hard product improving with new 787s."
    tier: neutral
  
  British Airways:
    product_name: "Club Suite / Club World"
    rating: 7.5
    notes: "Club Suite (A350) is good. Old Club World is awful."
    tier: neutral
  
  Air Canada:
    product_name: "Signature Suite / Signature Class"
    rating: 6.0
    notes: "Signature Suite on 787 is fine. Avoid older reverse herringbone."
    tier: deprioritized
  
  Air India:
    product_name: "Business"
    rating: 4.0
    tier: deprioritized

  # ... extend as needed
```

---

## 8. Email Template Design

Each daily digest has three sections:

### Section 1: Transfer Bonus Alert Bar
- Color-coded urgency: NEW (green), ACTIVE (blue), EXPIRING SOON (red/orange)
- Shows only bonuses relevant to Chase UR, Capital One, United
- Format: "Chase → Avios: 20% bonus (ends Mar 31) — 50k UR books 60k Avios fare"

### Section 2: Top Deals (ranked by score)
Each deal card shows:
```
┌──────────────────────────────────────────────────┐
│  ★ 87.3  |  ANA "THE Room" Business              │
│  LAX → NRT  |  Nonstop  |  Mar 15, 2027          │
│                                                    │
│  💰 75,000 Virgin Atlantic points per person       │
│  📱 YOUR COST: 62,500 Chase UR (20% bonus active) │
│  ✈️  2 seats available                             │
│  💵 Cash price: ~$6,800 → 5.4 cpp value           │
│                                                    │
│  ⚡ Transfer bonus expires in 16 days              │
│  🔗 Book via Virgin Atlantic                       │
└──────────────────────────────────────────────────┘
```

### Section 3: Quick Stats Footer
- Points balances remaining after hypothetical bookings
- Number of routes scanned
- New bonuses this week

---

## 9. GitHub Actions Workflow

```yaml
# .github/workflows/daily-digest.yml
name: Daily Points Digest

on:
  schedule:
    - cron: '0 14 * * *'    # 7:00 AM PT (14:00 UTC)
  workflow_dispatch:          # manual trigger for testing

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run digest
        env:
          SEATS_AERO_API_KEY: ${{ secrets.SEATS_AERO_API_KEY }}
          RESEND_API_KEY: ${{ secrets.RESEND_API_KEY }}
        run: python -m src.main
      
      - name: Persist state
        uses: actions/upload-artifact@v4
        with:
          name: run-state
          path: state/last_run.json
          retention-days: 7
```

**Note:** State persistence across runs needs a strategy. Options:
- GitHub Actions cache
- Commit state file back to repo 
- Use a free-tier KV store (e.g., Upstash Redis, Cloudflare KV)
- Simple: just store in the repo and auto-commit

---

## 10. Dependencies

```
httpx              # async HTTP client for API calls + scraping
beautifulsoup4     # HTML parsing for bonus scrapers
lxml               # fast HTML parser backend
feedparser         # RSS feed parsing
pyyaml             # config loading
jinja2             # email templating
pydantic           # config validation + data models (optional, dataclasses work too)
resend             # email sending (match existing setup)
python-dateutil    # date parsing from scraped content
```

---

## 11. Build Phases

### Phase 1: Foundation (Week 1-2)
- [ ] Project scaffolding, config loader, data models
- [ ] seats.aero API client (Cached Search + Get Trips)
- [ ] Basic scoring engine with effective cost calculation
- [ ] Manual transfer bonus input (YAML-based, no scraping yet)
- [ ] Minimal email template + Resend integration
- [ ] GitHub Actions cron working end-to-end
- **Milestone:** Receiving daily emails with raw award availability, scored by your balances

### Phase 2: Transfer Bonus Intelligence (Week 3-4)
- [ ] FrequentMiler scraper (primary source)
- [ ] TPG + AwardWallet scrapers (cross-reference)
- [ ] Transfer bonus integration into scoring engine
- [ ] Bonus urgency alerts (new, expiring)
- [ ] Enhanced email template with bonus section
- **Milestone:** Emails now factor in active transfer bonuses and flag expiring deals

### Phase 3: Quality & Polish (Week 5-6)
- [ ] Airline product quality database
- [ ] Route quality analysis (layover parsing from Get Trips)
- [ ] CPP value calculation (optional: integrate Google Flights cash price lookup)
- [ ] Deduplication across runs
- [ ] Email template polish (deal cards, responsive design)
- [ ] Historical deal tracking (what did we alert on before?)
- **Milestone:** Production-quality daily digest you'd actually rely on

### Phase 4: Nice-to-Haves (Ongoing)
- [ ] Opportunistic scanning with smart API budget allocation
- [ ] Weekend "deep scan" with broader search parameters
- [ ] Bonus pattern analysis (predict when Chase → Avios bonuses will come back)
- [ ] seats.aero MCP server integration for ad-hoc Claude queries
- [ ] Per-traveler preference profiles (different airline tier weights?)
- [ ] Slack/push notification for exceptional deals (score > 90)

---

## 12. Open Questions & Risks

| Item | Risk Level | Notes |
|---|---|---|
| seats.aero API access | **Medium** | Not all Pro users get API access. Verify your account has the API tab. If not, email support@seats.aero. |
| seats.aero rate limits | **Medium** | Daily cap is unknown without docs access. May need to prioritize queries carefully. Start conservative, measure actual limits. |
| Scraper fragility | **Medium** | Points blog pages change layout. Each parser needs error handling + fallback. Cross-referencing 2+ sources provides resilience. |
| Cash price data | **Low** | Google Flights scraping is against ToS. Options: skip CPP calc, use ITA Matrix manually, or use a flight price API (limited free tiers exist). |
| 2-seat availability | **Medium** | seats.aero may show 1J seat, not 2. Get Trips response needs inspection — verify it returns seat count or if you need to infer. |
| State persistence in GH Actions | **Low** | Artifact upload works but is clunky. Committing state back to repo is simpler for a personal project. |

---

## 13. Example: Why This Tool Beats Raw seats.aero

**Scenario (real, happening right now):**

Chase → Avios has a 20% transfer bonus through March 31, 2026.

Qatar Qsuite LAX → DOH → Europe is bookable through Avios for ~70,000 points per person.

Raw seats.aero would show: "70k Avios, Qatar, LAX-DOH-LHR, Business"

**Your tool would show:**
- "58,334 Chase UR per person (20% bonus active, expires in 16 days)"
- "116,668 Chase UR total for both of you"
- "You have 185,000 Chase UR — you can book this and have 68k left"
- "Qatar Qsuite, rated 9.5/10 — best J product in the world"
- "Cash price ~$8,400/person → 7.2 cpp value — exceptional"
- **Deal Score: 94.2**

That's the difference between "interesting data" and "book this now."
