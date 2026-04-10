"""Airline product quality lookups."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.config import load_airline_products

logger = logging.getLogger(__name__)


@dataclass
class AirlineInfo:
    name: str
    iata: str
    product_name: str
    rating: float
    tier: str
    notes: str = ""


_cache: dict[str, AirlineInfo] | None = None


def _load_cache() -> dict[str, AirlineInfo]:
    global _cache
    if _cache is not None:
        return _cache

    data = load_airline_products()
    products = data.get("products", {})
    iata_lookup = data.get("iata_lookup", {})

    _cache = {}
    for name, info in products.items():
        iata = info.get("iata", "")
        airline = AirlineInfo(
            name=name,
            iata=iata,
            product_name=info.get("product_name", ""),
            rating=float(info.get("rating", 5.0)),
            tier=info.get("tier", "neutral"),
            notes=info.get("notes", ""),
        )
        # Index by IATA code and by name
        if iata:
            _cache[iata] = airline
        _cache[name.lower()] = airline

    # Also add iata_lookup entries
    for iata_code, airline_name in iata_lookup.items():
        if iata_code not in _cache and airline_name.lower() in _cache:
            _cache[iata_code] = _cache[airline_name.lower()]

    return _cache


def get_airline_info(carrier: str) -> AirlineInfo:
    """Look up airline info by IATA code or name."""
    cache = _load_cache()

    # Try exact IATA match
    if carrier in cache:
        return cache[carrier]

    # Try lowercase name match
    lower = carrier.lower()
    if lower in cache:
        return cache[lower]

    # Default
    return AirlineInfo(
        name=carrier,
        iata=carrier,
        product_name="Business",
        rating=5.0,
        tier="neutral",
    )


def get_tier_for_carriers(carriers: list[str]) -> tuple[str, str, float, str]:
    """
    Get the best airline info for a list of operating carriers.

    Returns (airline_name, tier, rating, product_name) for the
    highest-rated carrier in the itinerary.
    """
    if not carriers:
        return ("Unknown", "neutral", 5.0, "Business")

    best = None
    for carrier in carriers:
        info = get_airline_info(carrier)
        if best is None or info.rating > best.rating:
            best = info

    return (best.name, best.tier, best.rating, best.product_name)
