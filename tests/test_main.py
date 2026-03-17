import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.main import (
    _build_route_candidates,
    _count_planned_search_calls,
    _parse_recipient_env,
    _resolve_recipients,
)


class MainHelpersTest(unittest.TestCase):
    def test_count_planned_search_calls_counts_outbound_and_return(self) -> None:
        trips = [
            {
                "destinations": [
                    {"preferred_airports": ["LHR", "CDG"]},
                ],
                "outbound": {"earliest": "2026-06-01", "latest": "2026-06-05"},
                "return": {"earliest": "2026-06-10", "latest": "2026-06-14"},
            }
        ]

        total = _count_planned_search_calls(trips=trips, origins=["SAN", "LAX"])

        self.assertEqual(total, 8)

    def test_build_route_candidates_filters_low_seat_and_untransferable_hits(self) -> None:
        raw_results = [
            {
                "ID": "one-seat",
                "Source": "avios",
                "Date": "2026-06-05",
                "JMileageCost": 60000,
                "JRemainingSeats": 1,
                "Route": {"OriginAirport": "LAX", "DestinationAirport": "LHR"},
            },
            {
                "ID": "untransferable",
                "Source": "lifemiles",
                "Date": "2026-06-05",
                "JMileageCost": 55000,
                "JRemainingSeats": 2,
                "Route": {"OriginAirport": "LAX", "DestinationAirport": "MAD"},
            },
            {
                "ID": "candidate",
                "Source": "avios",
                "Date": "2026-06-06",
                "JMileageCost": 50000,
                "JRemainingSeats": 2,
                "Route": {"OriginAirport": "LAX", "DestinationAirport": "LIS"},
            },
        ]
        transfer_partners = {
            "chase_ur": {
                "display_name": "Chase Ultimate Rewards",
                "partners": {
                    "avios": {"seats_aero_source": "avios", "rate": 1.0},
                },
            }
        }

        candidates, counts = _build_route_candidates(
            raw_results=raw_results,
            balances={"chase_ur": 120000},
            transfer_partners=transfer_partners,
            bonuses=[],
            travelers=2,
        )

        self.assertEqual(counts["insufficient_seats"], 1)
        self.assertEqual(counts["no_paths"], 1)
        self.assertEqual(counts["zero_cost"], 0)
        self.assertEqual([c.availability.id for c in candidates], ["candidate"])
        self.assertEqual(candidates[0].transfer_paths[0].points_needed_total, 100000)

    def test_manual_run_defaults_to_first_configured_recipient(self) -> None:
        settings = SimpleNamespace(
            email_recipients_override="",
            manual_run_recipients="",
        )

        with patch("src.main.is_manual_trigger", return_value=True):
            recipients = _resolve_recipients(
                {"recipients": ["norangio@gmail.com", "krista.hakola@gmail.com"]},
                settings,
            )

        self.assertEqual(recipients, ["norangio@gmail.com"])

    def test_override_recipients_take_precedence(self) -> None:
        settings = SimpleNamespace(
            email_recipients_override="one@example.com,two@example.com",
            manual_run_recipients="manual@example.com",
        )

        with patch("src.main.is_manual_trigger", return_value=True):
            recipients = _resolve_recipients(
                {"recipients": ["norangio@gmail.com", "krista.hakola@gmail.com"]},
                settings,
            )

        self.assertEqual(recipients, ["one@example.com", "two@example.com"])

    def test_parse_recipient_env_trims_and_skips_empty_values(self) -> None:
        self.assertEqual(
            _parse_recipient_env(" a@example.com, ,b@example.com "),
            ["a@example.com", "b@example.com"],
        )
