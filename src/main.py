"""Main orchestrator — daily pipeline: config → fetch → score → email."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, timedelta

from src.config import (
    get_settings,
    load_transfer_partners,
    load_yaml_config,
)
from src.email.builder import build_digest_email
from src.email.sender import EmailSender
from src.layover.analyzer import analyze_all_layovers
from src.models import ScoredDeal
from src.scoring.airline_quality import get_tier_for_carriers
from src.scoring.engine import score_deal
from src.scoring.transfer_paths import calculate_transfer_paths
from src.sources.seats_aero import SeatsAeroClient, parse_availability
from src.sources.transfer_bonuses import load_bonuses_from_config
from src.state import days_seen, get_first_seen, load_deal_history, save_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


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
    trips = config.get("trips", [])
    routing_config = config.get("routing", {})
    email_config = config.get("email", {})
    max_deals = email_config.get("max_deals_per_email", 15)

    if not settings.seats_aero_api_key:
        logger.error("SEATS_AERO_API_KEY not set — cannot fetch availability")
        return

    # Step 2: Load transfer bonuses (Phase 1: from config YAML)
    bonuses = load_bonuses_from_config(config)
    logger.info(f"Loaded {len(bonuses)} active transfer bonuses")

    # Step 3: Load deal history (for first_seen tracking, NOT suppression)
    history = load_deal_history()

    # Step 4: Query seats.aero for each trip (outbound + return)
    client = SeatsAeroClient(settings.seats_aero_api_key)
    all_deals: list[ScoredDeal] = []
    api_calls = 0

    search_stats: list[dict] = []  # per-route search summary

    try:
        for trip in trips:
            trip_name = trip.get("name", "Unnamed Trip")
            destinations = trip.get("destinations", [])
            flex = trip.get("flexibility_days", 0)

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
                        results = await client.cached_search(
                            origin=from_apt,
                            destination=to_apt,
                            cabin=config.get("cabin", "business"),
                            start_date=leg["earliest"],
                            end_date=leg["latest"],
                        )
                        api_calls += 1

                        route_deals = 0
                        for raw in results:
                            deal = await _process_result(
                                raw=raw,
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
                            )
                            if deal:
                                all_deals.append(deal)
                                route_deals += 1
                                api_calls += 1  # get_trip call

                        search_stats.append({
                            "route": f"{from_apt} → {to_apt}",
                            "direction": leg["direction"].capitalize(),
                            "trip_name": trip_name,
                            "raw_results": len(results),
                            "qualifying_deals": route_deals,
                        })

    finally:
        await client.close()

    logger.info(
        f"Found {len(all_deals)} deals from {api_calls} API calls"
    )
    total_raw = sum(s["raw_results"] for s in search_stats)
    logger.info(
        f"Searched {len(search_stats)} routes, {total_raw} total raw results"
    )

    # Step 5: Rank and take top N
    all_deals.sort(key=lambda d: d.score, reverse=True)
    top_deals = all_deals[:max_deals]

    # Step 6: Build and send email
    email_content = build_digest_email(
        deals=top_deals,
        bonuses=bonuses,
        balances=balances,
        config=config,
        search_stats=search_stats,
    )

    recipients = email_config.get("recipients", [])
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
        api_calls_used=api_calls,
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
) -> ScoredDeal | None:
    """Process a single seats.aero result into a ScoredDeal (or None if filtered)."""
    avail = parse_availability(raw)

    _tag = f"[{avail.source}] {avail.origin}→{avail.destination} {avail.departure_date}"

    if avail.points_cost == 0:
        logger.info(f"FILTERED zero_cost: {_tag}")
        return None

    # Get trip detail for routing info
    trip_detail = await client.get_trip(avail.id)
    if trip_detail:
        avail = parse_availability(raw, trip_detail)

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
    paths = calculate_transfer_paths(
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


def main() -> None:
    asyncio.run(run_digest())


if __name__ == "__main__":
    main()
