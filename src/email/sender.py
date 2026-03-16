"""Email sender using Resend — matches email-reports pattern."""

from __future__ import annotations

import logging

import resend

from src.config import get_settings

logger = logging.getLogger(__name__)


class EmailSender:
    """Send emails via Resend API."""

    def __init__(self) -> None:
        settings = get_settings()
        resend.api_key = settings.resend_api_key
        self.from_email = settings.email_from_address
        self.from_name = settings.email_from_name

    def send(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> str | None:
        """Send an email. Returns email ID on success, None on failure."""
        try:
            response = resend.Emails.send(
                {
                    "from": f"{self.from_name} <{self.from_email}>",
                    "to": [to_email],
                    "subject": subject,
                    "html": html_body,
                    "text": text_body,
                }
            )
            email_id = response.get("id")
            logger.info(f"Email sent to {to_email}, ID: {email_id}")
            return email_id
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return None

    def send_to_all(
        self,
        recipients: list[str],
        subject: str,
        html_body: str,
        text_body: str,
    ) -> list[str]:
        """Send to multiple recipients. Returns list of successful email IDs."""
        ids = []
        for email in recipients:
            result = self.send(email, subject, html_body, text_body)
            if result:
                ids.append(result)
        return ids
