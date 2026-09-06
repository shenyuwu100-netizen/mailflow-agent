"""Local synthetic-mail workbench; no mailbox credentials or real sending endpoints."""
import argparse
import json
from pathlib import Path
import uuid
from threading import Lock
from flask import Flask, jsonify, request, render_template
from .policy import KNOWLEDGE, decide, local_proposal
from .store import Conflict, DemoTransport, Store


def create_app(db_path="var/mailflow.db", *, live_model=False, max_model_calls=3):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 48000
    store = Store(db_path)
    app.extensions["mailflow_store"] = store
    calls = 0
    budget_lock = Lock()

    @app.before_request
    def local_only():
        if request.host.split(":")[0] not in {"127.0.0.1", "localhost"}:
            return jsonify(error="Local demo only"), 403
        if request.method == "POST" and (request.headers.get("X-MailFlow-Demo") != "1" or (request.headers.get("Origin") and request.headers["Origin"] != request.host_url.rstrip("/"))):
            return jsonify(error="Invalid demo request origin"), 403

    @app.errorhandler(ValueError)
    def bad_input(exc):
        return jsonify(error=str(exc)), 409 if isinstance(exc, Conflict) else 400

    @app.errorhandler(KeyError)
    def not_found(exc):
        return jsonify(error="Not found"), 404

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/jobs")
    def jobs():
        return jsonify(jobs=store.list(), knowledge=KNOWLEDGE, mode="live-model-simulated-send" if live_model else "synthetic-demo",
                       model_calls=calls, max_model_calls=max_model_calls)

    @app.post("/api/ingest")
    def ingest():
        nonlocal calls
        data = request.get_json()
        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ("subject", "body")):
            raise ValueError("subject and body must be text")
        if data.get("message_id") is not None and (not isinstance(data["message_id"], str) or not 1 <= len(data["message_id"]) <= 200):
            raise ValueError("message_id must contain 1–200 characters")
        email = {"mailbox": "demo", "message_id": data.get("message_id") or uuid.uuid4().hex,
                 "sender": "customer@example.com", "subject": data["subject"][:1000], "body": data["body"][:16000]}
        if live_model:
            with budget_lock:
                if calls >= max_model_calls:
                    proposal = {"error": "model_call_budget_exhausted"}
                else:
                    calls += 1
                    proposal = None
            if proposal is None:
                from .model import propose
                proposal = propose(email)
        else:
            proposal = local_proposal(email)
        decision = decide(email, proposal, enabled=data.get("automatic") is True).to_dict()
        decision["analysis"] = {k: proposal[k] for k in ("method", "usage", "error", "http_status") if k in proposal}
        job = store.submit(email, decision)
        if job["status"] == "ready":
            job = store.dispatch(job["id"], DemoTransport())
        return jsonify(job)

    @app.post("/api/jobs/<job_id>/review")
    def review(job_id):
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("Expected review object")
        job = store.review(job_id, data.get("version"), data.get("reply", ""), data.get("action"))
        if job["status"] == "ready":
            job = store.dispatch(job_id, DemoTransport())
        return jsonify(job)

    @app.get("/api/export")
    def export():
        return app.response_class(json.dumps(store.list(), ensure_ascii=False, indent=2), mimetype="application/json",
                                  headers={"Content-Disposition": "attachment; filename=mailflow-audit.json"})

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5087)
    parser.add_argument("--db", default="var/mailflow.db")
    parser.add_argument("--live-model", action="store_true", help="Explicitly enable paid model calls; sending remains simulated")
    parser.add_argument("--max-model-calls", type=int, default=3)
    args = parser.parse_args()
    create_app(args.db, live_model=args.live_model, max_model_calls=args.max_model_calls).run(host="127.0.0.1", port=args.port, debug=False)
