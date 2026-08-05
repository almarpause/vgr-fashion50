"""Alerting: SMTP email via environment variables, with file/sheet fallback.

Secrets are NEVER hardcoded.  Configure via environment variables:

    FASHION50_SMTP_HOST     e.g. smtp.gmail.com
    FASHION50_SMTP_PORT     e.g. 587
    FASHION50_SMTP_USER     login / from address
    FASHION50_SMTP_PASS     app password / token
    FASHION50_ALERT_TO      comma-separated recipient list
    FASHION50_SMTP_TLS      "1" (default) to STARTTLS, "0" to disable

If SMTP is not fully configured, alerts are written to the ``Alerts`` sheet
(by the caller) and appended to ``logs/alerts.log`` — never lost.
"""
from __future__ import annotations

import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText

from . import config


def _smtp_configured() -> bool:
    return all(
        os.environ.get(k)
        for k in ("FASHION50_SMTP_HOST", "FASHION50_SMTP_USER",
                  "FASHION50_SMTP_PASS", "FASHION50_ALERT_TO")
    )


def _log_to_file(subject: str, body: str) -> None:
    os.makedirs(os.path.dirname(config.ALERTS_LOG) or ".", exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with open(config.ALERTS_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"\n===== {ts}  {subject} =====\n{body}\n")


def send_alert(subject: str, body: str) -> str:
    """Send an alert.  Returns the channel used: 'email' or 'log'.

    Always writes to the log file; additionally emails when SMTP is configured.
    """
    _log_to_file(subject, body)
    if not _smtp_configured():
        return "log"
    try:
        host = os.environ["FASHION50_SMTP_HOST"]
        port = int(os.environ.get("FASHION50_SMTP_PORT", "587"))
        user = os.environ["FASHION50_SMTP_USER"]
        password = os.environ["FASHION50_SMTP_PASS"]
        recipients = [r.strip() for r in
                      os.environ["FASHION50_ALERT_TO"].split(",") if r.strip()]
        use_tls = os.environ.get("FASHION50_SMTP_TLS", "1") == "1"

        msg = MIMEText(body)
        msg["Subject"] = f"[Fashion50] {subject}"
        msg["From"] = user
        msg["To"] = ", ".join(recipients)

        with smtplib.SMTP(host, port, timeout=30) as server:
            if use_tls:
                server.starttls()
            server.login(user, password)
            server.sendmail(user, recipients, msg.as_string())
        return "email"
    except Exception as exc:  # pragma: no cover - network dependent
        _log_to_file(subject + " (EMAIL FAILED)", f"{exc!r}\n\n{body}")
        return "log"
