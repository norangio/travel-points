import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.email.builder import build_digest_email
from src.email.preview import render_preview
from src.models import AwardAvailability, ScoredDeal, TransferPath


class EmailBuilderTest(unittest.TestCase):
    def test_html_includes_quick_look_summary_table(self) -> None:
        deal = ScoredDeal(
            availability=AwardAvailability(
                id="deal-1",
                source="avios",
                origin="SAN",
                destination="LIS",
                departure_date=date(2026, 6, 5),
                points_cost=60000,
                seats_available=2,
                operating_carriers=["TP"],
            ),
            score=87,
            best_path=TransferPath(
                source_program="chase_ur",
                source_display_name="Chase Ultimate Rewards",
                target_program="avios",
                points_needed_per_person=50000,
                points_needed_total=100000,
                affordable_one=True,
                affordable_both=True,
                balance_remaining=90000,
            ),
            all_paths=[],
            airline_name="TAP Air Portugal",
            product_name="A330 Business",
        )

        content = build_digest_email(
            deals=[deal],
            bonuses=[],
            balances={"chase_ur": 190000},
            config={"travelers": 2},
        )

        self.assertIn("Quick Look", content.html_body)
        self.assertIn("Route", content.html_body)
        self.assertIn("Airline", content.html_body)
        self.assertIn("Points", content.html_body)
        self.assertIn("SAN → LIS", content.html_body)
        self.assertIn("Jun 05, 2026", content.html_body)
        self.assertIn("TAP Air Portugal (A330 Business)", content.html_body)
        self.assertIn("50,000 Chase Ultimate Rewards/person", content.html_body)

    def test_preview_renderer_writes_preview_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path, text_path = render_preview(Path(temp_dir))

            self.assertTrue(html_path.exists())
            self.assertTrue(text_path.exists())
            self.assertIn("Quick Look", html_path.read_text(encoding="utf-8"))
            self.assertIn("Points Deal Finder", text_path.read_text(encoding="utf-8"))
