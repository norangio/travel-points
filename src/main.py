"""Main orchestrator — daily pipeline: config → fetch → score → email."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta

from src.config import (
    get_settings,
    load_transfer_partners,
    load_yaml_config,
)
from src.email.builder import build_digest_email
from src.email.sender import EmailSender
from src.layover.analyzer import analyze_all_layovers
from src.models import AwardAvailability, ScoredDeal, TransferPath
from src.scoring.airline_quality import get_tier_for_carriers
from src.scoring.engine import score_deal
from src.scoring.transfer_paths import calculate_transfer_paths
from src.sources.seats_aero import SeatsAeroClient, parse_availability
from src.sources.transfer_bonuses import load_transfer_bonuses
from src.state import (
    days_seen,
    get_first_seen,
    is_manual_trigger,
    load_deal_history,
    save_state,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


TRIP_PRIORITY_RANK = {
    "high": 0,
    "medium": 1,
    "low": 2,
}


@dataclass
class RouteCandidate:
    """A raw search hit worth spending a trip-detail lookup on."""

    raw: dict
    availability: AwardAvailability
    transfer_paths: list[TransferPath]


def _make_deal_key(avail) -> str:
    """Consistent deal key for history tracking."""
    return (
        f"{avail.origin}-{avail.destination}-"
        f"{avail.departure_date}-{avail.source}-"
        f"{avail.points_cost}"
    )


async def run_digest() -> None:
    """Execute the full daily digest pipeline."""
    logger.info("=== Points Deal Finder — Starting daily digest ===")

    # Step 1: Load config
    settings = get_settings()
    config = load_yaml_config()
    transfer_partners = load_transfer_partners()
    balances = config.get("balances", {})
    travelers = config.get("travelers", 2)
    origins = config.get("origins", [])
    all_trips = config.get("trips", [])
    trips = [trip for trip in all_trips if trip.get("active", True)]
    trips_for_scan = _trip_scan_order(trips)
    skipped_trips = len(all_trips) - len(trips)
    if skipped_trips:
        logger.info("Skipping %s inactive trip(s) from config", skipped_trips)
    routing_config = config.get("routing", {})
    email_config = config.get("email", {})
    max_deals = email_config.get("max_deals_per_email", 15)
    planned_search_calls = _count_planned_search_calls(trips_for_scan, origins)

    if not settings.seats_aero_api_key:
        logger.error("SEATS_AERO_API_KEY not set — cannot fetch availability")
        return

    # Step 2: Load transfer bonuses from config + current-bonus pages
    bonuses = load_transfer_bonuses(
        config,
        transfer_partners,
        enable_scrapers=settings.transfer_bonus_scrapers_enabled,
        timeout_seconds=settings.transfer_bonus_scraper_timeout_seconds,
    )
    logger.info(f"Loaded {len(bonuses)} active transfer bonuses")

    # Step 3: Load deal history (for first_seen tracking, NOT suppression)
    history = load_deal_history()

    # Step 4: Query seats.aero for each trip (outbound + return)
    max_requests_per_run = settings.seats_aero_max_requests_per_run
    max_trip_details_per_search = settings.seats_aero_max_trip_details_per_search
    logger.info(
        "seats.aero plan: %s search requests scheduled, cap %s HTTP requests/run, "
        "max %s trip lookups per search, %.1fs minimum spacing",
        planned_search_calls,
        max_requests_per_run,
        max_trip_details_per_search,
        settings.seats_aero_request_delay_seconds,
    )

    client = SeatsAeroClient(
        settings.seats_aero_api_key,
        request_delay_seconds=settings.seats_aero_request_delay_seconds,
        max_retries=settings.seats_aero_max_retries,
    )
    all_deals: list[ScoredDeal] = []
    logical_api_calls = 0
    budget_exhausted = False

    search_stats: list[dict] = []  # per-route search summary

    try:
        for trip in trips_for_scan:
            trip_name = trip.get("name", "Unnamed Trip")
            destinations = trip.get("destinations", [])
            flex = trip.get("flexibility_days", 0)
            is_opportunistic = _is_opportunistic_trip(trip)

            # Collect destination airports
            dest_airports = []
            for dest_group in destinations:
                dest_airports.extend(dest_group.get("preferred_airports", []))

            # Build search legs: outbound and return
            search_legs = _build_search_legs(trip, origins, dest_airports, flex)

            for leg in search_legs:
                logger.info(
                    f"Trip: {trip_name} ({leg['direction']}) — "
                    f"{len(leg['from_airports'])} origins × "
                    f"{len(leg['to_airports'])} destinations, "
                    f"{leg['earliest']} to {leg['latest']}"
                )

                for from_apt in leg["from_airports"]:
                    for to_apt in leg["to_airports"]:
                        if _request_budget_exhausted(client, max_requests_per_run):
                            budget_exhausted = True
                            logger.warning(
                                "Stopping scan after %s HTTP requests to stay within the run cap of %s",
                                client.stats.total_http_requests,
                                max_requests_per_run,
                            )
                            break

                        results = await client.cached_search(
                            origin=from_apt,
                            destination=to_apt,
                            cabin=config.get("cabin", "business"),
                            start_date=leg["earliest"],
                            end_date=leg["latest"],
                        )
                        logical_api_calls += 1

                        candidates, route_filters = _build_route_candidates(
                            raw_results=results,
                            balances=balances,
                            transfer_partners=transfer_partners,
                            bonuses=bonuses,
                            travelers=travelers,
                        )
                        detail_cap = _trip_detail_lookup_cap(
                            is_opportunistic=is_opportunistic,
                            default_cap=max_trip_details_per_search,
                            request_cap=max_requests_per_run,
                        )
                        trip_candidates = candidates[:detail_cap]
                        skipped_by_cap = max(0, len(candidates) - len(trip_candidates))

                        route_deals = 0
                        trip_details_fetched = 0
                        for candidate in trip_candidates:
                            if _request_budget_exhausted(client, max_requests_per_run):
                                budget_exhausted = True
                                logger.warning(
                                    "Stopping trip lookups after %s HTTP requests to stay within the run cap of %s",
                                    client.stats.total_http_requests,
                                    max_requests_per_run,
                                )
                                break

                            deal = await _process_result(
                                raw=candidate.raw,
                                client=client,
                                config=config,
                                routing_config=routing_config,
                                balances=balances,
                                transfer_partners=transfer_partners,
                                bonuses=bonuses,
                                travelers=travelers,
                                history=history,
                                trip_name=trip_name,
                                direction=leg["direction"],
                                availability=candidate.availability,
                                precomputed_paths=candidate.transfer_paths,
                            )
                            logical_api_calls += 1
                            trip_details_fetched += 1
                            if deal:
                                all_deals.append(deal)
                                route_deals += 1

                        if skipped_by_cap:
                            logger.info(
                                "Route %s → %s (%s) had %s trip-detail candidates; limited to %s for quota control",
                                from_apt,
                                to_apt,
                                leg["direction"],
                                len(candidates),
                                max_trip_details_per_search,
                            )

                        search_stats.append({
                            "route": f"{from_apt} → {to_apt}",
                            "direction": leg["direction"].capitalize(),
                            "trip_name": trip_name,
                            "raw_results": len(results),
                            "candidate_results": len(candidates),
                            "trip_details_fetched": trip_details_fetched,
                            "qualifying_deals": route_deals,
                            "filtered_zero_cost": route_filters["zero_cost"],
                            "filtered_insufficient_seats": route_filters["insufficient_seats"],
                            "filtered_no_paths": route_filters["no_paths"],
                            "skipped_by_lookup_cap": skipped_by_cap,
                        })

                        if budget_exhausted:
                            break

                    if budget_exhausted:
                        break

                if budget_exhausted:
                    break

            if budget_exhausted:
                break

    finally:
        await client.close()

    api_summary = client.stats.to_dict()
    api_summary.update({
        "budget_exhausted": int(budget_exhausted),
        "max_requests_per_run": max_requests_per_run,
        "max_trip_details_per_search": max_trip_details_per_search,
        "planned_search_calls": planned_search_calls,
    })
    logger.info(
        "Found %s deals from %s logical API calls (%s HTTP requests)",
        len(all_deals),
        logical_api_calls,
        client.stats.total_http_requests,
    )
    total_raw = sum(s["raw_results"] for s in search_stats)
    logger.info(
        f"Searched {len(search_stats)} routes, {total_raw} total raw results"
    )
    logger.info("SEATS_AERO_USAGE %s", json.dumps(api_summary, sort_keys=True))

    # Step 5: Rank deals — prioritize by trip urgency (nearest departure),
    # then by score within each trip
    trip_urgency = _trip_urgency_order(trips)
    all_deals.sort(
        key=lambda d: (trip_urgency.get(d.trip_name, 99), -d.score),
    )
    top_deals = _allocate_deals_per_trip(all_deals, trip_urgency, max_deals)

    # Sort search stats by trip urgency too
    search_stats.sort(
        key=lambda s: trip_urgency.get(s.get("trip_name", ""), 99),
    )

    # Step 6: Build and send email
    email_content = build_digest_email(
        deals=top_deals,
        bonuses=bonuses,
        balances=balances,
        config=config,
        search_stats=search_stats,
    )

    recipients = _resolve_recipients(email_config, settings)
    if not recipients:
        logger.warning("No email recipients configured")
    else:
        sender = EmailSender()
        sent_ids = sender.send_to_all(
            recipients=recipients,
            subject=email_content.subject,
            html_body=email_content.html_body,
            text_body=email_content.text_body,
        )
        logger.info(
            f"Sent digest to {len(sent_ids)}/{len(recipients)} recipients"
        )

    # Step 7: Persist state (skipped on manual triggers)
    all_deal_keys = [_make_deal_key(d.availability) for d in all_deals]
    save_state(
        current_deal_keys=all_deal_keys,
        history=history,
        api_calls_used=client.stats.total_http_requests,
        api_summary=api_summary,
    )

    logger.info("=== Points Deal Finder — Digest complete ===")


def _build_search_legs(
    trip: dict,
    origins: list[str],
    dest_airports: list[str],
    flex: int,
) -> list[dict]:
    """
    Build search legs for a trip. Supports two formats:

    New format (outbound + return):
        outbound: {earliest, latest}
        return: {earliest, latest}

    Legacy format (single date_range, outbound only):
        date_range: {earliest, latest}
    """
    legs = []

    # Check for new outbound/return format
    outbound_cfg = trip.get("outbound")
    return_cfg = trip.get("return")

    if outbound_cfg:
        earliest = _parse_date(outbound_cfg.get("earliest"))
        latest = _parse_date(outbound_cfg.get("latest"))
        if earliest and flex:
            earliest = earliest - timedelta(days=flex)
        if latest and flex:
            latest = latest + timedelta(days=flex)

        legs.append({
            "direction": "outbound",
            "from_airports": origins,
            "to_airports": dest_airports,
            "earliest": earliest,
            "latest": latest,
        })

    if return_cfg:
        earliest = _parse_date(return_cfg.get("earliest"))
        latest = _parse_date(return_cfg.get("latest"))
        if earliest and flex:
            earliest = earliest - timedelta(days=flex)
        if latest and flex:
            latest = latest + timedelta(days=flex)

        # Return = destination → origin (reversed)
        legs.append({
            "direction": "return",
            "from_airports": dest_airports,
            "to_airports": origins,
            "earliest": earliest,
            "latest": latest,
        })

    # Legacy fallback: single date_range = outbound only
    if not legs:
        date_range = trip.get("date_range", {})
        earliest = _parse_date(date_range.get("earliest"))
        latest = _parse_date(date_range.get("latest"))
        if earliest and flex:
            earliest = earliest - timedelta(days=flex)
        if latest and flex:
            latest = latest + timedelta(days=flex)

        legs.append({
            "direction": "outbound",
            "from_airports": origins,
            "to_airports": dest_airports,
            "earliest": earliest,
            "latest": latest,
        })

    return legs


async def _process_result(
    raw: dict,
    client: SeatsAeroClient,
    config: dict,
    routing_config: dict,
    balances: dict,
    transfer_partners: dict,
    bonuses: list,
    travelers: int,
    history: dict[str, str],
    trip_name: str,
    direction: str,
    availability: AwardAvailability | None = None,
    precomputed_paths: list[TransferPath] | None = None,
) -> ScoredDeal | None:
    """Process a single seats.aero result into a ScoredDeal (or None if filtered)."""
    avail = availability or parse_availability(raw)

    _tag = f"[{avail.source}] {avail.origin}→{avail.destination} {avail.departure_date}"

    if avail.points_cost == 0:
        logger.info(f"FILTERED zero_cost: {_tag}")
        return None

    if avail.seats_available and avail.seats_available < travelers:
        logger.info(
            "FILTERED seats: %s — %s seat(s) < %s travelers",
            _tag,
            avail.seats_available,
            travelers,
        )
        return None

    # Get trip detail for routing info
    trip_detail = await client.get_trip(avail.id)
    if trip_detail:
        avail = parse_availability(raw, trip_detail)

    if avail.seats_available and avail.seats_available < travelers:
        logger.info(
            "FILTERED seats: %s — %s seat(s) < %s travelers after trip detail",
            _tag,
            avail.seats_available,
            travelers,
        )
        return None

    # Apply routing filters
    max_conn = routing_config.get("max_connections", 1)
    max_layover = routing_config.get("max_total_layover_hours", 6)
    max_travel = routing_config.get("max_total_travel_hours", 24)

    if avail.num_connections > max_conn:
        logger.info(
            f"FILTERED routing: {_tag} — {avail.num_connections} connections > max {max_conn}"
        )
        return None
    if avail.max_layover_hours > max_layover and avail.max_layover_hours > 0:
        logger.info(
            f"FILTERED layover: {_tag} — {avail.max_layover_hours}h > max {max_layover}h"
        )
        return None
    if avail.total_travel_hours > max_travel and avail.total_travel_hours > 0:
        logger.info(
            f"FILTERED travel_time: {_tag} — {avail.total_travel_hours}h > max {max_travel}h"
        )
        return None

    # Calculate transfer paths
    paths = precomputed_paths or calculate_transfer_paths(
        award_cost=avail.points_cost,
        booking_program=avail.source,
        balances=balances,
        transfer_partners=transfer_partners,
        active_bonuses=bonuses,
        travelers=travelers,
    )

    if not paths:
        logger.info(
            f"FILTERED no_paths: {_tag} — source '{avail.source}' not in any partner program"
        )
        return None

    best_path = paths[0]

    # Airline quality
    airline_name, airline_tier, airline_rating, product_name = (
        get_tier_for_carriers(avail.operating_carriers)
    )

    # Score the deal
    deal = score_deal(
        availability=avail,
        best_path=best_path,
        all_paths=paths,
        airline_name=airline_name,
        airline_tier=airline_tier,
        airline_rating=airline_rating,
        product_name=product_name,
        travelers=travelers,
    )
    deal.trip_name = trip_name
    deal.direction = direction

    # Deal history — tag with first_seen and days tracked
    deal_key = _make_deal_key(avail)
    first_seen = get_first_seen(deal_key, history)
    if first_seen:
        deal.first_seen = first_seen
        deal.days_tracked = days_seen(deal_key, history)
        deal.is_new = False
    else:
        deal.first_seen = date.today()
        deal.days_tracked = 0
        deal.is_new = True

    # Layover analysis for long layovers
    deal.layover_analyses = analyze_all_layovers(avail.layovers)

    return deal


def _parse_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        from datetime import datetime

        return datetime.strptime(str(val), "%Y-%m-%d").date()
    except ValueError:
        return None


def _trip_urgency_order(trips: list[dict]) -> dict[str, int]:
    """
    Assign urgency rank to trips based on earliest outbound departure.

    Nearest trip = rank 0 (shown first). Deals are sorted by
    (trip_urgency, -score) so the most imminent trip's best deals
    appear at the top.
    """
    trip_dates: list[tuple[date | None, str]] = []
    for trip in trips:
        name = trip.get("name", "Unnamed Trip")
        outbound = trip.get("outbound", {})
        earliest = _parse_date(outbound.get("earliest")) if outbound else None
        if not earliest:
            dr = trip.get("date_range", {})
            earliest = _parse_date(dr.get("earliest"))
        trip_dates.append((earliest, name))

    # Sort by date (None → end)
    trip_dates.sort(key=lambda t: t[0] or date.max)
    return {name: i for i, (_, name) in enumerate(trip_dates)}


def _count_planned_search_calls(trips: list[dict], origins: list[str]) -> int:
    """Count cached-search calls implied by the current config."""
    total = 0
    for trip in trips:
        dest_airports = []
        for dest_group in trip.get("destinations", []):
            dest_airports.extend(dest_group.get("preferred_airports", []))
        legs = _build_search_legs(
            trip,
            origins,
            dest_airports,
            trip.get("flexibility_days", 0),
        )
        for leg in legs:
            total += len(leg["from_airports"]) * len(leg["to_airports"])
    return total


def _trip_scan_order(trips: list[dict]) -> list[dict]:
    """
    Process high-priority trips first so focused trips are scanned before broad
    opportunistic searches.
    """
    return sorted(
        trips,
        key=lambda trip: (
            TRIP_PRIORITY_RANK.get(str(trip.get("priority", "medium")).lower(), 99),
            _trip_earliest_date(trip) or date.max,
            trip.get("name", ""),
        ),
    )


def _trip_earliest_date(trip: dict) -> date | None:
    """Best-effort earliest outbound date used as a sort tiebreaker."""
    outbound = trip.get("outbound", {})
    earliest = _parse_date(outbound.get("earliest")) if outbound else None
    if earliest:
        return earliest
    dr = trip.get("date_range", {})
    return _parse_date(dr.get("earliest"))


def _is_opportunistic_trip(trip: dict) -> bool:
    """Heuristic: broad exploratory trip definitions are marked as opportunistic."""
    trip_name = str(trip.get("name", "")).lower()
    return "opportunistic" in trip_name


def _trip_detail_lookup_cap(
    *,
    is_opportunistic: bool,
    default_cap: int,
    request_cap: int,
) -> int:
    """
    Keep opportunistic scans lighter when run budget is high enough to approach
    seats.aero daily limits.
    """
    if not is_opportunistic:
        return default_cap

    if request_cap >= 800:
        return min(default_cap, 2)
    return min(default_cap, 3)


def _build_route_candidates(
    raw_results: list[dict],
    balances: dict,
    transfer_partners: dict,
    bonuses: list,
    travelers: int,
) -> tuple[list[RouteCandidate], dict[str, int]]:
    """Filter and prioritize raw search hits before trip-detail lookups."""
    counts = {
        "zero_cost": 0,
        "insufficient_seats": 0,
        "no_paths": 0,
    }
    candidates: list[RouteCandidate] = []

    for raw in raw_results:
        avail = parse_availability(raw)
        if avail.points_cost == 0:
            counts["zero_cost"] += 1
            continue
        if avail.seats_available and avail.seats_available < travelers:
            counts["insufficient_seats"] += 1
            continue

        paths = calculate_transfer_paths(
            award_cost=avail.points_cost,
            booking_program=avail.source,
            balances=balances,
            transfer_partners=transfer_partners,
            active_bonuses=bonuses,
            travelers=travelers,
        )
        if not paths:
            counts["no_paths"] += 1
            continue

        candidates.append(
            RouteCandidate(
                raw=raw,
                availability=avail,
                transfer_paths=paths,
            )
        )

    candidates.sort(key=lambda candidate: _route_candidate_sort_key(candidate, travelers))
    return candidates, counts


def _route_candidate_sort_key(
    candidate: RouteCandidate,
    travelers: int,
) -> tuple:
    """Prioritize bookable, cheaper results first within each route search."""
    best_path = candidate.transfer_paths[0]
    enough_seats = (
        candidate.availability.seats_available == 0
        or candidate.availability.seats_available >= travelers
    )
    return (
        not enough_seats,
        not best_path.affordable_both,
        best_path.points_needed_per_person,
        candidate.availability.departure_date,
        candidate.availability.source,
    )


def _request_budget_exhausted(
    client: SeatsAeroClient,
    max_requests_per_run: int,
) -> bool:
    """Whether the hard seats.aero HTTP request budget has been reached."""
    return client.stats.total_http_requests >= max_requests_per_run


def _allocate_deals_per_trip(
    all_deals: list[ScoredDeal],
    trip_urgency: dict[str, int],
    max_deals: int,
) -> list[ScoredDeal]:
    """
    Guarantee each trip gets representation in the email.

    Splits max_deals evenly across trips (minimum 3 per trip), then
    re-sorts by urgency + score for display order.
    """
    trip_names = sorted(
        {d.trip_name for d in all_deals},
        key=lambda n: trip_urgency.get(n, 99),
    )
    num_trips = len(trip_names)
    if num_trips <= 1:
        return all_deals[:max_deals]

    slots = max(3, math.ceil(max_deals / num_trips))
    selected: list[ScoredDeal] = []
    for name in trip_names:
        trip_deals = [d for d in all_deals if d.trip_name == name]
        selected.extend(trip_deals[:slots])

    selected.sort(key=lambda d: (trip_urgency.get(d.trip_name, 99), -d.score))
    return selected[:max_deals]


def _resolve_recipients(email_config: dict, settings) -> list[str]:
    """Determine recipients for this run, with safe defaults for testing."""
    configured = list(email_config.get("recipients", []))

    override = _parse_recipient_env(
        settings.email_recipients_override or os.environ.get("EMAIL_RECIPIENTS_OVERRIDE", "")
    )
    if override:
        logger.info(
            "Using EMAIL_RECIPIENTS_OVERRIDE for this run: %s recipient(s)",
            len(override),
        )
        return override

    if is_manual_trigger():
        manual_override = _parse_recipient_env(
            settings.manual_run_recipients or os.environ.get("MANUAL_RUN_RECIPIENTS", "")
        )
        if manual_override:
            logger.info(
                "Manual run detected; using MANUAL_RUN_RECIPIENTS: %s recipient(s)",
                len(manual_override),
            )
            return manual_override

        if configured:
            logger.info(
                "Manual run detected; defaulting to first configured recipient only: %s",
                configured[0],
            )
            return [configured[0]]

    return configured


def _parse_recipient_env(value: str) -> list[str]:
    """Parse a comma-separated env var into recipient addresses."""
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> None:
    asyncio.run(run_digest())


if __name__ == "__main__":
    main()
