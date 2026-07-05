"""Sends the digest (or a failure notification) via Gmail SMTP, async.

Reuses the working pattern from job_digest/src/job_digest/emailer.py (Gmail
app-password auth), rewritten on aiosmtplib instead of smtplib so email
delivery -- itself a network call -- stays async like the rest of the
pipeline.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from email.message import EmailMessage

import aiosmtplib

from visa_jobs_api.config import Settings

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

_TAG_RE = re.compile(r"<[^>]+>")


def _build_message(*, settings: Settings, subject: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.gmail_address
    message["To"] = ", ".join(settings.recipient_list())
    return message


async def _deliver(message: EmailMessage, *, settings: Settings) -> None:
    await aiosmtplib.send(
        message,
        hostname=SMTP_HOST,
        port=SMTP_PORT,
        use_tls=True,
        username=settings.gmail_address,
        password=settings.gmail_app_password,
    )
    logger.info("Sent email %r to %s", message["Subject"], message["To"])


async def send_success_email(*, settings: Settings, subject: str, html_body: str) -> None:
    """Send the completed digest as HTML, with a plain-text fallback for clients that need one."""
    plain_text_fallback = _TAG_RE.sub("", html_body)

    message = _build_message(settings=settings, subject=subject)
    message.set_content(plain_text_fallback)
    message.add_alternative(html_body, subtype="html")
    await _deliver(message, settings=settings)


async def send_failure_email(*, settings: Settings, reason: str) -> None:
    """Notify the mailing list that a digest run failed, and why."""
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = f"[FAILED] Visa jobs digest run - {timestamp}"
    body = f"The visa jobs digest run at {timestamp} failed before it could send a digest.\n\nReason:\n{reason}\n"

    message = _build_message(settings=settings, subject=subject)
    message.set_content(body)
    await _deliver(message, settings=settings)
