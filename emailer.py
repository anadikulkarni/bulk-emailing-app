import os
import time
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

def _build_message(to_addr, subject, body, attachment_name=None, attachment_bytes=None):
    msg = EmailMessage()
    msg["From"] = f"{os.environ.get('SENDER_NAME','')} <{os.environ['GMAIL_ADDRESS']}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment_name and attachment_bytes:
        msg.add_attachment(
            attachment_bytes,
            maintype="application",
            subtype="octet-stream",
            filename=attachment_name,
        )
    return msg

def send_bulk(recipients, subject, body, attachment_name=None, attachment_bytes=None,
              progress_cb=None):
    """Returns list of (recipient, status, error)."""
    results = []
    delay = float(os.environ.get("SEND_DELAY_SECONDS", "1"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
        for i, addr in enumerate(recipients):
            try:
                server.send_message(
                    _build_message(addr, subject, body, attachment_name, attachment_bytes)
                )
                results.append((addr, "SENT", None))
            except Exception as e:
                results.append((addr, "FAILED", str(e)))
            if progress_cb:
                progress_cb(i + 1, len(recipients))
            time.sleep(delay)
    return results