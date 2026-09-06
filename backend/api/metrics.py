"""
Metrics API

Provides system performance metrics endpoints for monitoring dashboards.
Returns classification accuracy, auto-send rate, error rate, and latency data.
"""

import json
from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
from typing import Dict, Any
from models.database import get_db_connection
from utils.logger import get_logger


logger = get_logger('metrics_api')

metrics_bp = Blueprint('metrics', __name__)


def _get_date_range():
    """Extract date range from query params, default to last 30 days."""
    end = request.args.get('end_date', datetime.utcnow().isoformat())
    start = request.args.get('start_date',
        (datetime.utcnow() - timedelta(days=30)).isoformat())
    return start, end


@metrics_bp.route('/metrics', methods=['GET'])
def get_metrics_summary():
    """Main metrics endpoint returning all key metrics."""
    start, end = _get_date_range()

    with get_db_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as c FROM emails WHERE created_at BETWEEN ? AND ?",
            (start, end)
        ).fetchone()['c']

        statuses = conn.execute(
            """SELECT status, COUNT(*) as c FROM emails
               WHERE created_at BETWEEN ? AND ?
               GROUP BY status""",
            (start, end)
        ).fetchall()

        avg_conf = conn.execute(
            "SELECT AVG(confidence) as avg FROM emails WHERE created_at BETWEEN ? AND ? AND confidence IS NOT NULL",
            (start, end)
        ).fetchone()['avg']

        categories = conn.execute(
            """SELECT category, COUNT(*) as c, AVG(confidence) as avg_conf
               FROM emails WHERE created_at BETWEEN ? AND ?
               GROUP BY category ORDER BY c DESC""",
            (start, end)
        ).fetchall()

    status_dict = {row['status']: row['c'] for row in statuses}
    auto_sent = status_dict.get('auto_sent', 0)

    return jsonify({
        'period': {'start': start, 'end': end},
        'total_emails': total,
        'auto_send_rate': round(auto_sent / total * 100, 1) if total else 0,
        'average_confidence': round(avg_conf, 3) if avg_conf else 0,
        'status_distribution': status_dict,
        'category_breakdown': [{
            'category': row['category'],
            'count': row['c'],
            'avg_confidence': round(row['avg_conf'], 3) if row['avg_conf'] else 0
        } for row in categories],
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }), 200


@metrics_bp.route('/metrics/timeseries', methods=['GET'])
def get_timeseries():
    """Time-series data for trend analysis (daily aggregates)."""
    start, end = _get_date_range()

    with get_db_connection() as conn:
        rows = conn.execute(
            """SELECT DATE(created_at) as day,
                      COUNT(*) as total,
                      SUM(CASE WHEN status = 'auto_sent' THEN 1 ELSE 0 END) as auto_sent,
                      SUM(CASE WHEN status = 'pending_review' THEN 1 ELSE 0 END) as pending,
                      AVG(confidence) as avg_confidence
               FROM emails
               WHERE created_at BETWEEN ? AND ?
               GROUP BY DATE(created_at)
               ORDER BY day""",
            (start, end)
        ).fetchall()

    return jsonify({
        'period': {'start': start, 'end': end},
        'daily': [{
            'date': row['day'],
            'total': row['total'],
            'auto_sent': row['auto_sent'],
            'pending_review': row['pending'],
            'auto_send_rate': round(row['auto_sent'] / row['total'] * 100, 1) if row['total'] else 0,
            'avg_confidence': round(row['avg_confidence'], 3) if row['avg_confidence'] else 0
        } for row in rows],
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }), 200


@metrics_bp.route('/metrics/quality', methods=['GET'])
def get_quality_metrics():
    """Reply quality validation metrics."""
    start, end = _get_date_range()

    with get_db_connection() as conn:
        total_replies = conn.execute(
            "SELECT COUNT(*) as c FROM replies r JOIN emails e ON r.email_id = e.id WHERE e.created_at BETWEEN ? AND ?",
            (start, end)
        ).fetchone()['c']

        validated = conn.execute(
            """SELECT COUNT(*) as c FROM replies r JOIN emails e ON r.email_id = e.id
               WHERE e.created_at BETWEEN ? AND ? AND r.reply_validation_scores IS NOT NULL""",
            (start, end)
        ).fetchone()['c']

        passed = conn.execute(
            """SELECT COUNT(*) as c FROM replies r JOIN emails e ON r.email_id = e.id
               WHERE e.created_at BETWEEN ? AND ? AND r.validation_passed = 1""",
            (start, end)
        ).fetchone()['c']

    return jsonify({
        'period': {'start': start, 'end': end},
        'total_replies': total_replies,
        'validated': validated,
        'passed': passed,
        'pass_rate': round(passed / total_replies * 100, 1) if total_replies else 0,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }), 200


@metrics_bp.route('/metrics/pii', methods=['GET'])
def get_pii_metrics():
    """PII detection metrics."""
    start, end = _get_date_range()

    with get_db_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as c FROM emails WHERE created_at BETWEEN ? AND ?",
            (start, end)
        ).fetchone()['c']

        pii_count = conn.execute(
            "SELECT COUNT(*) as c FROM emails WHERE created_at BETWEEN ? AND ? AND pii_detected = 1",
            (start, end)
        ).fetchone()['c']

    return jsonify({
        'period': {'start': start, 'end': end},
        'total_emails': total,
        'emails_with_pii': pii_count,
        'pii_rate': round(pii_count / total * 100, 1) if total else 0,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }), 200
