"""Configuration loader — YAML config + environment secrets."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = Path(__file__).parent / "data"


class Settings(BaseSettings):
    """Environment-based secrets (not in YAML config)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    seats_aero_api_key: str = Field(default="")
    resend_api_key: str = Field(default="")
    email_from_address: str = Field(default="onboarding@resend.dev")
    email_from_name: str = Field(default="Points Deal Finder")
    email_recipients_override: str = Field(default="")
    manual_run_recipients: str = Field(default="")
    seats_aero_request_delay_seconds: float = Field(default=1.0)
    seats_aero_max_retries: int = Field(default=4)
    seats_aero_max_requests_per_run: int = Field(default=800)
    seats_aero_max_trip_details_per_search: int = Field(default=6)
    transfer_bonus_scrapers_enabled: bool = Field(default=True)
    transfer_bonus_scraper_timeout_seconds: float = Field(default=15.0)
    github_token: str = Field(default="")
    gist_id: str = Field(default="")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml_config(path: Path | None = None) -> dict:
    """Load the user's config.yaml."""
    if path is None:
        path = PROJECT_ROOT / "config.yaml"
    if not path.exists():
        logger.warning(f"Config file not found at {path}, using defaults")
        return _default_config()
    with open(path) as f:
        return yaml.safe_load(f)


def load_transfer_partners() -> dict:
    """Load the static transfer partner map."""
    path = DATA_DIR / "transfer_partners.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def load_airline_products() -> dict:
    """Load the static airline product quality data."""
    path = DATA_DIR / "airline_products.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def load_layover_cities() -> dict:
    """Load the static layover city data."""
    path = DATA_DIR / "layover_cities.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def _default_config() -> dict:
    return {
        "balances": {
            "chase_ur": 0,
            "capital_one": 0,
            "united_miles": 0,
        },
        "origins": ["LAX"],
        "cabin": "business",
        "travelers": 2,
        "trips": [],
        "airline_tiers": {
            "preferred": [],
            "neutral": [],
            "deprioritized": [],
        },
        "routing": {
            "max_connections": 1,
            "max_total_layover_hours": 6,
            "max_total_travel_hours": 24,
        },
        "value_floor": {
            "min_cpp": 1.5,
        },
        "email": {
            "recipients": [],
            "max_deals_per_email": 15,
            "include_transfer_bonus_summary": True,
        },
    }
