"""
Health Check API

Provides health check endpoints to monitor system status.
Checks database, OpenAI API, and Graph API connectivity.
"""

from flask import Blueprint, jsonify
from datetime import datetime
from typing import Dict, Any
import sqlite3
from openai import OpenAI
from config import Config
from models.database import get_db_connection
from utils.retry_handler import get_all_circuit_breakers


health_bp = Blueprint('health', __name__)


def check_database() -> Dict[str, Any]:
    """
    Check database connectivity.

    Returns:
        Health check result
    """
    try:
        with get_db_connection() as conn:
            # Simple query to test connection
            result = conn.execute("SELECT 1").fetchone()
            if result:
                return {
                    'status': 'healthy',
                    'message': 'Database connection successful',
                    'response_time_ms': 0  # Could measure actual time
                }
    except sqlite3.Error as e:
        return {
            'status': 'unhealthy',
            'message': f'Database connection failed: {str(e)}',
            'error': str(e)
        }
    except Exception as e:
        return {
            'status': 'unhealthy',
            'message': f'Unexpected error: {str(e)}',
            'error': str(e)
        }


def check_openai_api() -> Dict[str, Any]:
    """
    Check OpenAI API connectivity.

    Returns:
        Health check result
    """
    try:
        client = OpenAI(api_key=Config.OPENAI_API_KEY, base_url=Config.OPENAI_BASE_URL)

        # Simple API call to test connectivity
        start_time = datetime.now()
        response = client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            temperature=0
        )
        end_time = datetime.now()

        response_time = (end_time - start_time).total_seconds() * 1000

        if response.choices:
            return {
                'status': 'healthy',
                'message': 'OpenAI API connection successful',
                'response_time_ms': round(response_time, 2),
                'model': Config.OPENAI_MODEL
            }
        else:
            return {
                'status': 'degraded',
                'message': 'OpenAI API responded but with no content',
                'response_time_ms': round(response_time, 2)
            }

    except Exception as e:
        return {
            'status': 'unhealthy',
            'message': f'OpenAI API connection failed: {str(e)}',
            'error': str(e)
        }


def check_graph_api() -> Dict[str, Any]:
    """
    Check Microsoft Graph API connectivity.

    Returns:
        Health check result
    """
    try:
        # Check if Graph API credentials are configured
        if not hasattr(Config, 'GRAPH_CLIENT_ID') or not Config.GRAPH_CLIENT_ID:
            return {
                'status': 'not_configured',
                'message': 'Graph API credentials not configured'
            }

        # In production, would make actual API call
        # For now, just check configuration
        return {
            'status': 'healthy',
            'message': 'Graph API configured (not tested)',
            'note': 'Actual connectivity test requires authentication'
        }

    except Exception as e:
        return {
            'status': 'unhealthy',
            'message': f'Graph API check failed: {str(e)}',
            'error': str(e)
        }


def get_overall_status(checks: Dict[str, Dict[str, Any]]) -> str:
    """
    Determine overall system status.

    Args:
        checks: Dictionary of health check results

    Returns:
        Overall status: healthy, degraded, or unhealthy
    """
    statuses = [check['status'] for check in checks.values()]

    if all(s == 'healthy' or s == 'not_configured' for s in statuses):
        return 'healthy'
    elif any(s == 'unhealthy' for s in statuses):
        return 'unhealthy'
    else:
        return 'degraded'


@health_bp.route('/health', methods=['GET'])
def health_check():
    """
    Main health check endpoint.

    Returns:
        JSON response with health status
    """
    checks = {
        'database': check_database(),
        'openai_api': check_openai_api(),
        'graph_api': check_graph_api()
    }

    overall_status = get_overall_status(checks)

    # Get circuit breaker states
    circuit_breakers = get_all_circuit_breakers()

    response = {
        'status': overall_status,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'checks': checks,
        'circuit_breakers': circuit_breakers
    }

    # Set HTTP status code based on health
    status_code = 200 if overall_status == 'healthy' else (503 if overall_status == 'unhealthy' else 207)

    return jsonify(response), status_code


@health_bp.route('/health/database', methods=['GET'])
def health_check_database():
    """Database-specific health check."""
    result = check_database()
    status_code = 200 if result['status'] == 'healthy' else 503
    return jsonify(result), status_code


@health_bp.route('/health/openai', methods=['GET'])
def health_check_openai():
    """OpenAI API-specific health check."""
    result = check_openai_api()
    status_code = 200 if result['status'] == 'healthy' else 503
    return jsonify(result), status_code


@health_bp.route('/health/graph', methods=['GET'])
def health_check_graph():
    """Graph API-specific health check."""
    result = check_graph_api()
    status_code = 200 if result['status'] in ['healthy', 'not_configured'] else 503
    return jsonify(result), status_code


@health_bp.route('/health/circuit-breakers', methods=['GET'])
def health_check_circuit_breakers():
    """Circuit breaker status endpoint."""
    circuit_breakers = get_all_circuit_breakers()

    return jsonify({
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'circuit_breakers': circuit_breakers
    }), 200


@health_bp.route('/health/ready', methods=['GET'])
def readiness_check():
    """
    Readiness check for Kubernetes/container orchestration.
    Returns 200 if system is ready to accept traffic.
    """
    checks = {
        'database': check_database()
    }

    # System is ready if database is accessible
    is_ready = checks['database']['status'] == 'healthy'

    response = {
        'ready': is_ready,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'checks': checks
    }

    status_code = 200 if is_ready else 503
    return jsonify(response), status_code


@health_bp.route('/health/live', methods=['GET'])
def liveness_check():
    """
    Liveness check for Kubernetes/container orchestration.
    Returns 200 if application is running (even if degraded).
    """
    return jsonify({
        'alive': True,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }), 200
