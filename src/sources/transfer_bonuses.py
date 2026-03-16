"""Transfer bonus loader.

Phase 1: Loads manually-maintained bonuses from config.yaml.
Phase 2: Will add scrapers for FrequentMiler, TPG, AwardWallet.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from src.models import TransferBonus

logger = logging.getLogger(__name__)


def load_bonuses_from_config(config: dict) -> list[TransferBonus]:
    """Load transfer bonuses from the YAML config (manual entry for Phase 1)."""
    bonuses_raw = config.get("transfer_bonuses", [])
    bonuses: list[TransferBonus] = []

    for b in bonuses_raw:
        try:
            start = _parse_date(b.get("start_date"))
            end = _parse_date(b.get("end_date"))
            pct = float(b.get("bonus_percentage", 0))

            bonus = TransferBonus(
                source_program=b["source_program"],
                target_program=b["target_program"],
                bonus_percentage=pct,
                effective_rate=1.0 + pct,
                start_date=start,
                end_date=end,
                source_url=b.get("source_url", ""),
                verified=b.get("verified", True),  # manual = trusted
                notes=b.get("notes", ""),
            )

            # Only include active bonuses
            today = date.today()
            if start and start > today:
                continue
            if end and end < today:
                continue

            bonuses.append(bonus)
            logger.info(
                f"Active bonus: {bonus.source_program} → {bonus.target_program} "
                f"+{bonus.bonus_percentage:.0%} (ends {bonus.end_date})"
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Skipping invalid bonus config: {e}")

    return bonuses


def find_active_bonus(
    source_program: str,
    target_program: str,
    bonuses: list[TransferBonus],
) -> TransferBonus | None:
    """Find the best active bonus for a specific transfer path."""
    matching = [
        b
        for b in bonuses
        if b.source_program == source_program and b.target_program == target_program
    ]
    if not matching:
        return None
    # Return the one with highest bonus percentage
    return max(matching, key=lambda b: b.bonus_percentage)


def classify_bonuses(
    bonuses: list[TransferBonus],
) -> dict[str, list[TransferBonus]]:
    """Classify bonuses by status for the email alert bar."""
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


def _parse_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        return datetime.strptime(str(val), "%Y-%m-%d").date()
    except ValueError:
        return None
