"""Durable outbox: atomic claims, no automatic retries of uncertain sends."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, mailbox TEXT NOT NULL, message_id TEXT NOT NULL,
              fingerprint TEXT NOT NULL, email TEXT NOT NULL, decision TEXT NOT NULL,
              reply TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
              receipt TEXT, created_at TEXT NOT NULL, UNIQUE(mailbox,message_id));
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY, job_id TEXT NOT NULL, action TEXT NOT NULL,
              detail TEXT NOT NULL, at TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def event(db, job_id, action, detail=""):
        db.execute("INSERT INTO events(job_id,action,detail,at) VALUES(?,?,?,?)",
                   (job_id, action, detail, datetime.now(timezone.utc).isoformat()))

    def submit(self, email, decision):
        if not email.get("message_id") or not email.get("mailbox"):
            raise ValueError("mailbox and message_id are required")
        payload = json.dumps(email, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE mailbox=? AND message_id=?",
                             (email["mailbox"], email["message_id"])).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise Conflict("Message ID reused with different content")
                job_id = row["id"]
            else:
                job_id = uuid.uuid4().hex
                status = "ready" if decision["route"] == "auto" else "review"
                db.execute("INSERT INTO jobs(id,mailbox,message_id,fingerprint,email,decision,reply,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (job_id, email["mailbox"], email["message_id"], fingerprint, payload,
                            json.dumps(decision, ensure_ascii=False), decision["reply"], status,
                            datetime.now(timezone.utc).isoformat()))
                self.event(db, job_id, "classified", json.dumps(decision["reasons"]))
        return self.get(job_id)

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            data = dict(row)
            data["email"] = json.loads(data["email"])
            data["decision"] = json.loads(data["decision"])
            data["events"] = [dict(x) for x in db.execute("SELECT action,detail,at FROM events WHERE job_id=? ORDER BY seq", (job_id,))]
            return data

    def list(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 100")]
        return [self.get(key) for key in ids]

    def review(self, job_id, version, reply, action, operator="local-reviewer"):
        if type(version) is not int or not isinstance(reply, str):
            raise ValueError("A numeric version and text reply are required")
        if action not in {"approve", "reject"}:
            raise ValueError("Invalid review action")
        if action == "approve" and (not isinstance(reply, str) or not reply.strip() or len(reply) > 12000):
            raise ValueError("Reply must contain 1–12000 characters")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("UPDATE jobs SET status=?,reply=?,version=version+1 WHERE id=? AND version=? AND status='review'",
                             ("ready" if action == "approve" else "rejected", reply, job_id, version))
            if cur.rowcount != 1:
                raise Conflict("Stale review or job no longer awaiting review")
            self.event(db, job_id, action, operator)
        return self.get(job_id)

    def dispatch(self, job_id, transport=None):
        if transport is None:
            # Eligible does not mean delivered; absence of a transport changes no state.
            return self.get(job_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT decision FROM jobs WHERE id=? AND status='ready'", (job_id,)).fetchone()
            if row is not None:
                from .policy import VERSION, knowledge_hash
                decision = json.loads(row["decision"])
                approved = db.execute("SELECT 1 FROM events WHERE job_id=? AND action='approve'", (job_id,)).fetchone()
                if not approved and (decision.get("policy_version") != VERSION or decision.get("knowledge_digest") != knowledge_hash()):
                    db.execute("UPDATE jobs SET status='review',version=version+1 WHERE id=?", (job_id,))
                    self.event(db, job_id, "policy_changed", "Re-review required before sending")
                    return_after_commit = True
                else:
                    return_after_commit = False
            else:
                return_after_commit = False
            if return_after_commit:
                db.commit()
                return self.get(job_id)
            cur = db.execute("UPDATE jobs SET status='sending',version=version+1 WHERE id=? AND status='ready'", (job_id,))
            if cur.rowcount != 1:
                raise Conflict("Job already claimed or not approved")
            self.event(db, job_id, "send_claimed", transport.name)
        job = self.get(job_id)
        try:
            receipt = transport.send(job)
            if not isinstance(receipt, str) or not receipt:
                raise ValueError("Transport did not acknowledge delivery acceptance")
        except Exception as exc:
            # Network errors can mean the provider accepted the mail. Never blindly retry.
            with self.connect() as db:
                db.execute("UPDATE jobs SET status='send_unknown',version=version+1 WHERE id=?", (job_id,))
                self.event(db, job_id, "send_unknown", type(exc).__name__)
        else:
            status = "simulated_sent" if transport.simulated else "sent"
            with self.connect() as db:
                db.execute("UPDATE jobs SET status=?,receipt=?,version=version+1 WHERE id=?", (status, receipt, job_id))
                self.event(db, job_id, status, receipt)
        return self.get(job_id)


class DemoTransport:
    name = "local-simulator"
    simulated = True

    def send(self, job):
        return "demo:" + job["id"]
