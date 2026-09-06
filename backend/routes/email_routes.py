"""
Email API routes.
Blueprint prefix: /api
"""

import json
import time
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, session

from models.database import get_db_connection
from services.graph_service import GraphService, EmailSendError

from services.classification_service import ClassificationService
from services.reply_service import ReplyService
from routes.auth_routes import get_valid_token

email_bp = Blueprint("email", __name__)

classification_svc = ClassificationService()
reply_svc = ReplyService()


def _require_auth():
    """Return (access_token, user_email) or raise if not authenticated."""
    token, needs_reauth = get_valid_token()
    if not token or needs_reauth:
        return None, None
    user = session.get("user", {})
    email = user.get("preferred_username", user.get("email", "system"))
    return token, email


# ---------------------------------------------------------------------------
# POST /api/fetch  – Pull new emails from Outlook and process them
# ---------------------------------------------------------------------------
@email_bp.route("/fetch", methods=["POST"])
def fetch_emails():
    token, operator = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    graph = GraphService(token)
    top = int(request.json.get("top", 10)) if request.is_json else 10
    fetch_started_at = time.perf_counter()

    print(f"[FETCH] 开始拉取邮件，top={top}", flush=True)
    try:
        emails = graph.get_emails(top=top)
    except Exception as e:
        print(f"[FETCH] Graph API 调用失败: {e}", flush=True)
        return jsonify({"error": f"Graph API 调用失败: {str(e)}"}), 500

    print(f"[FETCH] Graph 返回 {len(emails)} 封邮件", flush=True)

    processed = []
    skipped = []
    failed = []
    status_counts = {
        "auto_sent": 0,
        "pending_review": 0,
        "ignored_no_reply": 0,
        "send_failed": 0,
    }
    total_latency_ms = 0.0

    for msg in emails:
        started = time.perf_counter()
        message_id = msg.get("id", "")

        try:
            with get_db_connection() as conn:
                existing = conn.execute(
                    "SELECT id FROM emails WHERE message_id = ?", (message_id,)
                ).fetchone()

            if existing:
                skipped.append(message_id)
                continue

            subject = msg.get("subject", "(No Subject)")
            sender = msg.get("from", {}).get("emailAddress", {}).get("address", "unknown")
            received_at = msg.get("receivedDateTime", "")
            body = msg.get("body", {}).get("content", msg.get("bodyPreview", ""))

            classification = classification_svc.classify_email(subject, body)
            result = reply_svc.process_email(
                message_id=message_id,
                subject=subject,
                sender=sender,
                received_at=received_at,
                body=body,
                classification=classification,
                graph_service=graph,
                operator=operator,
            )
            processed.append(result)
            status = result.get("status", "pending_review")
            if status in status_counts:
                status_counts[status] += 1

            item_latency_ms = round((time.perf_counter() - started) * 1000, 2)
            total_latency_ms += item_latency_ms
            print(
                "[PIPELINE] " + json.dumps({
                    "message_id": message_id,
                    "status": status,
                    "category": result.get("category"),
                    "confidence": result.get("confidence"),
                    "latency_ms": item_latency_ms,
                }, ensure_ascii=False),
                flush=True,
            )
        except Exception as e:
            failed.append({"message_id": message_id, "error": str(e)})
            print(
                "[PIPELINE] " + json.dumps({
                    "message_id": message_id,
                    "status": "processing_failed",
                    "error": str(e),
                }, ensure_ascii=False),
                flush=True,
            )

    total_elapsed_ms = round((time.perf_counter() - fetch_started_at) * 1000, 2)
    avg_latency_ms = round(total_latency_ms / len(processed), 2) if processed else 0.0

    return jsonify({
        "processed": len(processed),
        "skipped": len(skipped),
        "failed": len(failed),
        "status_breakdown": status_counts,
        "pipeline_metrics": {
            "total_elapsed_ms": total_elapsed_ms,
            "avg_per_email_ms": avg_latency_ms,
        },
        "errors": failed[:20],
        "emails": processed,
    })



# ---------------------------------------------------------------------------
# GET /api/emails  – List emails with optional filters and pagination
# ---------------------------------------------------------------------------
@email_bp.route("/emails", methods=["GET"])
def list_emails():
    token, _ = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 20))
    status_filter = request.args.get("status")
    category_filter = request.args.get("category")
    search = request.args.get("search", "").strip()

    offset = (page - 1) * per_page

    where_clauses = ["e.is_deleted = 0"]
    params = []

    if status_filter:
        where_clauses.append("e.status = ?")
        params.append(status_filter)
    if category_filter:
        where_clauses.append("e.category = ?")
        params.append(category_filter)
    if search:
        where_clauses.append("(e.subject LIKE ? OR e.sender LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with get_db_connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) as cnt FROM emails e {where_sql}", params
        ).fetchone()["cnt"]

        rows = conn.execute(
            f"""
            SELECT e.id, e.message_id, e.subject, e.sender, e.received_at,
                   e.category, e.confidence, e.status, e.retry_count, e.last_error, e.created_at,
                   r.reply_text

            FROM emails e
            LEFT JOIN replies r ON r.email_id = e.id
            {where_sql}
            ORDER BY e.created_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "emails": [dict(r) for r in rows],
    })


# ---------------------------------------------------------------------------
# GET /api/emails/<id>  – Single email detail
# ---------------------------------------------------------------------------
@email_bp.route("/emails/<int:email_id>", methods=["GET"])
def get_email(email_id):
    token, _ = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT e.*, r.reply_text, r.sent_at
            FROM emails e
            LEFT JOIN replies r ON r.email_id = e.id
            WHERE e.id = ?
            """,
            (email_id,),
        ).fetchone()

    if not row:
        return jsonify({"error": "Email not found"}), 404

    return jsonify(dict(row))


# ---------------------------------------------------------------------------
# POST /api/emails/<id>/approve  – Approve and send a pending email reply
# ---------------------------------------------------------------------------
@email_bp.route("/emails/<int:email_id>/approve", methods=["POST"])
def approve_email(email_id):
    token, operator = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT e.*, r.reply_text FROM emails e LEFT JOIN replies r ON r.email_id = e.id WHERE e.id = ?",
            (email_id,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Email not found"}), 404

        if row["status"] not in ("pending_review", "send_failed"):
            return jsonify({"error": f"Cannot approve email with status '{row['status']}'"}), 400


    # Allow overriding reply text
    reply_text = row["reply_text"]
    if request.is_json and request.json.get("reply_text"):
        reply_text = request.json["reply_text"]

    graph = GraphService(token)

    try:
        send_result = graph.send_reply(row["message_id"], reply_text)
        graph.mark_as_read(row["message_id"])
        sent_at = datetime.now(timezone.utc).isoformat()

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE emails
                SET status = 'approved',
                    retry_count = COALESCE(retry_count, 0) + ?,
                    last_error = NULL
                WHERE id = ?
                """,
                (send_result.get("attempts", 1), email_id),
            )
            conn.execute(
                "UPDATE replies SET reply_text = ?, sent_at = ? WHERE email_id = ?",
                (reply_text, sent_at, email_id),
            )
            conn.execute(
                "INSERT INTO audit_log (email_id, action, operator) VALUES (?, 'approved', ?)",
                (email_id, operator),
            )
            conn.commit()

        return jsonify({
            "success": True,
            "status": "approved",
            "sent_at": sent_at,
            "retry_attempts": send_result.get("attempts", 1),
        })
    except EmailSendError as e:
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE emails
                SET status = 'send_failed',
                    retry_count = COALESCE(retry_count, 0) + ?,
                    last_error = ?
                WHERE id = ?
                """,
                (e.attempts, e.last_error, email_id),
            )
            conn.execute(
                "INSERT INTO audit_log (email_id, action, operator) VALUES (?, 'send_failed', ?)",
                (email_id, operator),
            )
            conn.commit()

        return jsonify({
            "error": "邮件发送失败，系统已自动重试",
            "status": "send_failed",
            "retry_attempts": e.attempts,
            "detail": e.last_error,
        }), 502



# ---------------------------------------------------------------------------
# POST /api/emails/<id>/reject  – Reject a pending email
# ---------------------------------------------------------------------------
@email_bp.route("/emails/<int:email_id>/reject", methods=["POST"])
def reject_email(email_id):
    token, operator = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, status FROM emails WHERE id = ?", (email_id,)
        ).fetchone()

        if not row:
            return jsonify({"error": "Email not found"}), 404

        if row["status"] not in ("pending_review", "send_failed"):
            return jsonify({"error": f"Cannot reject email with status '{row['status']}'"}), 400


        conn.execute(
            "UPDATE emails SET status = 'rejected' WHERE id = ?", (email_id,)
        )
        conn.execute(
            "INSERT INTO audit_log (email_id, action, operator) VALUES (?, 'rejected', ?)",
            (email_id, operator),
        )
        conn.commit()

    return jsonify({"success": True, "status": "rejected"})


# ---------------------------------------------------------------------------
# GET /api/stats  – Aggregate statistics for the dashboard
# ---------------------------------------------------------------------------
@email_bp.route("/stats", methods=["GET"])
def get_stats():
    token, _ = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    with get_db_connection() as conn:
        total = conn.execute("SELECT COUNT(*) as cnt FROM emails").fetchone()["cnt"]

        status_rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM emails GROUP BY status"
        ).fetchall()

        category_rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM emails GROUP BY category"
        ).fetchall()

        avg_confidence = conn.execute(
            "SELECT AVG(confidence) as avg FROM emails"
        ).fetchone()["avg"] or 0.0

        # Last 7 days daily counts
        daily_rows = conn.execute(
            """
            SELECT date(created_at) as day,
                   SUM(CASE WHEN status IN ('auto_sent','approved') THEN 1 ELSE 0 END) as handled,
                   SUM(CASE WHEN status = 'pending_review' THEN 1 ELSE 0 END) as pending
            FROM emails
            WHERE created_at >= date('now', '-6 days')
            GROUP BY day
            ORDER BY day
            """
        ).fetchall()

    status_map = {r["status"]: r["cnt"] for r in status_rows}
    auto_sent = status_map.get("auto_sent", 0)
    approved = status_map.get("approved", 0)
    pending_review = status_map.get("pending_review", 0)
    rejected = status_map.get("rejected", 0)
    send_failed = status_map.get("send_failed", 0)
    ignored_no_reply = status_map.get("ignored_no_reply", 0)

    auto_rate = round((auto_sent + approved) / total * 100, 1) if total > 0 else 0.0
    non_business_rate = round(ignored_no_reply / total * 100, 1) if total > 0 else 0.0
    send_failure_rate = round(send_failed / total * 100, 1) if total > 0 else 0.0

    return jsonify({
        "total": total,
        "auto_sent": auto_sent,
        "approved": approved,
        "pending_review": pending_review,
        "rejected": rejected,
        "send_failed": send_failed,
        "ignored_no_reply": ignored_no_reply,
        "auto_rate": auto_rate,
        "non_business_rate": non_business_rate,
        "send_failure_rate": send_failure_rate,
        "avg_confidence": round(avg_confidence * 100, 1),

        "categories": [dict(r) for r in category_rows],
        "daily": [dict(r) for r in daily_rows],
    })


# ---------------------------------------------------------------------------
# DELETE /api/emails/<id>  – Soft delete a single email
# ---------------------------------------------------------------------------
@email_bp.route("/emails/<int:email_id>", methods=["DELETE"])
def delete_email(email_id):
    """Soft delete a single email."""
    token, operator = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, subject FROM emails WHERE id = ? AND is_deleted = 0",
            (email_id,)
        ).fetchone()

        if not row:
            return jsonify({"error": "Email not found or already deleted"}), 404

        # Soft delete
        conn.execute(
            """UPDATE emails
               SET is_deleted = 1, deleted_at = datetime('now'), deleted_by = ?
               WHERE id = ?""",
            (operator, email_id)
        )
        conn.execute(
            "INSERT INTO audit_log (email_id, action, operator) VALUES (?, 'deleted', ?)",
            (email_id, operator)
        )
        conn.commit()

    return jsonify({"success": True, "message": "Email deleted successfully"})


# ---------------------------------------------------------------------------
# POST /api/emails/bulk-delete  – Soft delete multiple emails
# ---------------------------------------------------------------------------
@email_bp.route("/emails/bulk-delete", methods=["POST"])
def bulk_delete_emails():
    """Soft delete multiple emails."""
    token, operator = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    if not request.is_json:
        return jsonify({"error": "JSON body required"}), 400

    email_ids = request.json.get("email_ids", [])
    if not email_ids or not isinstance(email_ids, list):
        return jsonify({"error": "email_ids array required"}), 400

    with get_db_connection() as conn:
        placeholders = ",".join("?" * len(email_ids))
        conn.execute(
            f"""UPDATE emails
                SET is_deleted = 1, deleted_at = datetime('now'), deleted_by = ?
                WHERE id IN ({placeholders}) AND is_deleted = 0""",
            [operator] + email_ids
        )

        # Log each deletion
        for email_id in email_ids:
            conn.execute(
                "INSERT INTO audit_log (email_id, action, operator) VALUES (?, 'bulk_deleted', ?)",
                (email_id, operator)
            )
        conn.commit()

    return jsonify({"success": True, "deleted_count": len(email_ids)})


# ---------------------------------------------------------------------------
# POST /api/emails/bulk-approve  – Approve and send multiple emails
# ---------------------------------------------------------------------------
@email_bp.route("/emails/bulk-approve", methods=["POST"])
def bulk_approve_emails():
    """Approve and send multiple emails."""
    token, operator = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    email_ids = request.json.get("email_ids", [])
    if not email_ids:
        return jsonify({"error": "email_ids required"}), 400

    graph = GraphService(token)
    success_count = 0
    failed_ids = []

    with get_db_connection() as conn:
        for email_id in email_ids:
            row = conn.execute(
                """SELECT e.*, r.reply_text
                   FROM emails e
                   LEFT JOIN replies r ON r.email_id = e.id
                   WHERE e.id = ? AND e.status IN ('pending_review', 'send_failed')
                   AND e.is_deleted = 0""",
                (email_id,)
            ).fetchone()

            if not row:
                failed_ids.append(email_id)
                continue

            try:
                graph.send_reply(row["message_id"], row["reply_text"])
                graph.mark_as_read(row["message_id"])
                sent_at = datetime.now(timezone.utc).isoformat()

                conn.execute(
                    "UPDATE emails SET status = 'approved' WHERE id = ?",
                    (email_id,)
                )
                conn.execute(
                    "UPDATE replies SET sent_at = ? WHERE email_id = ?",
                    (sent_at, email_id)
                )
                conn.execute(
                    "INSERT INTO audit_log (email_id, action, operator) VALUES (?, 'bulk_approved', ?)",
                    (email_id, operator)
                )
                success_count += 1
            except Exception as e:
                failed_ids.append(email_id)
                conn.execute(
                    "UPDATE emails SET status = 'send_failed', last_error = ? WHERE id = ?",
                    (str(e), email_id)
                )

        conn.commit()

    return jsonify({
        "success": True,
        "approved_count": success_count,
        "failed_count": len(failed_ids),
        "failed_ids": failed_ids
    })


# ---------------------------------------------------------------------------
# POST /api/emails/bulk-reject  – Reject multiple emails
# ---------------------------------------------------------------------------
@email_bp.route("/emails/bulk-reject", methods=["POST"])
def bulk_reject_emails():
    """Reject multiple emails."""
    token, operator = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    email_ids = request.json.get("email_ids", [])
    if not email_ids:
        return jsonify({"error": "email_ids required"}), 400

    with get_db_connection() as conn:
        placeholders = ",".join("?" * len(email_ids))
        conn.execute(
            f"""UPDATE emails
                SET status = 'rejected'
                WHERE id IN ({placeholders})
                AND status IN ('pending_review', 'send_failed')
                AND is_deleted = 0""",
            email_ids
        )

        for email_id in email_ids:
            conn.execute(
                "INSERT INTO audit_log (email_id, action, operator) VALUES (?, 'bulk_rejected', ?)",
                (email_id, operator)
            )
        conn.commit()

    return jsonify({"success": True, "rejected_count": len(email_ids)})


# ---------------------------------------------------------------------------
# GET /api/emails/export  – Export emails as CSV
# ---------------------------------------------------------------------------
@email_bp.route("/emails/export", methods=["GET"])
def export_emails():
    """Export emails as CSV."""
    token, _ = _require_auth()
    if not token:
        return jsonify({"error": "Not authenticated"}), 401

    # Use same filters as list_emails
    status_filter = request.args.get("status")
    category_filter = request.args.get("category")
    search = request.args.get("search", "").strip()

    where_clauses = ["e.is_deleted = 0"]
    params = []

    if status_filter:
        where_clauses.append("e.status = ?")
        params.append(status_filter)
    if category_filter:
        where_clauses.append("e.category = ?")
        params.append(category_filter)
    if search:
        where_clauses.append("(e.subject LIKE ? OR e.sender LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]

    where_sql = "WHERE " + " AND ".join(where_clauses)

    with get_db_connection() as conn:
        rows = conn.execute(
            f"""SELECT e.id, e.subject, e.sender, e.received_at, e.category,
                       e.confidence, e.status, e.created_at, r.reply_text, r.sent_at
                FROM emails e
                LEFT JOIN replies r ON r.email_id = e.id
                {where_sql}
                ORDER BY e.created_at DESC
                LIMIT 10000""",
            params
        ).fetchall()

    # Generate CSV
    import csv
    from io import StringIO

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Subject', 'Sender', 'Received At', 'Category',
                     'Confidence', 'Status', 'Reply Text', 'Sent At', 'Created At'])

    for row in rows:
        writer.writerow([
            row['id'], row['subject'], row['sender'], row['received_at'],
            row['category'], row['confidence'], row['status'],
            row['reply_text'], row['sent_at'], row['created_at']
        ])

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=emails_export.csv'}
    )

