"""Composite scoring engine for deal ranking."""

from __future__ import annotations

import logging

from src.models import AwardAvailability, ScoredDeal, TransferPath

logger = logging.getLogger(__name__)


def deal_score(
    effective_cost: int,
    airline_tier: str,
    total_travel_hours: float,
    num_connections: int,
    max_layover_hours: float,
    cash_price: float | None,
    has_transfer_bonus: bool,
    bonus_expiry_days: int | None,
    seats_available: int = 0,
    travelers: int = 2,
) -> float:
    """
    Compute composite deal score (0-100+). Higher = better.

    Components:
    - Cost (0-40): lower effective cost = higher score
    - Airline quality (0-25): preferred/neutral/deprioritized
    - Routing quality (0-20): nonstop > short layover > long layover
    - Transfer bonus urgency (0-10): expiring bonus = act now
    - CPP value (0-5): high cents-per-point = great value
    - Seat availability bonus (0-5): enough seats for all travelers
    """
    score = 0.0

    # Cost component (0-40) — normalized against 100k threshold
    cost_score = max(0, (100_000 - effective_cost) / 100_000) * 40
    score += cost_score

    # Airline quality (0-25)
    tier_scores = {"preferred": 25, "neutral": 15, "deprioritized": 5}
    score += tier_scores.get(airline_tier, 10)

    # Routing quality (0-20)
    if num_connections == 0:
        route_score = 20.0
    elif num_connections == 1 and max_layover_hours <= 3:
        route_score = 15.0
    elif num_connections == 1 and max_layover_hours <= 6:
        route_score = 10.0
    elif num_connections == 1:
        route_score = 5.0  # long layover, 1 stop
    else:
        route_score = 0.0
    score += route_score

    # Transfer bonus urgency (0-10)
    if has_transfer_bonus:
        if bonus_expiry_days is not None and bonus_expiry_days <= 7:
            score += 10  # expiring soon — act now
        elif bonus_expiry_days is not None and bonus_expiry_days <= 14:
            score += 7
        else:
            score += 5

    # CPP value (0-5, can go negative for bad deals)
    if cash_price and effective_cost > 0:
        cpp = (cash_price * 100) / effective_cost
        if cpp >= 3.0:
            score += 5
        elif cpp >= 2.0:
            score += 3
        elif cpp < 1.5:
            score -= 10  # just pay cash

    # Seat availability bonus (0-5)
    if seats_available >= travelers:
        score += 5
    elif seats_available >= 1:
        score += 2

    return round(score, 1)


def score_deal(
    availability: AwardAvailability,
    best_path: TransferPath,
    all_paths: list[TransferPath],
    airline_name: str,
    airline_tier: str,
    airline_rating: float,
    product_name: str,
    travelers: int = 2,
    cash_price: float | None = None,
) -> ScoredDeal:
    """Score a single deal and return a ScoredDeal."""
    bonus_expiry = None
    if best_path.bonus:
        bonus_expiry = best_path.bonus.days_remaining

    score = deal_score(
        effective_cost=best_path.points_needed_per_person,
        airline_tier=airline_tier,
        total_travel_hours=availability.total_travel_hours,
        num_connections=availability.num_connections,
        max_layover_hours=availability.max_layover_hours,
        cash_price=cash_price,
        has_transfer_bonus=best_path.has_active_bonus,
        bonus_expiry_days=bonus_expiry,
        seats_available=availability.seats_available,
        travelers=travelers,
    )

    cpp_value = None
    if cash_price and best_path.points_needed_per_person > 0:
        cpp_value = round(
            (cash_price * 100) / best_path.points_needed_per_person, 1
        )

    return ScoredDeal(
        availability=availability,
        score=score,
        best_path=best_path,
        all_paths=all_paths,
        airline_name=airline_name,
        airline_tier=airline_tier,
        airline_rating=airline_rating,
        product_name=product_name,
        cash_price_usd=cash_price,
        cpp_value=cpp_value,
    )
