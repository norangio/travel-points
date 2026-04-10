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
        self.api_key = settings.resend_api_key
        resend.api_key = self.api_key
        self.from_email = settings.email_from_address
        self.from_name = settings.email_from_name
        if not self.api_key:
            logger.warning("RESEND_API_KEY is not set — email sending will fail")

    def send(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> str | None:
        """Send an email. Returns email ID on success, None on failure."""
        if not self.api_key:
            logger.error("Cannot send email to %s without RESEND_API_KEY", to_email)
            return None

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
        if recipients and self.from_email.strip().lower() == "onboarding@resend.dev":
            logger.warning(
                "Using Resend test sender onboarding@resend.dev. Resend only delivers "
                "this sender to the account owner; set EMAIL_FROM_ADDRESS to a verified "
                "sender before expecting all recipients to receive the digest."
            )
        ids = []
        for email in recipients:
            result = self.send(email, subject, html_body, text_body)
            if result:
                ids.append(result)
        logger.info(
            "Email send summary: %s/%s recipients accepted by Resend",
            len(ids),
            len(recipients),
        )
        return ids
