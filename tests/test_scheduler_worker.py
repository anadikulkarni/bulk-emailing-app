"""Tests for scheduler_worker's sending logic, against an in-memory database.

Run with: pip install pytest && pytest tests/
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scheduler_worker


class FakeDb:
    """The scheduled-send functions of db.py, held in memory."""

    def __init__(self, jobs, sent_last_24h=0):
        self.jobs = {j["id"]: {"status": "PENDING", "due": True, **j} for j in jobs}
        self.sent_last_24h = sent_last_24h
        self.log = []                 # (job_id, recipient, status, batch_id)
        self.flushes = []             # size of each logged batch
        self.taken_elsewhere = set()  # ids another run claims first

    def release_stale_scheduled_sends(self, stale_minutes):
        stale = [j for j in self.jobs.values() if j["status"] == "SENDING"]
        for j in stale:
            j["status"] = "PENDING"
        return len(stale)

    def count_sent_last_24h(self):
        return self.sent_last_24h

    def get_due_scheduled_sends(self):
        return [dict(j) for j in self.jobs.values() if j["status"] == "PENDING" and j["due"]]

    def claim_scheduled_send(self, send_id):
        job = self.jobs[send_id]
        if send_id in self.taken_elsewhere or job["status"] != "PENDING":
            return False
        job["status"] = "SENDING"
        return True

    def get_scheduled_attempts(self, send_id):
        return {r: s for jid, r, s, _ in self.log if jid == send_id}

    def log_scheduled_progress(self, send_id, records):
        self.flushes.append(len(records))
        for job_id, _rtype, recipient, _subject, status, _error, _by, batch_id in records:
            self.log.append((job_id, recipient, status, batch_id))
            if status == "SENT":
                self.sent_last_24h += 1

    def finish_scheduled_send(self, send_id, done, summary):
        self.jobs[send_id]["status"] = "SENT" if done else "PENDING"
        self.jobs[send_id]["summary"] = summary


def make_sender(fail=(), crash_after=None):
    """A send_bulk stand-in that records every address it is asked to send to."""
    sent = []

    def send_bulk(recipients, subject, body, attachment_name=None, attachment_bytes=None,
                  progress_cb=None, on_result=None):
        results = []
        for addr in recipients:
            if crash_after is not None and len(sent) >= crash_after:
                raise ConnectionError("SMTP connection dropped")
            result = (addr, "FAILED", "bad address") if addr in fail else (addr, "SENT", None)
            sent.append(addr)
            results.append(result)
            if on_result:
                on_result(*result)
        return results

    send_bulk.sent = sent
    return send_bulk


def job(job_id, n_recipients, **extra):
    return {
        "id": job_id, "retailer_type": "CUSTOM", "subject": f"Campaign {job_id}",
        "body": "Hello", "attachment_name": None, "attachment_data": None,
        "recipients": json.dumps([f"r{i}@example.com" for i in range(n_recipients)]),
        "created_by": "anadi", **extra,
    }


def test_sends_a_due_job_and_marks_it_sent():
    db = FakeDb([job(1, 25)])
    send = make_sender()
    scheduler_worker.run(db, send, daily_cap=1800)
    assert len(send.sent) == 25
    assert db.jobs[1]["status"] == "SENT"
    assert db.jobs[1]["summary"] == "25/25 sent"
    assert db.flushes == [10, 10, 5]  # logged as it goes, not only at the end


def test_does_nothing_when_nothing_is_due():
    db = FakeDb([job(1, 5, due=False)])
    send = make_sender()
    scheduler_worker.run(db, send, daily_cap=1800)
    assert send.sent == []
    assert db.jobs[1]["status"] == "PENDING"


def test_skips_a_job_another_run_claimed():
    db = FakeDb([job(1, 5), job(2, 5)])
    db.taken_elsewhere = {1}
    send = make_sender()
    scheduler_worker.run(db, send, daily_cap=1800)
    assert send.sent == [f"r{i}@example.com" for i in range(5)]
    assert {jid for jid, *_ in db.log} == {2}


def test_daily_allowance_pauses_then_resumes_without_repeats():
    db = FakeDb([job(1, 15)], sent_last_24h=4)
    send = make_sender()
    scheduler_worker.run(db, send, daily_cap=10)
    assert len(send.sent) == 6
    assert db.jobs[1]["status"] == "PENDING"
    assert db.jobs[1]["summary"].startswith("6 of 15 tried so far")

    db.sent_last_24h = 0  # a day later, the allowance has freed up
    scheduler_worker.run(db, send, daily_cap=10)
    assert len(send.sent) == 15
    assert len(set(send.sent)) == 15  # nobody emailed twice
    assert db.jobs[1]["status"] == "SENT"


def test_allowance_already_used_up_claims_nothing():
    db = FakeDb([job(1, 5)], sent_last_24h=1800)
    send = make_sender()
    scheduler_worker.run(db, send, daily_cap=1800)
    assert send.sent == []
    assert db.jobs[1]["status"] == "PENDING"


def test_resume_skips_recipients_already_tried():
    db = FakeDb([job(1, 6)])
    db.log = [(1, "r0@example.com", "SENT", "b"), (1, "r1@example.com", "FAILED", "b")]
    send = make_sender()
    scheduler_worker.run(db, send, daily_cap=1800)
    assert send.sent == [f"r{i}@example.com" for i in range(2, 6)]  # failures aren't retried
    assert db.jobs[1]["summary"] == "5/6 sent, 1 failed"


def test_failures_are_logged_and_counted():
    db = FakeDb([job(1, 4)])
    send = make_sender(fail={"r2@example.com"})
    scheduler_worker.run(db, send, daily_cap=1800)
    assert ("r2@example.com", "FAILED") in {(r, s) for _, r, s, _ in db.log}
    assert db.jobs[1]["summary"] == "3/4 sent, 1 failed"


def test_crash_mid_send_keeps_progress_and_resumes():
    db = FakeDb([job(1, 30)])
    crashing = make_sender(crash_after=13)
    scheduler_worker.run(db, crashing, daily_cap=1800)
    assert len(db.log) == 13  # 10 logged mid-send, the last 3 on the way out
    assert db.jobs[1]["status"] == "PENDING"
    assert "SMTP connection dropped" in db.jobs[1]["summary"]

    healthy = make_sender()
    scheduler_worker.run(db, healthy, daily_cap=1800)
    assert healthy.sent == [f"r{i}@example.com" for i in range(13, 30)]
    assert db.jobs[1]["status"] == "SENT"
    assert len({batch for *_, batch in db.log}) == 1  # one campaign in the activity log


def test_stale_claim_from_a_killed_run_is_released():
    db = FakeDb([job(1, 3, status="SENDING")])
    send = make_sender()
    scheduler_worker.run(db, send, daily_cap=1800)
    assert len(send.sent) == 3
    assert db.jobs[1]["status"] == "SENT"
