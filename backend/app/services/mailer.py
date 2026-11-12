import logging
from email.message import EmailMessage

import aiosmtplib

from app.core.config import get_settings

log = logging.getLogger("raven.mailer")


def smtp_configured() -> bool:
    return bool(get_settings().smtp_host)


async def send_email(
    *, to: list[str], subject: str, text: str, reply_to: str | None = None
) -> bool:
    s = get_settings()
    if not s.smtp_host or not to:
        log.info("SMTP not configured or no recipients; skipping email '%s'", subject)
        return False
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    try:
        await aiosmtplib.send(
            msg,
            hostname=s.smtp_host,
            port=s.smtp_port,
            username=s.smtp_username,
            password=s.smtp_password,
            start_tls=s.smtp_starttls,
            timeout=20,
        )
        return True
    except (aiosmtplib.SMTPException, OSError) as e:
        log.error("Failed to send email '%s': %s", subject, e)
        return False
