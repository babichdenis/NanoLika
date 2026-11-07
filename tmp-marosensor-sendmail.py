#!/usr/bin/env python3
"""Отправка письма вотчдога через внешний SMTP (настройки — в env)."""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

host = os.environ.get("SMTP_HOST", "").strip()
port = int(os.environ.get("SMTP_PORT", "465"))
user = os.environ.get("SMTP_USER", "").strip()
password = os.environ.get("SMTP_PASS", "")
sender = os.environ.get("MAIL_FROM", user)
recipients = [x.strip() for x in os.environ.get("MAIL_TO", "").split(",") if x.strip()]
security = os.environ.get("SMTP_SECURITY", "ssl").strip().lower()

if not host or not sender or not recipients:
    sys.exit(2)

subject = sys.argv[1] if len(sys.argv) > 1 else "Marosensor watchdog"
body = sys.stdin.read()

msg = EmailMessage()
msg["Subject"] = subject
msg["From"] = sender
msg["To"] = ", ".join(recipients)
msg.set_content(body)

try:
    if security == "ssl" or port == 465:
        client = smtplib.SMTP_SSL(host, port, timeout=25, context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(host, port, timeout=25)
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
    if user:
        client.login(user, password)
    client.send_message(msg)
    client.quit()
except Exception as exc:
    print(f"sendmail: {exc}", file=sys.stderr)
    sys.exit(1)
