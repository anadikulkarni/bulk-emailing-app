"""Send the scheduled emails that are due.

Run it every few minutes on a machine that can reach the database, from this
folder, with the same .streamlit/secrets.toml as the app:

    python scheduler_worker.py

Every 5 minutes with Windows Task Scheduler:

    schtasks /create /tn "Bulk email scheduler" /sc minute /mo 5 /tr "\"C:\\Path\\To\\python.exe\" \"C:\\Path\\To\\bulk-emailing-app\\scheduler_worker.py\""

or with cron:

    */5 * * * * cd /path/to/bulk-emailing-app && python scheduler_worker.py

Each run:
  1. returns sends left half-done by a crashed run to the queue,
  2. works out how much of the daily allowance (DAILY_CAP per rolling 24 hours) is left,
  3. claims each due send atomically, so overlapping runs never send one twice,
  4. sends to the recipients not yet tried, logging each result as it goes,
  5. marks the send SENT once everyone has been tried, or leaves it queued
     to carry on when the allowance frees up.
"""
import json
import os
import uuid
from pathlib import Path

STALE_AFTER_MINUTES = 30   # a running send refreshes its claim every LOG_EVERY emails
LOG_EVERY = 10             # results are logged in batches of this size while sending


def run(db, send_bulk, daily_cap):
    """Send whatever is due. db and send_bulk are passed in so tests can fake them."""
    released = db.release_stale_scheduled_sends(STALE_AFTER_MINUTES)
    if released:
        print(f"Re-queued {released} send(s) a crashed run left unfinished.")

    allowance = daily_cap - db.count_sent_last_24h()
    due = db.get_due_scheduled_sends()
    if not due:
        print("Nothing due.")
        return

    for job in due:
        if allowance <= 0:
            print(f"Daily allowance of {daily_cap} used up; the rest wait for a later run.")
            return
        if not db.claim_scheduled_send(job["id"]):
            continue  # another run claimed it first
        try:
            allowance -= send_job(db, send_bulk, job, allowance)
        except Exception as e:
            # Leave it queued; the next run resumes after the recipients already logged
            db.finish_scheduled_send(job["id"], done=False, summary=f"Paused after an error: {e}")
            print(f"Send {job['id']} paused after an error: {e}")
            return


def send_job(db, send_bulk, job, allowance):
    """Send one claimed job to the recipients not yet tried, up to the allowance.
    Returns how many emails it attempted."""
    recipients = json.loads(job["recipients"])
    attempts = db.get_scheduled_attempts(job["id"])
    todo = [r for r in recipients if r not in attempts]
    batch = todo[:allowance]
    # One batch id per scheduled send, so resumed runs appear as one campaign in the log
    batch_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bulk-emailing-app/scheduled/{job['id']}"))
    unlogged = []

    def flush():
        if unlogged:
            db.log_scheduled_progress(job["id"], unlogged)
            unlogged.clear()

    def on_result(addr, status, error):
        attempts[addr] = status
        unlogged.append((job["id"], job["retailer_type"], addr, job["subject"],
                         status, error, job["created_by"], batch_id))
        if len(unlogged) >= LOG_EVERY:
            flush()

    attachment = bytes(job["attachment_data"]) if job["attachment_data"] else None
    try:
        if batch:
            send_bulk(batch, job["subject"], job["body"], job["attachment_name"],
                      attachment, on_result=on_result)
    finally:
        flush()

    sent = sum(1 for r in recipients if attempts.get(r) == "SENT")
    failed = sum(1 for r in recipients if attempts.get(r) == "FAILED")
    remaining = len(todo) - len(batch)
    if remaining:
        summary = (f"{sent + failed} of {len(recipients)} tried so far ({failed} failed); "
                   "the rest go out as the daily allowance frees up")
        print(f"Send {job['id']}: {summary}")
        db.finish_scheduled_send(job["id"], done=False, summary=summary)
    else:
        summary = f"{sent}/{len(recipients)} sent" + (f", {failed} failed" if failed else "")
        print(f"Send {job['id']}: {summary}")
        db.finish_scheduled_send(job["id"], done=True, summary=summary)
    return len(batch)


def main():
    # db and emailer read .streamlit/secrets.toml from the working directory when
    # imported, and a scheduled task may start anywhere
    os.chdir(Path(__file__).resolve().parent)
    import db
    from emailer import send_bulk, DAILY_CAP
    run(db, send_bulk, DAILY_CAP)


if __name__ == "__main__":
    main()
