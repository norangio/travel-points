import unittest
from unittest.mock import patch

from src.email.sender import EmailSender


class EmailSenderTest(unittest.TestCase):
    def test_warns_when_using_resend_test_sender(self) -> None:
        sender = EmailSender.__new__(EmailSender)
        sender.api_key = "test-key"
        sender.from_email = "onboarding@resend.dev"
        sender.from_name = "Points Deal Finder"

        with patch.object(EmailSender, "send", return_value="email-1"):
            with self.assertLogs("src.email.sender", level="WARNING") as logs:
                ids = sender.send_to_all(
                    recipients=["traveler@example.com"],
                    subject="Subject",
                    html_body="<p>Hi</p>",
                    text_body="Hi",
                )

        self.assertEqual(ids, ["email-1"])
        self.assertIn(
            "Using Resend test sender onboarding@resend.dev",
            "\n".join(logs.output),
        )
