"""Transfer path calculator — finds the cheapest way to pay for an award."""

from __future__ import annotations

import math
import logging

from src.models import TransferBonus, TransferPath
from src.sources.transfer_bonuses import find_active_bonus

logger = logging.getLogger(__name__)


def calculate_transfer_paths(
    award_cost: int,
    booking_program: str,
    balances: dict[str, int],
    transfer_partners: dict,
    active_bonuses: list[TransferBonus],
    travelers: int = 2,
) -> list[TransferPath]:
    """
    Calculate all possible ways to pay for an award from your balances.

    Returns paths sorted by points_needed_per_person (cheapest first).
    """
    paths: list[TransferPath] = []

    for program_key, program_data in transfer_partners.items():
        balance = balances.get(program_key, 0)
        if balance == 0:
            continue

        partners = program_data.get("partners", {})
        display_name = program_data.get("display_name", program_key)

        # Check if this program can transfer to the booking program
        # Match by seats_aero_source or by partner key
        for partner_key, partner_info in partners.items():
            seats_source = partner_info.get("seats_aero_source", "")
            is_direct = partner_info.get("direct", False)

            # Match: partner key matches booking program, OR
            # seats_aero_source matches booking program
            if partner_key != booking_program and seats_source != booking_program:
                continue

            base_rate = float(partner_info.get("rate", 1.0))

            # Check for active transfer bonus
            bonus = find_active_bonus(program_key, partner_key, active_bonuses)
            effective_rate = base_rate
            if bonus:
                effective_rate = base_rate * (1.0 + bonus.bonus_percentage)

            # Points needed from this source
            points_needed = math.ceil(award_cost / effective_rate)
            points_needed_total = points_needed * travelers

            path = TransferPath(
                source_program=program_key,
                source_display_name=display_name,
                target_program=partner_key,
                points_needed_per_person=points_needed,
                points_needed_total=points_needed_total,
                has_active_bonus=bonus is not None,
                bonus=bonus,
                effective_rate=effective_rate,
                affordable_one=points_needed <= balance,
                affordable_both=points_needed_total <= balance,
                balance_remaining=max(0, balance - points_needed_total),
            )
            paths.append(path)

    # Sort by cheapest per-person cost
    paths.sort(key=lambda p: p.points_needed_per_person)
    return paths
