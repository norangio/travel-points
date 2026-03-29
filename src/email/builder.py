"""Email content builder — renders Jinja2 templates with deal data."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config import load_transfer_partners
from src.models import LayoverAnalysis, ScoredDeal, TransferBonus

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)

HOTEL_PROGRAMS = frozenset({"hyatt", "marriott", "ihg", "wyndham", "accor", "choice"})

SOURCE_PROGRAM_LABELS = {
    "chase_ur": "Chase Ultimate Rewards",
    "capital_one": "Capital One Miles",
    "united_miles": "United MileagePlus",
}

TARGET_PROGRAM_LABELS = {
    "avios": "Avios (British Airways / Iberia / Aer Lingus)",
    "flying_blue": "Flying Blue (Air France / KLM)",
    "aeroplan": "Aeroplan (Air Canada)",
    "virgin_atlantic": "Virgin Atlantic Flying Club",
    "turkish": "Turkish Miles&Smiles",
    "avianca": "Avianca LifeMiles",
    "cathay": "Cathay Pacific Asia Miles",
    "qatar": "Qatar Airways Privilege Club",
    "emirates": "Emirates Skywards",
    "etihad": "Etihad Guest",
    "finnair": "Finnair Plus",
    "qantas": "Qantas Frequent Flyer",
    "aeromexico": "Aeromexico Rewards",
    "eva_air": "EVA Infinity MileageLands",
    "jal": "Japan Airlines Mileage Bank",
    "tap": "TAP Miles&Go",
    "jetblue": "JetBlue TrueBlue",
    "southwest": "Southwest Rapid Rewards",
    "hyatt": "World of Hyatt",
    "marriott": "Marriott Bonvoy",
    "ihg": "IHG One Rewards",
    "wyndham": "Wyndham Rewards",
    "choice": "Choice Privileges",
    "accor": "Accor Live Limitless",
    "united": "United MileagePlus",
    "singapore": "Singapore KrisFlyer",
}


def _program_label(program: str) -> str:
    """Convert internal program keys into user-facing labels."""
    if program in SOURCE_PROGRAM_LABELS:
        return SOURCE_PROGRAM_LABELS[program]
    if program in TARGET_PROGRAM_LABELS:
        return TARGET_PROGRAM_LABELS[program]
    return program.replace("_", " ").title()


# Register custom filters
jinja_env.filters["format_number"] = lambda n: f"{n:,}"
jinja_env.filters["program_label"] = _program_label


def _build_transfer_partner_table() -> dict:
    """Build matrix data for the transfer partner reference table in the email."""
    partners_data = load_transfer_partners()
    prog_order = ["chase_ur", "capital_one", "united_miles"]
    prog_labels = {
        "chase_ur": "Chase UR",
        "capital_one": "Capital One",
        "united_miles": "United Miles",
    }

    all_partners: dict[str, dict] = {}
    for prog_key in prog_order:
        prog_data = partners_data.get(prog_key, {})
        for partner_key, info in prog_data.get("partners", {}).items():
            if partner_key in HOTEL_PROGRAMS:
                continue
            if partner_key not in all_partners:
                all_partners[partner_key] = {
                    "name": TARGET_PROGRAM_LABELS.get(
                        partner_key, partner_key.replace("_", " ").title()
                    ),
                    "rates": {},
                }
            rate = info.get("rate", 1.0)
            if prog_key == "united_miles" and partner_key == "united":
                rate_str = "Direct"
            elif rate == 1.0:
                rate_str = "1:1"
            elif rate == 0.75:
                rate_str = "4:3 \u2605"
            else:
                rate_str = str(rate)
            all_partners[partner_key]["rates"][prog_key] = rate_str

    def sort_key(item: tuple) -> tuple:
        _, d = item
        return (-len(d["rates"]), d["name"])

    rows = []
    for _, d in sorted(all_partners.items(), key=sort_key):
        rows.append({
            "name": d["name"],
            "cells": [d["rates"].get(p, "") for p in prog_order],
        })

    has_reduced_rate = any(
        "\u2605" in cell for row in rows for cell in row["cells"]
    )

    return {
        "columns": [prog_labels[p] for p in prog_order],
        "rows": rows,
        "has_reduced_rate": has_reduced_rate,
    }


@dataclass
class EmailContent:
    subject: str
    html_body: str
    text_body: str


def build_digest_email(
    deals: list[ScoredDeal],
    bonuses: list[TransferBonus],
    balances: dict[str, int],
    config: dict,
    search_stats: list[dict] | None = None,
) -> EmailContent:
    """Build the complete daily digest email."""
    pt = ZoneInfo("America/Los_Angeles")
    now = datetime.now(pt)
    digest_date = now.strftime("%b %d, %Y")

    # Classify bonuses
    bonus_alerts = _classify_bonuses(bonuses)

    stats = search_stats or []
    total_routes = len(stats)
    total_raw_results = sum(s.get("raw_results", 0) for s in stats)

    # Build template context
    context = {
        "digest_date": digest_date,
        "deals": deals,
        "deal_summary_rows": _build_deal_summary_rows(deals),
        "bonus_alerts": bonus_alerts,
        "all_bonuses": bonuses,
        "balances": balances,
        "travelers": config.get("travelers", 2),
        "total_deals": len(deals),
        "has_bonuses": len(bonuses) > 0,
        "search_stats": stats,
        "total_routes_searched": total_routes,
        "total_raw_results": total_raw_results,
        "trip_date_blurbs": _build_trip_date_blurbs(config),
        "trip_notes": _build_trip_notes(config),
        "transfer_partner_table": _build_transfer_partner_table(),
    }

    # Render HTML
    html_template = jinja_env.get_template("daily_digest.html")
    html_body = html_template.render(**context)

    # Render plain text
    text_body = _build_plain_text(deals, bonuses, balances, config, digest_date, stats)

    subject = f"Points Deal Finder — {digest_date}"

    return EmailContent(
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def _classify_bonuses(
    bonuses: list[TransferBonus],
) -> dict[str, list[TransferBonus]]:
    from datetime import date

    result: dict[str, list[TransferBonus]] = {
        "new": [],
        "active": [],
        "expiring_soon": [],
    }
    for b in bonuses:
        if b.is_expiring_soon:
            result["expiring_soon"].append(b)
        elif b.start_date and (date.today() - b.start_date).days <= 3:
            result["new"].append(b)
        else:
            result["active"].append(b)
    return result


def _build_deal_summary_rows(deals: list[ScoredDeal]) -> list[dict]:
    """Build enriched summary rows for the unified deal table."""

    def _fmt_hours(h: float) -> str:
        return f"{h:.0f}h" if h == int(h) else f"{h:.1f}h"

    rows: list[dict] = []
    for deal in deals:
        a = deal.availability
        bp = deal.best_path

        # Airline display
        airline = deal.airline_name or ", ".join(a.operating_carriers)
        if deal.product_name:
            airline = f"{airline} ({deal.product_name})" if airline else deal.product_name
        if not airline:
            airline = a.source

        aircraft = ", ".join(a.aircraft_types) if a.aircraft_types else ""

        points_display = (
            f"{bp.points_needed_per_person:,} "
            f"{bp.source_display_name}/pp"
        )

        # Stops + travel time
        if a.num_connections == 0:
            stops = "Nonstop"
        else:
            stops = f"{a.num_connections} stop{'s' if a.num_connections > 1 else ''}"
            if a.max_layover_hours:
                stops += f" · {_fmt_hours(a.max_layover_hours)} layover"
        if a.total_travel_hours:
            stops += f" · {_fmt_hours(a.total_travel_hours)}"

        # Bonus text
        bonus_text = ""
        if bp.has_active_bonus and bp.bonus:
            pct = int(bp.bonus.bonus_percentage * 100)
            bonus_text = f"+{pct}%"
            if bp.bonus.days_remaining is not None:
                bonus_text += f" ({bp.bonus.days_remaining}d)"

        # Alternative paths
        alt_paths: list[str] = []
        for path in deal.all_paths[1:3]:
            alt = f"{path.points_needed_per_person:,} {path.source_display_name}"
            if path.has_active_bonus:
                alt += " (bonus)"
            if path.affordable_both:
                alt += " ✓"
            alt_paths.append(alt)

        # Layover summaries
        layovers: list[dict] = []
        for la in deal.layover_analyses:
            hotel_parts: list[str] = []
            if la.airport_hotel_usd:
                hotel_parts.append(f"~${la.airport_hotel_usd}/night near airport")
            if la.city_center_hotel_usd:
                hotel_parts.append(f"~${la.city_center_hotel_usd}/night city center")

            transit_parts: list[str] = []
            for t in la.transit_options:
                transit_parts.append(f"{t.mode} ~${t.cost_usd:.0f}/{t.time_min}min")

            layovers.append({
                "header": f"{la.city} ({la.airport}) — {_fmt_hours(la.duration_hours)}",
                "hotels": hotel_parts,
                "transit": transit_parts,
                "notes": la.notes or "",
            })

        rows.append({
            "route": f"{a.origin} → {a.destination}",
            "date": a.departure_date.strftime("%b %d"),
            "airline": airline,
            "aircraft": aircraft,
            "points": points_display,
            "trip_name": deal.trip_name,
            "direction": deal.direction,
            "direction_label": "OUT" if deal.direction == "outbound" else "RET" if deal.direction == "return" else "",
            "stops": stops,
            "seats": a.seats_available,
            "affordable_both": bp.affordable_both,
            "affordable_one": bp.affordable_one,
            "freshness_label": deal.freshness_label,
            "is_new": deal.is_new,
            "bonus_text": bonus_text,
            "alt_paths": alt_paths,
            "layovers": layovers,
        })

    return rows


def _build_trip_date_blurbs(config: dict) -> dict[str, str]:
    """Build formatted date range blurbs per trip name, flex-adjusted."""
    from datetime import date, timedelta

    def _parse(val) -> date | None:
        if val is None:
            return None
        if isinstance(val, date):
            return val
        try:
            from datetime import datetime
            return datetime.strptime(str(val), "%Y-%m-%d").date()
        except ValueError:
            return None

    def _fmt(d: date | None) -> str:
        return d.strftime("%b %d") if d else "?"

    blurbs: dict[str, str] = {}
    for trip in config.get("trips", []):
        name = trip.get("name", "Unnamed Trip")
        flex = trip.get("flexibility_days", 0)
        parts = []

        outbound = trip.get("outbound")
        if outbound:
            e = _parse(outbound.get("earliest"))
            l = _parse(outbound.get("latest"))
            if e and flex:
                e = e - timedelta(days=flex)
            if l and flex:
                l = l + timedelta(days=flex)
            parts.append(f"Outbound: {_fmt(e)} – {_fmt(l)}")

        return_cfg = trip.get("return")
        if return_cfg:
            e = _parse(return_cfg.get("earliest"))
            l = _parse(return_cfg.get("latest"))
            if e and flex:
                e = e - timedelta(days=flex)
            if l and flex:
                l = l + timedelta(days=flex)
            parts.append(f"Return: {_fmt(e)} – {_fmt(l)}")

        if not parts:
            dr = trip.get("date_range", {})
            e = _parse(dr.get("earliest"))
            l = _parse(dr.get("latest"))
            if e and flex:
                e = e - timedelta(days=flex)
            if l and flex:
                l = l + timedelta(days=flex)
            parts.append(f"{_fmt(e)} – {_fmt(l)}")

        blurbs[name] = " · ".join(parts)
    return blurbs


def _build_trip_notes(config: dict) -> dict[str, str]:
    """Build optional notes to render under each trip section header."""
    notes: dict[str, str] = {}
    for trip in config.get("trips", []):
        name = trip.get("name", "Unnamed Trip")
        note = str(trip.get("email_note", "")).strip()
        if note:
            notes[name] = note
    return notes


def _build_plain_text(
    deals: list[ScoredDeal],
    bonuses: list[TransferBonus],
    balances: dict[str, int],
    config: dict,
    digest_date: str,
    search_stats: list[dict] | None = None,
) -> str:
    """Build plain text version of the email."""
    lines = [
        f"Points Deal Finder — {digest_date}",
        "=" * 50,
        "",
    ]

    # Bonuses
    if bonuses:
        lines.append("ACTIVE TRANSFER BONUSES")
        lines.append("-" * 30)
        for b in bonuses:
            expiry = ""
            if b.end_date:
                expiry = f" (ends {b.end_date.strftime('%b %d')})"
            lines.append(
                f"  {_program_label(b.source_program)} → {_program_label(b.target_program)}: "
                f"+{b.bonus_percentage:.0%}{expiry}"
            )
        lines.append("")

    # Deals
    if deals:
        lines.append(f"TOP {len(deals)} DEALS")
        lines.append("-" * 50)

        current_trip = ""
        for deal in deals:
            a = deal.availability
            bp = deal.best_path

            if deal.trip_name != current_trip:
                current_trip = deal.trip_name
                lines.append(f"\n  {deal.trip_name.upper()}")

            airline = deal.airline_name or a.source
            if deal.product_name:
                airline += f" ({deal.product_name})"
            lines.append(
                f"  {a.origin} → {a.destination}  |  {a.departure_date.strftime('%b %d')}  |  "
                f"{airline}  |  {bp.points_needed_per_person:,} {bp.source_display_name}/pp"
            )

            # Detail line
            dir_label = "OUT" if deal.direction == "outbound" else "RET" if deal.direction == "return" else ""
            parts: list[str] = []
            if dir_label:
                parts.append(dir_label)
            if a.num_connections == 0:
                parts.append("Nonstop")
            else:
                parts.append(f"{a.num_connections} stop")
                if a.max_layover_hours:
                    parts.append(f"{a.max_layover_hours:.1f}h layover")
            if a.total_travel_hours:
                parts.append(f"{a.total_travel_hours:.1f}h total")
            if a.seats_available:
                parts.append(f"{a.seats_available} seats")
            if bp.affordable_both:
                parts.append("✓ bookable")
            elif bp.affordable_one:
                parts.append("⚠ 1 trav only")
            else:
                parts.append("✗ need more pts")
            parts.append(deal.freshness_label)
            if bp.has_active_bonus and bp.bonus:
                parts.append(f"+{int(bp.bonus.bonus_percentage * 100)}%")
            lines.append(f"    {' · '.join(parts)}")

            if len(deal.all_paths) > 1:
                alts = []
                for path in deal.all_paths[1:3]:
                    alt = f"{path.points_needed_per_person:,} {path.source_display_name}"
                    if path.affordable_both:
                        alt += " ✓"
                    alts.append(alt)
                lines.append(f"    Also: {' · '.join(alts)}")

            for la in deal.layover_analyses:
                lines.append(f"    Layover: {la.city} ({la.airport}) — {la.duration_hours:.1f}h")
                if la.airport_hotel_usd:
                    lines.append(f"      Hotels: ~${la.airport_hotel_usd} near airport")
                if la.city_center_hotel_usd:
                    lines.append(f"      Hotels: ~${la.city_center_hotel_usd} city center")
                if la.transit_options:
                    transit = [f"{t.mode} ~${t.cost_usd:.0f}/{t.time_min}min" for t in la.transit_options]
                    lines.append(f"      Transit: {' · '.join(transit)}")
                if la.notes:
                    lines.append(f"      Tip: {la.notes}")
    else:
        lines.append("No deals found matching your criteria today.")
        if search_stats:
            total_routes = len(search_stats)
            total_raw = sum(s.get("raw_results", 0) for s in search_stats)
            lines.append("")
            lines.append(
                f"WHAT WE SEARCHED — {total_routes} routes, {total_raw} raw results"
            )
            lines.append("-" * 30)
            for s in search_stats:
                lines.append(
                    f"  {s['route']} ({s['direction']}) "
                    f"· {s['trip_name']}: {s['raw_results']} results"
                )

    # Balances
    lines.extend(["", "YOUR BALANCES", "-" * 30])
    for prog, bal in balances.items():
        lines.append(f"  {prog}: {bal:,}")

    # Transfer partner reference
    table = _build_transfer_partner_table()
    lines.extend(["", "TRANSFER PARTNER REFERENCE", "-" * 50])
    col_header = "  {:<40} {}".format(
        "Loyalty Program", "  ".join(f"{c:<14}" for c in table["columns"])
    )
    lines.append(col_header)
    for row in table["rows"]:
        cells = "  ".join(f"{(c or '—'):<14}" for c in row["cells"])
        lines.append(f"  {row['name']:<40} {cells}")
    if table["has_reduced_rate"]:
        lines.append("  ★ 4:3 rate = 1,000 points → 750 miles")

    lines.append(f"\n— Points Deal Finder")
    return "\n".join(lines)
