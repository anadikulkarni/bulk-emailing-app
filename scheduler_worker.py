import datetime
from db import get_conn, get_emails_for_type, log_email
from emailer import send_bulk

def run():
    now = datetime.datetime.now()
    with get_conn() as c:
        jobs = c.execute(
            "SELECT id, retailer_type, subject, body, attachment_name, attachment_data, created_by "
            "FROM SCHEDULED_EMAILS WHERE status='PENDING' AND send_at <= ?", now
        ).fetchall()

    for job in jobs:
        # claim it so overlapping runs don't double-send
        with get_conn() as c:
            updated = c.execute(
                "UPDATE SCHEDULED_EMAILS SET status='PROCESSING' WHERE id=? AND status='PENDING'",
                job.id,
            ).rowcount
        if not updated:
            continue

        recipients = get_emails_for_type(job.retailer_type)
        att_bytes = bytes(job.attachment_data) if job.attachment_data else None
        results = send_bulk(recipients, job.subject, job.body,
                            job.attachment_name, att_bytes)
        for addr, status, err in results:
            log_email(job.id, job.retailer_type, addr, job.subject, status, err, job.created_by)

        sent = sum(1 for _, s, _ in results if s == "SENT")
        summary = f"{sent}/{len(results)} sent"
        with get_conn() as c:
            c.execute(
                "UPDATE SCHEDULED_EMAILS SET status='SENT', processed_at=?, result_summary=? WHERE id=?",
                datetime.datetime.now(), summary, job.id,
            )
        print(f"Job {job.id}: {summary}")

if __name__ == "__main__":
    run()