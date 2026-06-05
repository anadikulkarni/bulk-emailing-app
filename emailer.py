import os
import time
import smtplib
from email.message import EmailMessage
import streamlit as st

for key, value in st.secrets.items():
    os.environ[key] = str(value)

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

def _connect():
    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
    return server

def send_bulk(recipients, subject, body, attachment_name=None, attachment_bytes=None,
              progress_cb=None):
    results = []
    delay = float(os.environ.get("SEND_DELAY_SECONDS", "1"))
    RECONNECT_EVERY = 100

    server = _connect()
    sent_on_conn = 0

    for i, addr in enumerate(recipients):
        msg = _build_message(addr, subject, body, attachment_name, attachment_bytes)

        # proactively refresh the connection before Gmail drops it
        if sent_on_conn >= RECONNECT_EVERY:
            try:
                server.quit()
            except Exception:
                pass
            server = _connect()
            sent_on_conn = 0

        try:
            server.send_message(msg)
            results.append((addr, "SENT", None))
            sent_on_conn += 1
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, OSError):
            # connection dropped — reconnect once and retry this one message
            try:
                server = _connect()
                sent_on_conn = 0
                server.send_message(msg)
                results.append((addr, "SENT", None))
                sent_on_conn += 1
            except Exception as e2:
                results.append((addr, "FAILED", str(e2)))
        except Exception as e:
            results.append((addr, "FAILED", str(e)))

        if progress_cb:
            progress_cb(i + 1, len(recipients))
        time.sleep(delay)

    try:
        server.quit()
    except Exception:
        pass
    return results