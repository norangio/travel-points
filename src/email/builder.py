"""Email content builder — renders Jinja2 templates with deal data."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from src.models import LayoverAnalysis, ScoredDeal, TransferBonus

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)

# Register custom filters
jinja_env.filters["format_number"] = lambda n: f"{n:,}"


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
) -> EmailContent:
    """Build the complete daily digest email."""
    pt = ZoneInfo("America/Los_Angeles")
    now = datetime.now(pt)
    digest_date = now.strftime("%b %d, %Y")

    # Classify bonuses
    bonus_alerts = _classify_bonuses(bonuses)

    # Build template context
    context = {
        "digest_date": digest_date,
        "deals": deals,
        "bonus_alerts": bonus_alerts,
        "all_bonuses": bonuses,
        "balances": balances,
        "travelers": config.get("travelers", 2),
        "total_deals": len(deals),
        "has_bonuses": len(bonuses) > 0,
    }

    # Render HTML
    html_template = jinja_env.get_template("daily_digest.html")
    html_body = html_template.render(**context)

    # Render plain text
    text_body = _build_plain_text(deals, bonuses, balances, config, digest_date)

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


def _build_plain_text(
    deals: list[ScoredDeal],
    bonuses: list[TransferBonus],
    balances: dict[str, int],
    config: dict,
    digest_date: str,
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
                f"  {b.source_program} → {b.target_program}: "
                f"+{b.bonus_percentage:.0%}{expiry}"
            )
        lines.append("")

    # Deals
    if deals:
        lines.append(f"TOP {len(deals)} DEALS")
        lines.append("-" * 30)
        for i, deal in enumerate(deals, 1):
            a = deal.availability
            lines.append(f"\n{i}. [{deal.score}] {deal.airline_name} {deal.product_name}")
            lines.append(f"   {a.origin} → {a.destination} | {a.departure_date.strftime('%b %d, %Y')}")

            if a.num_connections == 0:
                lines.append("   Nonstop")
            else:
                lines.append(f"   {a.num_connections} stop(s), {a.max_layover_hours:.1f}h max layover")

            bp = deal.best_path
            lines.append(
                f"   Cost: {bp.points_needed_per_person:,} {bp.source_display_name} per person"
            )
            if bp.has_active_bonus and bp.bonus:
                lines.append(
                    f"   Bonus: +{bp.bonus.bonus_percentage:.0%} active"
                )
            lines.append(
                f"   Total for {config.get('travelers', 2)}: {bp.points_needed_total:,} points"
            )
            if bp.affordable_both:
                lines.append(f"   ✓ You can afford this ({bp.balance_remaining:,} remaining)")
            elif bp.affordable_one:
                lines.append("   ⚠ Can book 1 traveler, not both")

            if a.seats_available:
                lines.append(f"   Seats: {a.seats_available} available")

            if deal.cpp_value:
                lines.append(f"   Value: {deal.cpp_value:.1f} cpp")

            # Layover analysis
            for la in deal.layover_analyses:
                lines.append(f"\n   LAYOVER: {la.city} ({la.airport}) — {la.duration_hours:.1f}h")
                if la.airport_hotel_usd:
                    lines.append(f"   Hotel near airport: ~${la.airport_hotel_usd}/night (3★+)")
                if la.city_center_hotel_usd:
                    lines.append(f"   Hotel city center: ~${la.city_center_hotel_usd}/night (3★+)")
                if la.transit_options:
                    lines.append("   Transit:")
                    for t in la.transit_options:
                        lines.append(f"     {t.mode}: ~${t.cost_usd:.0f}, {t.time_min}min — {t.notes}")
                if la.notes:
                    lines.append(f"   Tip: {la.notes}")
    else:
        lines.append("No deals found matching your criteria today.")

    # Balances
    lines.extend(["", "YOUR BALANCES", "-" * 30])
    for prog, bal in balances.items():
        lines.append(f"  {prog}: {bal:,}")

    lines.append(f"\n— Points Deal Finder")
    return "\n".join(lines)
