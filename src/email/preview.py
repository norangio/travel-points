"""Render a local email preview without calling external services."""

from __future__ import annotations

import argparse
import tempfile
from datetime import date, timedelta
from pathlib import Path

from src.email.builder import build_digest_email
from src.models import (
    AwardAvailability,
    LayoverAnalysis,
    LayoverInfo,
    ScoredDeal,
    TransferBonus,
    TransferPath,
    TransitOption,
)


def render_preview(output_dir: Path | None = None) -> tuple[Path, Path]:
    """Render a representative digest preview to HTML and text files."""
    target_dir = output_dir or (Path(tempfile.gettempdir()) / "travel_points_email_preview")
    target_dir.mkdir(parents=True, exist_ok=True)

    content = build_digest_email(
        deals=_sample_deals(),
        bonuses=_sample_bonuses(),
        balances={
            "chase_ur": 185000,
            "capital_one": 132000,
            "united": 94000,
        },
        config={"travelers": 2},
        search_stats=[
            {
                "route": "SAN → LIS",
                "direction": "Outbound",
                "trip_name": "Portugal / Spain",
                "raw_results": 18,
            },
            {
                "route": "LAX → HND",
                "direction": "Outbound",
                "trip_name": "Japan Winter",
                "raw_results": 11,
            },
        ],
    )

    html_path = target_dir / "daily_digest_preview.html"
    text_path = target_dir / "daily_digest_preview.txt"
    html_path.write_text(content.html_body, encoding="utf-8")
    text_path.write_text(content.text_body, encoding="utf-8")
    return html_path, text_path


def _sample_deals() -> list[ScoredDeal]:
    today = date.today()

    return [
        ScoredDeal(
            availability=AwardAvailability(
                id="preview-1",
                source="avios",
                origin="SAN",
                destination="LIS",
                departure_date=today + timedelta(days=82),
                points_cost=60000,
                seats_available=2,
                total_travel_hours=18.0,
                num_connections=1,
                max_layover_hours=5.5,
                layovers=[
                    LayoverInfo(
                        airport="MAD",
                        city="Madrid",
                        duration_hours=5.5,
                        is_long=True,
                    )
                ],
                operating_carriers=["TP"],
            ),
            score=88,
            best_path=TransferPath(
                source_program="chase_ur",
                source_display_name="Chase Ultimate Rewards",
                target_program="avios",
                points_needed_per_person=50000,
                points_needed_total=100000,
                has_active_bonus=True,
                bonus=TransferBonus(
                    source_program="chase_ur",
                    target_program="avios",
                    bonus_percentage=0.2,
                    effective_rate=1.2,
                    end_date=today + timedelta(days=8),
                ),
                effective_rate=1.2,
                affordable_one=True,
                affordable_both=True,
                balance_remaining=85000,
            ),
            all_paths=[
                TransferPath(
                    source_program="chase_ur",
                    source_display_name="Chase Ultimate Rewards",
                    target_program="avios",
                    points_needed_per_person=50000,
                    points_needed_total=100000,
                    has_active_bonus=True,
                    effective_rate=1.2,
                    affordable_one=True,
                    affordable_both=True,
                    balance_remaining=85000,
                ),
                TransferPath(
                    source_program="capital_one",
                    source_display_name="Capital One Miles",
                    target_program="avios",
                    points_needed_per_person=60000,
                    points_needed_total=120000,
                    affordable_one=True,
                    affordable_both=True,
                    balance_remaining=12000,
                ),
            ],
            airline_name="TAP Air Portugal",
            product_name="A330 Business",
            trip_name="Portugal / Spain",
            direction="outbound",
            layover_analyses=[
                LayoverAnalysis(
                    airport="MAD",
                    city="Madrid",
                    country="Spain",
                    duration_hours=5.5,
                    airport_hotel_usd=120,
                    city_center_hotel_usd=85,
                    transit_options=[
                        TransitOption(mode="Metro", cost_usd=3, time_min=25, notes="direct from T4"),
                        TransitOption(mode="Taxi", cost_usd=45, time_min=35, notes="direct"),
                    ],
                    notes="Metro is fastest — direct line from airport to city center",
                ),
            ],
        ),
        ScoredDeal(
            availability=AwardAvailability(
                id="preview-2",
                source="american",
                origin="LAX",
                destination="HND",
                departure_date=today + timedelta(days=145),
                points_cost=80000,
                seats_available=2,
                total_travel_hours=11.0,
                num_connections=0,
                max_layover_hours=0.0,
                operating_carriers=["JL"],
            ),
            score=84,
            best_path=TransferPath(
                source_program="capital_one",
                source_display_name="Capital One Miles",
                target_program="american",
                points_needed_per_person=80000,
                points_needed_total=160000,
                affordable_one=True,
                affordable_both=False,
                balance_remaining=52000,
            ),
            all_paths=[
                TransferPath(
                    source_program="capital_one",
                    source_display_name="Capital One Miles",
                    target_program="american",
                    points_needed_per_person=80000,
                    points_needed_total=160000,
                    affordable_one=True,
                    affordable_both=False,
                    balance_remaining=52000,
                ),
            ],
            airline_name="Japan Airlines",
            product_name="Sky Suite",
            trip_name="Japan Winter",
            direction="outbound",
            is_new=False,
            days_tracked=2,
            first_seen=today - timedelta(days=2),
        ),
        ScoredDeal(
            availability=AwardAvailability(
                id="preview-3",
                source="flyingblue",
                origin="BCN",
                destination="SFO",
                departure_date=today + timedelta(days=96),
                points_cost=55000,
                seats_available=4,
                total_travel_hours=13.0,
                num_connections=1,
                max_layover_hours=1.5,
                layovers=[
                    LayoverInfo(
                        airport="CDG",
                        city="Paris",
                        duration_hours=1.5,
                    )
                ],
                operating_carriers=["AF", "KL"],
            ),
            score=79,
            best_path=TransferPath(
                source_program="chase_ur",
                source_display_name="Chase Ultimate Rewards",
                target_program="flyingblue",
                points_needed_per_person=55000,
                points_needed_total=110000,
                affordable_one=True,
                affordable_both=True,
                balance_remaining=75000,
            ),
            all_paths=[
                TransferPath(
                    source_program="chase_ur",
                    source_display_name="Chase Ultimate Rewards",
                    target_program="flyingblue",
                    points_needed_per_person=55000,
                    points_needed_total=110000,
                    affordable_one=True,
                    affordable_both=True,
                    balance_remaining=75000,
                ),
            ],
            airline_name="Air France / KLM",
            trip_name="Portugal / Spain",
            direction="return",
        ),
    ]


def _sample_bonuses() -> list[TransferBonus]:
    today = date.today()
    return [
        TransferBonus(
            source_program="chase_ur",
            target_program="avios",
            bonus_percentage=0.2,
            effective_rate=1.2,
            start_date=today - timedelta(days=2),
            end_date=today + timedelta(days=8),
            verified=True,
            notes="Useful for Iberia and BA space.",
        )
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a local travel-points email preview.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the rendered preview files. Defaults to the system temp dir.",
    )
    args = parser.parse_args()

    html_path, text_path = render_preview(args.output_dir)
    print(f"HTML preview: {html_path}")
    print(f"Text preview: {text_path}")


if __name__ == "__main__":
    main()
