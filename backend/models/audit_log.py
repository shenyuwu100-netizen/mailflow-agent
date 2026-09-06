"""
Audit Log Model

Provides immutable, append-only audit logging for compliance.
Tracks all system actions on emails for accountability and traceability.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from models.database import get_db_connection
from utils.logger import get_logger


logger = get_logger('audit_log')


class AuditAction:
    """Standard audit action types."""
    EMAIL_RECEIVED = "email_received"
    CLASSIFICATION = "classification"
    REPLY_GENERATED = "reply_generated"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"
    AUTO_SENT = "auto_sent"
    MANUAL_REVIEW = "manual_review"
    MANUAL_SENT = "manual_sent"
    CONSENT_GIVEN = "consent_given"
    CONSENT_WITHDRAWN = "consent_withdrawn"
    PII_DETECTED = "pii_detected"
    PII_REDACTED = "pii_redacted"
    DATA_DELETED = "data_deleted"
    DATA_ANONYMIZED = "data_anonymized"
    DATA_EXPORTED = "data_exported"


def init_audit_tables():
    """Create audit log tables if they don't exist."""
    with get_db_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS compliance_audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id    INTEGER,
                action      TEXT NOT NULL,
                operator    TEXT DEFAULT 'system',
                details     TEXT,
                ip_address  TEXT,
                user_agent  TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (email_id) REFERENCES emails(id)
            );

            CREATE INDEX IF NOT EXISTS idx_audit_email_id
                ON compliance_audit_log(email_id);
            CREATE INDEX IF NOT EXISTS idx_audit_action
                ON compliance_audit_log(action);
            CREATE INDEX IF NOT EXISTS idx_audit_created_at
                ON compliance_audit_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_operator
                ON compliance_audit_log(operator);
        """)
        conn.commit()


def log_audit_event(
    action: str,
    email_id: Optional[int] = None,
    operator: str = "system",
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
):
    """
    Log an audit event (append-only).

    Args:
        action: Action type (use AuditAction constants)
        email_id: Related email ID (optional)
        operator: Who performed the action
        details: Additional details (stored as JSON)
        ip_address: Client IP address
        user_agent: Client user agent
    """
    import json

    details_json = json.dumps(details, ensure_ascii=False) if details else None

    try:
        with get_db_connection() as conn:
            conn.execute(
                """INSERT INTO compliance_audit_log
                   (email_id, action, operator, details, ip_address, user_agent)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (email_id, action, operator, details_json, ip_address, user_agent)
            )
            conn.commit()

        logger.info("Audit event logged", {
            'action': action,
            'email_id': email_id,
            'operator': operator
        })

    except Exception as e:
        logger.error("Failed to log audit event", {
            'action': action,
            'email_id': email_id,
            'error': str(e)
        }, exc_info=True)


def get_audit_trail(
    email_id: Optional[int] = None,
    action: Optional[str] = None,
    operator: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Query audit trail with filters.

    Args:
        email_id: Filter by email ID
        action: Filter by action type
        operator: Filter by operator
        start_date: Filter by start date (ISO format)
        end_date: Filter by end date (ISO format)
        limit: Maximum results
        offset: Result offset

    Returns:
        List of audit log entries
    """
    conditions = []
    params = []

    if email_id is not None:
        conditions.append("email_id = ?")
        params.append(email_id)

    if action is not None:
        conditions.append("action = ?")
        params.append(action)

    if operator is not None:
        conditions.append("operator = ?")
        params.append(operator)

    if start_date is not None:
        conditions.append("created_at >= ?")
        params.append(start_date)

    if end_date is not None:
        conditions.append("created_at <= ?")
        params.append(end_date)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    query = f"""
        SELECT id, email_id, action, operator, details, ip_address, user_agent, created_at
        FROM compliance_audit_log
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    try:
        with get_db_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error("Failed to query audit trail", {
            'error': str(e)
        }, exc_info=True)
        return []


def get_audit_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get audit log summary statistics.

    Args:
        start_date: Filter by start date
        end_date: Filter by end date

    Returns:
        Summary statistics
    """
    conditions = []
    params = []

    if start_date:
        conditions.append("created_at >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("created_at <= ?")
        params.append(end_date)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    try:
        with get_db_connection() as conn:
            # Total events
            total = conn.execute(
                f"SELECT COUNT(*) as count FROM compliance_audit_log WHERE {where_clause}",
                params
            ).fetchone()['count']

            # Events by action
            action_counts = conn.execute(
                f"""SELECT action, COUNT(*) as count
                    FROM compliance_audit_log
                    WHERE {where_clause}
                    GROUP BY action
                    ORDER BY count DESC""",
                params
            ).fetchall()

            # Events by operator
            operator_counts = conn.execute(
                f"""SELECT operator, COUNT(*) as count
                    FROM compliance_audit_log
                    WHERE {where_clause}
                    GROUP BY operator
                    ORDER BY count DESC""",
                params
            ).fetchall()

            return {
                'total_events': total,
                'events_by_action': {row['action']: row['count'] for row in action_counts},
                'events_by_operator': {row['operator']: row['count'] for row in operator_counts}
            }

    except Exception as e:
        logger.error("Failed to get audit summary", {
            'error': str(e)
        }, exc_info=True)
        return {'total_events': 0, 'events_by_action': {}, 'events_by_operator': {}}
