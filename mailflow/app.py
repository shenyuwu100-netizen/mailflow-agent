"""Local synthetic-mail workbench; no mailbox credentials or real sending endpoints."""
import argparse
import json
from pathlib import Path
import uuid
from .workflow import Workflow
from flask import Flask, jsonify, request, render_template
from .policy import KNOWLEDGE
from .store import Conflict, DemoTransport, Store


def create_app(db_path="var/mailflow.db", *, live_model=False, max_model_calls=12):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 48000
    store = Store(db_path)
    app.extensions["mailflow_store"] = store
    workflow = Workflow(store, live=live_model, maximum=max_model_calls)
    app.extensions["mailflow_workflow"] = workflow

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
        budget = workflow.ledger.budget()
        return jsonify(jobs=store.list(), knowledge=KNOWLEDGE, mode="live-model-simulated-send" if live_model else "synthetic-demo",
                       model_calls=budget["used"], max_model_calls=max_model_calls, budget=budget)

    @app.get("/api/runs/<message_id>")
    def run_status(message_id):
        return jsonify(workflow.ledger.status(message_id))

    @app.post("/api/ingest")
    def ingest():
        data = request.get_json()
        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ("subject", "body")):
            raise ValueError("subject and body must be text")
        if len(data["subject"]) > 1000 or len(data["body"]) > 16000:
            raise ValueError("Email exceeds the analysis limit; content will not be silently truncated")
        if not data["body"].strip():
            raise ValueError("Email body is required")
        if data.get("message_id") is not None and (not isinstance(data["message_id"], str) or not 1 <= len(data["message_id"]) <= 200):
            raise ValueError("message_id must contain 1–200 characters")
        email = {"mailbox": "demo", "message_id": data.get("message_id") or uuid.uuid4().hex,
                 "sender": "customer@example.com", "subject": data["subject"], "body": data["body"]}
        if "has_attachments" in data:
            if type(data["has_attachments"]) is not bool:
                raise ValueError("has_attachments must be a boolean")
            email["has_attachments"] = data["has_attachments"]
        if "headers" in data:
            if not isinstance(data["headers"], dict) or len(data["headers"]) > 20 or any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 100 or len(v) > 500 for k,v in data["headers"].items()):
                raise ValueError("Invalid email headers")
            email["headers"] = data["headers"]
        job = workflow.process(email, automatic=data.get("automatic") is True)
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

    @app.post("/api/jobs/<job_id>/dispatch")
    def dispatch(job_id):
        return jsonify(store.dispatch(job_id, DemoTransport()))

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5087)
    parser.add_argument("--db", default="var/mailflow.db")
    parser.add_argument("--live-model", action="store_true", help="Explicitly enable paid model calls; sending remains simulated")
    parser.add_argument("--max-model-calls", type=int, default=12)
    args = parser.parse_args()
    create_app(args.db, live_model=args.live_model, max_model_calls=args.max_model_calls).run(host="127.0.0.1", port=args.port, debug=False)
