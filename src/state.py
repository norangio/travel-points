"""State persistence — dedup across daily runs."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_DIR = Path(__file__).parent.parent / "state"
STATE_FILE = STATE_DIR / "last_run.json"


def load_previous_deals() -> set[str]:
    """Load deal IDs from the previous run for deduplication."""
    if not STATE_FILE.exists():
        return set()

    try:
        with open(STATE_FILE) as f:
            data = json.load(f)

        # Only use if from today or yesterday (stale state = no dedup)
        run_date = data.get("run_date", "")
        if run_date:
            last = datetime.strptime(run_date, "%Y-%m-%d").date()
            age = (date.today() - last).days
            if age > 1:
                logger.info(f"State is {age} days old, ignoring for dedup")
                return set()

        return set(data.get("deal_ids", []))
    except Exception as e:
        logger.warning(f"Failed to load state: {e}")
        return set()


def save_state(deal_ids: list[str], api_calls_used: int = 0) -> None:
    """Save current run state for next day's dedup."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    state = {
        "run_date": date.today().isoformat(),
        "deal_ids": deal_ids,
        "api_calls_used": api_calls_used,
    }

    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        logger.info(f"Saved state: {len(deal_ids)} deals, {api_calls_used} API calls")
    except Exception as e:
        logger.error(f"Failed to save state: {e}")
