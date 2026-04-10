"""Layover analysis — hotel costs and transit options for long layovers."""

from __future__ import annotations

import logging

from src.config import load_layover_cities
from src.models import LayoverAnalysis, LayoverInfo, TransitOption

logger = logging.getLogger(__name__)

LONG_LAYOVER_THRESHOLD_HOURS = 4.0

_city_cache: dict | None = None


def _load_city_data() -> dict:
    global _city_cache
    if _city_cache is not None:
        return _city_cache
    data = load_layover_cities()
    _city_cache = data.get("airports", {})
    return _city_cache


def analyze_layover(layover: LayoverInfo) -> LayoverAnalysis | None:
    """
    Analyze a long layover — find hotel costs and transit options.

    Returns None if the layover is not long enough to warrant analysis,
    or if we don't have data for the airport.
    """
    if layover.duration_hours < LONG_LAYOVER_THRESHOLD_HOURS:
        return None

    cities = _load_city_data()
    airport_code = layover.airport.upper()

    city_data = cities.get(airport_code)
    if not city_data:
        logger.info(
            f"No layover data for {airport_code} — "
            f"{layover.duration_hours:.1f}h layover"
        )
        return LayoverAnalysis(
            airport=airport_code,
            city=layover.city or airport_code,
            country="",
            duration_hours=layover.duration_hours,
            notes=f"No hotel/transit data available for {airport_code}.",
        )

    # Parse transit options
    transit_options = []
    for t in city_data.get("transit", []):
        transit_options.append(
            TransitOption(
                mode=t.get("mode", ""),
                cost_usd=float(t.get("cost_usd", 0)),
                time_min=int(t.get("time_min", 0)),
                notes=t.get("notes", ""),
            )
        )

    analysis = LayoverAnalysis(
        airport=airport_code,
        city=city_data.get("city", ""),
        country=city_data.get("country", ""),
        duration_hours=layover.duration_hours,
        airport_hotel_usd=city_data.get("airport_hotel_usd"),
        city_center_hotel_usd=city_data.get("city_center_hotel_usd"),
        transit_options=transit_options,
        notes=city_data.get("notes", ""),
    )

    logger.info(
        f"Layover analysis for {airport_code} ({analysis.city}): "
        f"{layover.duration_hours:.1f}h, "
        f"hotel ${analysis.airport_hotel_usd}-${analysis.city_center_hotel_usd}/night"
    )
    return analysis


def analyze_all_layovers(
    layovers: list[LayoverInfo],
) -> list[LayoverAnalysis]:
    """Analyze all layovers in an itinerary, returning analyses for long ones."""
    analyses = []
    for layover in layovers:
        analysis = analyze_layover(layover)
        if analysis is not None:
            analyses.append(analysis)
    return analyses


def format_layover_summary(analysis: LayoverAnalysis) -> str:
    """Format a human-readable layover summary for plain text email."""
    lines = [
        f"  Layover: {analysis.city} ({analysis.airport}) — "
        f"{analysis.duration_hours:.1f} hours"
    ]

    if analysis.airport_hotel_usd:
        lines.append(
            f"  Hotel near airport: ~${analysis.airport_hotel_usd}/night (3-star+)"
        )
    if analysis.city_center_hotel_usd:
        lines.append(
            f"  Hotel city center: ~${analysis.city_center_hotel_usd}/night (3-star+)"
        )

    if analysis.transit_options:
        lines.append("  Transit from airport:")
        for t in analysis.transit_options:
            line = f"    {t.mode}: ~${t.cost_usd:.0f}, {t.time_min} min"
            if t.notes:
                line += f" — {t.notes}"
            lines.append(line)

    if analysis.notes:
        lines.append(f"  Note: {analysis.notes}")

    return "\n".join(lines)
