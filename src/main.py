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
from src.state import load_previous_deals, save_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


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

    # Step 3: Load previous state for dedup
    previous_deal_ids = load_previous_deals()
    logger.info(f"Previous run had {len(previous_deal_ids)} deals for dedup")

    # Step 4: Query seats.aero
    client = SeatsAeroClient(settings.seats_aero_api_key)
    all_deals: list[ScoredDeal] = []
    api_calls = 0

    try:
        for trip in trips:
            trip_name = trip.get("name", "Unnamed Trip")
            destinations = trip.get("destinations", [])
            date_range = trip.get("date_range", {})
            earliest = _parse_date(date_range.get("earliest"))
            latest = _parse_date(date_range.get("latest"))
            flex = trip.get("flexibility_days", 0)

            if earliest and flex:
                earliest = earliest - timedelta(days=flex)
            if latest and flex:
                latest = latest + timedelta(days=flex)

            # Get all destination airports for this trip
            dest_airports = []
            for dest_group in destinations:
                dest_airports.extend(dest_group.get("preferred_airports", []))

            logger.info(
                f"Trip: {trip_name} — {len(origins)} origins × "
                f"{len(dest_airports)} destinations"
            )

            for origin in origins:
                for dest in dest_airports:
                    # Search seats.aero
                    results = await client.cached_search(
                        origin=origin,
                        destination=dest,
                        cabin=config.get("cabin", "business"),
                        start_date=earliest,
                        end_date=latest,
                    )
                    api_calls += 1

                    for raw in results:
                        # Basic filters
                        avail = parse_availability(raw)

                        if avail.points_cost == 0:
                            continue

                        # Check seat count for travelers
                        if avail.seats_available and avail.seats_available < 1:
                            continue

                        # Get trip detail for routing info
                        trip_detail = await client.get_trip(avail.id)
                        api_calls += 1

                        if trip_detail:
                            avail = parse_availability(raw, trip_detail)

                        # Apply routing filters
                        max_conn = routing_config.get("max_connections", 1)
                        max_layover = routing_config.get(
                            "max_total_layover_hours", 6
                        )
                        max_travel = routing_config.get(
                            "max_total_travel_hours", 24
                        )

                        if avail.num_connections > max_conn:
                            continue
                        if (
                            avail.max_layover_hours > max_layover
                            and avail.max_layover_hours > 0
                        ):
                            continue
                        if (
                            avail.total_travel_hours > max_travel
                            and avail.total_travel_hours > 0
                        ):
                            continue

                        # Dedup
                        deal_key = (
                            f"{avail.origin}-{avail.destination}-"
                            f"{avail.departure_date}-{avail.source}-"
                            f"{avail.points_cost}"
                        )
                        if deal_key in previous_deal_ids:
                            continue

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
                            continue

                        best_path = paths[0]

                        # Check CPP floor
                        min_cpp = config.get("value_floor", {}).get(
                            "min_cpp", 1.5
                        )
                        # (cash price lookup is Phase 3 — skip for now)

                        # Airline quality
                        (
                            airline_name,
                            airline_tier,
                            airline_rating,
                            product_name,
                        ) = get_tier_for_carriers(avail.operating_carriers)

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

                        # Layover analysis for long layovers
                        deal.layover_analyses = analyze_all_layovers(avail.layovers)

                        all_deals.append(deal)

    finally:
        await client.close()

    logger.info(
        f"Found {len(all_deals)} deals from {api_calls} API calls"
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

    # Step 7: Persist state
    deal_ids = [
        f"{d.availability.origin}-{d.availability.destination}-"
        f"{d.availability.departure_date}-{d.availability.source}-"
        f"{d.availability.points_cost}"
        for d in all_deals
    ]
    save_state(deal_ids=deal_ids, api_calls_used=api_calls)

    logger.info("=== Points Deal Finder — Digest complete ===")


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
