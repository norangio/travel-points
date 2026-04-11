import unittest
from datetime import date, timedelta

from src.models import TransferBonus
from src.sources.transfer_bonuses import (
    _merge_scraped_candidates,
    _parse_awardwallet_html,
    _parse_frequent_miler_html,
    _parse_the_points_guy_html,
    load_transfer_bonuses,
    merge_transfer_bonus_lists,
    ScrapedBonusCandidate,
)


TRANSFER_PARTNERS = {
    "chase_ur": {
        "partners": {
            "avios": {},
            "wyndham": {},
            "flying_blue": {},
            "virgin_atlantic": {},
        }
    },
    "capital_one": {
        "partners": {
            "flying_blue": {},
            "virgin_atlantic": {},
        }
    },
}

FUTURE_END_DATE = date.today() + timedelta(days=30)
FUTURE_END_DATE_STR = FUTURE_END_DATE.isoformat()


class TransferBonusScraperTest(unittest.TestCase):
    def test_parse_frequent_miler_table(self) -> None:
        html = """
        <table>
          <tr>
            <th>Bank</th><th>Program</th><th>Bonus</th><th>Details</th><th>Start Date</th><th>Expiry Date</th>
          </tr>
          <tr>
            <td>Chase Ultimate Rewards</td>
            <td>British Airways Club, Iberia Plus</td>
            <td>20%</td>
            <td>Applies across Avios programs</td>
            <td>03/01/2026</td>
            <td>03/31/2026</td>
          </tr>
          <tr>
            <td>Capital One Miles</td>
            <td>Flying Blue</td>
            <td>25%</td>
            <td></td>
            <td>03/10/2026</td>
            <td>04/10/2026</td>
          </tr>
        </table>
        """

        candidates = _parse_frequent_miler_html(
            html,
            page_url="https://frequentmiler.com/current-point-transfer-bonuses/",
            source_name="Frequent Miler",
            transfer_partners=TRANSFER_PARTNERS,
        )

        by_key = {
            (candidate.source_program, candidate.target_program): candidate
            for candidate in candidates
        }
        self.assertEqual(len(candidates), 2)
        self.assertIn(("chase_ur", "avios"), by_key)
        self.assertIn(("capital_one", "flying_blue"), by_key)
        self.assertEqual(by_key[("chase_ur", "avios")].bonus_percentage, 0.20)
        self.assertEqual(by_key[("chase_ur", "avios")].end_date, date(2026, 3, 31))

    def test_parse_tpg_narrative_sections(self) -> None:
        html = """
        <article>
          <h2>Chase Ultimate Rewards</h2>
          <p>
            Chase is currently offering a 20% transfer bonus to Aer Lingus AerClub,
            British Airways Club and Iberia Plus through March 31, 2026, as well as
            a separate 30% bonus to Wyndham Rewards through April 15, 2026.
          </p>
          <h2>Capital One Miles</h2>
          <ul>
            <li>Capital One is offering a 15% transfer bonus to Virgin Red through April 30, 2026.</li>
          </ul>
        </article>
        """

        candidates = _parse_the_points_guy_html(
            html,
            page_url="https://thepointsguy.com/loyalty-programs/current-transfer-bonuses/",
            source_name="The Points Guy",
            transfer_partners=TRANSFER_PARTNERS,
        )

        pairs = {(candidate.source_program, candidate.target_program) for candidate in candidates}
        self.assertIn(("chase_ur", "avios"), pairs)
        self.assertIn(("chase_ur", "wyndham"), pairs)
        self.assertIn(("capital_one", "virgin_atlantic"), pairs)

    def test_parse_awardwallet_table(self) -> None:
        html = """
        <table>
          <tr>
            <th>Rewards Program</th><th>Transfer Partner</th><th>Transfer Bonus</th><th>End Date</th>
          </tr>
          <tr>
            <td>Chase Ultimate Rewards</td>
            <td>British Airways Avios</td>
            <td>20%</td>
            <td>March 31, 2026</td>
          </tr>
        </table>
        """

        candidates = _parse_awardwallet_html(
            html,
            page_url="https://awardwallet.com/news/credit-card-transfer-bonuses/",
            source_name="AwardWallet",
            transfer_partners=TRANSFER_PARTNERS,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_program, "chase_ur")
        self.assertEqual(candidates[0].target_program, "avios")
        self.assertEqual(candidates[0].bonus_percentage, 0.20)

    def test_merge_scraped_candidates_marks_cross_source_match_verified(self) -> None:
        bonuses = _merge_scraped_candidates([
            ScrapedBonusCandidate(
                source_program="chase_ur",
                target_program="avios",
                bonus_percentage=0.20,
                source_name="Frequent Miler",
                source_url="https://frequentmiler.com/current-point-transfer-bonuses/",
                end_date=FUTURE_END_DATE,
            ),
            ScrapedBonusCandidate(
                source_program="chase_ur",
                target_program="avios",
                bonus_percentage=0.20,
                source_name="The Points Guy",
                source_url="https://thepointsguy.com/loyalty-programs/current-transfer-bonuses/",
                end_date=FUTURE_END_DATE,
            ),
        ])

        self.assertEqual(len(bonuses), 1)
        self.assertTrue(bonuses[0].verified)
        self.assertIn("Frequent Miler", bonuses[0].notes)
        self.assertIn("The Points Guy", bonuses[0].notes)

    def test_merge_transfer_bonus_lists_dedupes_manual_and_scraped(self) -> None:
        manual = TransferBonus(
            source_program="chase_ur",
            target_program="avios",
            bonus_percentage=0.20,
            effective_rate=1.2,
            end_date=FUTURE_END_DATE,
            source_url="https://example.com/manual",
            verified=True,
            notes="Manual entry",
        )
        scraped = TransferBonus(
            source_program="chase_ur",
            target_program="avios",
            bonus_percentage=0.20,
            effective_rate=1.2,
            end_date=FUTURE_END_DATE,
            source_url="https://frequentmiler.com/current-point-transfer-bonuses/",
            verified=False,
            notes="Sources: Frequent Miler",
        )

        merged = merge_transfer_bonus_lists([manual], [scraped])

        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].verified)
        self.assertIn("Manual entry", merged[0].notes)

    def test_load_transfer_bonuses_keeps_manual_entries_when_scrapers_disabled(self) -> None:
        config = {
            "transfer_bonuses": [
                {
                    "source_program": "chase_ur",
                    "target_program": "avios",
                    "bonus_percentage": 0.20,
                    "end_date": FUTURE_END_DATE_STR,
                }
            ]
        }

        bonuses = load_transfer_bonuses(
            config,
            TRANSFER_PARTNERS,
            enable_scrapers=False,
        )

        self.assertEqual(len(bonuses), 1)
        self.assertEqual(bonuses[0].source_program, "chase_ur")
