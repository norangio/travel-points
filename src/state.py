"""State persistence — deal history tracking across daily runs.

Design decisions:
- Deals are NOT suppressed on repeat days. Instead, each deal tracks the
  date it was first seen, so the email can show "NEW" vs "Day N" badges.
- Ad-hoc manual runs (TRAVEL_POINTS_MANUAL=1) do NOT write state, so
  testing never pollutes the history.
- State is stored as JSON in state/last_run.json on the VPS filesystem.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_DIR = Path(__file__).parent.parent / "state"
STATE_FILE = STATE_DIR / "last_run.json"

# How many days of history to keep (prune older entries)
HISTORY_RETENTION_DAYS = 30


def is_manual_trigger() -> bool:
    """Check if this run should skip state writes (ad-hoc testing).

    Scheduled systemd runs leave the env var unset and save state normally.
    For ad-hoc manual test runs, set TRAVEL_POINTS_MANUAL=1 to skip the
    state write so history isn't polluted by test runs.
    """
    return os.environ.get("TRAVEL_POINTS_MANUAL", "").lower() in (
        "1",
        "true",
        "yes",
    )


def load_deal_history() -> dict[str, str]:
    """
    Load deal history: mapping of deal_key → first_seen date (ISO string).

    Returns empty dict if no history exists.
    """
    if not STATE_FILE.exists():
        return {}

    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        history = data.get("deal_history", {})

        # Prune entries older than retention period
        today = date.today()
        pruned = {}
        for key, first_seen_str in history.items():
            try:
                first_seen = datetime.strptime(first_seen_str, "%Y-%m-%d").date()
                age = (today - first_seen).days
                if age <= HISTORY_RETENTION_DAYS:
                    pruned[key] = first_seen_str
            except ValueError:
                continue

        logger.info(
            f"Loaded deal history: {len(pruned)} active entries "
            f"(pruned {len(history) - len(pruned)} stale)"
        )
        return pruned
    except Exception as e:
        logger.warning(f"Failed to load state: {e}")
        return {}


def get_first_seen(deal_key: str, history: dict[str, str]) -> date | None:
    """Get the first_seen date for a deal, or None if it's new."""
    first_seen_str = history.get(deal_key)
    if first_seen_str is None:
        return None
    try:
        return datetime.strptime(first_seen_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def days_seen(deal_key: str, history: dict[str, str]) -> int:
    """How many days has this deal been tracked? 0 = brand new."""
    first = get_first_seen(deal_key, history)
    if first is None:
        return 0
    return (date.today() - first).days


def save_state(
    current_deal_keys: list[str],
    history: dict[str, str],
    api_calls_used: int = 0,
    api_summary: dict | None = None,
) -> None:
    """
    Save updated deal history. Merges today's deals into existing history.

    Skips save entirely on ad-hoc manual runs to avoid polluting history
    with test runs.
    """
    if is_manual_trigger():
        logger.info(
            "Manual trigger detected — skipping state save to preserve history"
        )
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Merge: add new deals with today's date, keep existing first_seen dates
    today_str = date.today().isoformat()
    updated_history = dict(history)  # copy existing
    new_count = 0
    for key in current_deal_keys:
        if key not in updated_history:
            updated_history[key] = today_str
            new_count += 1

    state = {
        "run_date": today_str,
        "deal_history": updated_history,
        "api_calls_used": api_calls_used,
        "deals_today": len(current_deal_keys),
        "new_deals_today": new_count,
    }
    if api_summary is not None:
        state["api_summary"] = api_summary

    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        logger.info(
            f"Saved state: {len(current_deal_keys)} deals today, "
            f"{new_count} new, {len(updated_history)} total in history"
        )
    except Exception as e:
        logger.error(f"Failed to save state: {e}")
