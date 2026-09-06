"""Durable analysis claims and lifetime request budget shared by processes."""
import hashlib
import json
from .store import Conflict


class Ledger:
    def __init__(self, store, maximum):
        if maximum is not None and (type(maximum) is not int or maximum < 0):
            raise ValueError("Model call budget must be a non-negative integer or None (unlimited)")
        self.store, self.maximum = store, maximum
        with store.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS analyses (
              mailbox TEXT, message_id TEXT, fingerprint TEXT NOT NULL,
              state TEXT NOT NULL, stages TEXT NOT NULL DEFAULT '[]', job_id TEXT,
              PRIMARY KEY(mailbox,message_id));
            CREATE TABLE IF NOT EXISTS model_calls (
              id INTEGER PRIMARY KEY, mailbox TEXT, message_id TEXT, stage TEXT,
              state TEXT NOT NULL, usage TEXT, error TEXT,
              created_at TEXT DEFAULT (datetime('now')));
            """)

    def claim(self, email, automatic):
        fingerprint = hashlib.sha256(json.dumps({"email": email, "automatic": automatic}, sort_keys=True).encode()).hexdigest()
        key = email["mailbox"], email["message_id"]
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM analyses WHERE mailbox=? AND message_id=?", key).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise Conflict("Request ID reused with different content or settings")
                if row["job_id"]:
                    return row["job_id"]
                # Recover a crash after saving the result but before completing the analysis.
                job = db.execute("SELECT id FROM jobs WHERE mailbox=? AND message_id=?", key).fetchone()
                if job:
                    db.execute("UPDATE analyses SET state='complete',job_id=? WHERE mailbox=? AND message_id=?", (job[0], *key))
                    return job[0]
                raise Conflict("Analysis already claimed; inspect processing state before resubmitting")
            previous = db.execute("SELECT id,fingerprint FROM jobs WHERE mailbox=? AND message_id=?", key).fetchone()
            if previous:
                prior_fingerprint = hashlib.sha256(json.dumps(email, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if previous["fingerprint"] != prior_fingerprint:
                    raise Conflict("Message ID reused with different content")
                # Adopt jobs saved by v0.1 before analysis claims existed, without paying again.
                db.execute("INSERT INTO analyses(mailbox,message_id,fingerprint,state,job_id) VALUES(?,?,?,'complete',?)", (*key, fingerprint, previous["id"]))
                return previous["id"]
            db.execute("INSERT INTO analyses(mailbox,message_id,fingerprint,state) VALUES(?,?,?,'processing')", (*key, fingerprint))
        return None

    def stage(self, email, name, detail):
        key = email["mailbox"], email["message_id"]
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            stages = json.loads(db.execute("SELECT stages FROM analyses WHERE mailbox=? AND message_id=?", key).fetchone()[0])
            stages.append({"stage": name, "detail": detail})
            db.execute("UPDATE analyses SET stages=? WHERE mailbox=? AND message_id=?", (json.dumps(stages, ensure_ascii=False), *key))

    def complete(self, email, job_id):
        with self.store.connect() as db:
            db.execute("UPDATE analyses SET state='complete',job_id=? WHERE mailbox=? AND message_id=?", (job_id, email["mailbox"], email["message_id"]))

    def status(self, message_id):
        with self.store.connect() as db:
            row = db.execute("SELECT state,stages,job_id FROM analyses WHERE mailbox='demo' AND message_id=?", (message_id,)).fetchone()
        if not row:
            raise KeyError(message_id)
        return {"state": row["state"], "stages": json.loads(row["stages"]), "job_id": row["job_id"]}

    def reserve(self, email, stage):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0]
            if self.maximum is not None and count >= self.maximum:
                return None
            return db.execute("INSERT INTO model_calls(mailbox,message_id,stage,state) VALUES(?,?,?,'reserved')",
                              (email["mailbox"], email["message_id"], stage)).lastrowid

    def finish(self, call_id, result):
        with self.store.connect() as db:
            db.execute("UPDATE model_calls SET state=?,usage=?,error=? WHERE id=?",
                       ("failed" if result.get("error") else "complete", json.dumps(result.get("usage", {})), result.get("error"), call_id))

    def budget(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT state,usage FROM model_calls").fetchall()
        return {"used": len(rows), "maximum": self.maximum, "remaining": None if self.maximum is None else max(0, self.maximum-len(rows)),
                "known_tokens": sum(json.loads(row["usage"] or "{}").get("total_tokens", 0) for row in rows),
                "unknown_usage_calls": sum(not json.loads(row["usage"] or "{}").get("total_tokens") for row in rows)}
