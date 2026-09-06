"""
A/B Testing Framework

Supports experiment tracking, variant assignment, metrics collection,
and statistical significance testing for system configuration changes.
"""

import json
import hashlib
import math
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
from models.database import get_db_connection
from utils.logger import get_logger


logger = get_logger('ab_testing')


class ExperimentStatus(Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


class ABTestingFramework:
    """Manages A/B testing experiments for system configuration."""

    def __init__(self):
        self._init_tables()

    def _init_tables(self):
        with get_db_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS experiments (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT UNIQUE NOT NULL,
                    description TEXT,
                    status      TEXT DEFAULT 'draft',
                    variants    TEXT NOT NULL,
                    traffic_split TEXT NOT NULL,
                    metrics     TEXT,
                    created_at  TEXT DEFAULT (datetime('now')),
                    updated_at  TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS experiment_assignments (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id   INTEGER REFERENCES experiments(id),
                    entity_id       TEXT NOT NULL,
                    variant         TEXT NOT NULL,
                    created_at      TEXT DEFAULT (datetime('now')),
                    UNIQUE(experiment_id, entity_id)
                );

                CREATE TABLE IF NOT EXISTS experiment_events (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id   INTEGER REFERENCES experiments(id),
                    entity_id       TEXT NOT NULL,
                    variant         TEXT NOT NULL,
                    metric_name     TEXT NOT NULL,
                    metric_value    REAL NOT NULL,
                    metadata        TEXT,
                    created_at      TEXT DEFAULT (datetime('now'))
                );
            """)
            conn.commit()

    def create_experiment(
        self,
        name: str,
        description: str,
        variants: List[str],
        traffic_split: Optional[List[float]] = None,
        metrics: Optional[List[str]] = None
    ) -> int:
        """
        Create a new experiment.

        Args:
            name: Unique experiment name
            description: What the experiment tests
            variants: List of variant names (e.g., ["control", "treatment"])
            traffic_split: Traffic allocation per variant (must sum to 1.0)
            metrics: Metric names to track

        Returns:
            Experiment ID
        """
        if traffic_split is None:
            traffic_split = [1.0 / len(variants)] * len(variants)

        if len(variants) != len(traffic_split):
            raise ValueError("variants and traffic_split must have same length")
        if abs(sum(traffic_split) - 1.0) > 0.01:
            raise ValueError("traffic_split must sum to 1.0")

        with get_db_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO experiments (name, description, variants, traffic_split, metrics)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, description, json.dumps(variants), json.dumps(traffic_split),
                 json.dumps(metrics or []))
            )
            conn.commit()
            exp_id = cursor.lastrowid

        logger.info("Experiment created", {'id': exp_id, 'name': name, 'variants': variants})
        return exp_id

    def start_experiment(self, experiment_id: int):
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE experiments SET status = 'running', updated_at = datetime('now') WHERE id = ?",
                (experiment_id,)
            )
            conn.commit()

    def stop_experiment(self, experiment_id: int):
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE experiments SET status = 'completed', updated_at = datetime('now') WHERE id = ?",
                (experiment_id,)
            )
            conn.commit()

    def assign_variant(self, experiment_id: int, entity_id: str) -> str:
        """
        Deterministically assign an entity to a variant.
        Uses consistent hashing so the same entity always gets the same variant.
        """
        with get_db_connection() as conn:
            existing = conn.execute(
                "SELECT variant FROM experiment_assignments WHERE experiment_id = ? AND entity_id = ?",
                (experiment_id, entity_id)
            ).fetchone()
            if existing:
                return existing['variant']

            exp = conn.execute(
                "SELECT variants, traffic_split, status FROM experiments WHERE id = ?",
                (experiment_id,)
            ).fetchone()

            if not exp or exp['status'] != 'running':
                variants = json.loads(exp['variants']) if exp else ['control']
                return variants[0]

            variants = json.loads(exp['variants'])
            splits = json.loads(exp['traffic_split'])

            hash_input = f"{experiment_id}:{entity_id}"
            hash_val = int(hashlib.md5(hash_input.encode()).hexdigest(), 16) % 10000
            bucket = hash_val / 10000.0

            cumulative = 0.0
            assigned = variants[0]
            for variant, split in zip(variants, splits):
                cumulative += split
                if bucket < cumulative:
                    assigned = variant
                    break

            conn.execute(
                "INSERT OR IGNORE INTO experiment_assignments (experiment_id, entity_id, variant) VALUES (?, ?, ?)",
                (experiment_id, entity_id, assigned)
            )
            conn.commit()

        return assigned

    def record_metric(
        self,
        experiment_id: int,
        entity_id: str,
        metric_name: str,
        metric_value: float,
        metadata: Optional[Dict] = None
    ):
        """Record a metric event for an experiment."""
        variant = self.assign_variant(experiment_id, entity_id)

        with get_db_connection() as conn:
            conn.execute(
                """INSERT INTO experiment_events
                   (experiment_id, entity_id, variant, metric_name, metric_value, metadata)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (experiment_id, entity_id, variant, metric_name, metric_value,
                 json.dumps(metadata) if metadata else None)
            )
            conn.commit()

    def get_experiment_results(self, experiment_id: int) -> Dict[str, Any]:
        """Get aggregated results for an experiment."""
        with get_db_connection() as conn:
            exp = conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
            if not exp:
                return {}

            variants = json.loads(exp['variants'])
            results = {'experiment': dict(exp), 'variants': {}}

            for variant in variants:
                assignment_count = conn.execute(
                    "SELECT COUNT(*) as c FROM experiment_assignments WHERE experiment_id = ? AND variant = ?",
                    (experiment_id, variant)
                ).fetchone()['c']

                metrics = conn.execute(
                    """SELECT metric_name, COUNT(*) as count,
                              AVG(metric_value) as mean,
                              MIN(metric_value) as min_val,
                              MAX(metric_value) as max_val
                       FROM experiment_events
                       WHERE experiment_id = ? AND variant = ?
                       GROUP BY metric_name""",
                    (experiment_id, variant)
                ).fetchall()

                results['variants'][variant] = {
                    'assignments': assignment_count,
                    'metrics': {row['metric_name']: {
                        'count': row['count'],
                        'mean': round(row['mean'], 4),
                        'min': round(row['min_val'], 4),
                        'max': round(row['max_val'], 4)
                    } for row in metrics}
                }

        return results

    def calculate_significance(
        self,
        experiment_id: int,
        metric_name: str
    ) -> Dict[str, Any]:
        """
        Calculate statistical significance between control and treatment using
        Welch's t-test (unequal variance).
        """
        with get_db_connection() as conn:
            exp = conn.execute("SELECT variants FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
            if not exp:
                return {'error': 'Experiment not found'}

            variants = json.loads(exp['variants'])
            if len(variants) < 2:
                return {'error': 'Need at least 2 variants'}

            variant_data = {}
            for variant in variants:
                rows = conn.execute(
                    "SELECT metric_value FROM experiment_events WHERE experiment_id = ? AND variant = ? AND metric_name = ?",
                    (experiment_id, variant, metric_name)
                ).fetchall()
                variant_data[variant] = [row['metric_value'] for row in rows]

        control = variant_data.get(variants[0], [])
        treatment = variant_data.get(variants[1], [])

        if len(control) < 2 or len(treatment) < 2:
            return {
                'significant': False,
                'reason': 'Insufficient data',
                'control_n': len(control),
                'treatment_n': len(treatment)
            }

        n1, n2 = len(control), len(treatment)
        mean1 = sum(control) / n1
        mean2 = sum(treatment) / n2
        var1 = sum((x - mean1) ** 2 for x in control) / (n1 - 1)
        var2 = sum((x - mean2) ** 2 for x in treatment) / (n2 - 1)

        se = math.sqrt(var1 / n1 + var2 / n2) if (var1 / n1 + var2 / n2) > 0 else 0.0001
        t_stat = (mean2 - mean1) / se

        # Approximate p-value using normal distribution for large samples
        p_value = 2 * (1 - _normal_cdf(abs(t_stat)))

        return {
            'metric': metric_name,
            'control': {'name': variants[0], 'n': n1, 'mean': round(mean1, 4)},
            'treatment': {'name': variants[1], 'n': n2, 'mean': round(mean2, 4)},
            'difference': round(mean2 - mean1, 4),
            'relative_change': round((mean2 - mean1) / mean1 * 100, 2) if mean1 != 0 else None,
            't_statistic': round(t_stat, 4),
            'p_value': round(p_value, 4),
            'significant': p_value < 0.05
        }

    def list_experiments(self, status: Optional[str] = None) -> List[Dict]:
        with get_db_connection() as conn:
            if status:
                rows = conn.execute("SELECT * FROM experiments WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM experiments ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def _normal_cdf(x: float) -> float:
    """Approximate standard normal CDF using Abramowitz & Stegun formula."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return 0.5 * (1.0 + sign * y)


# Singleton
_framework = None


def get_ab_framework() -> ABTestingFramework:
    global _framework
    if _framework is None:
        _framework = ABTestingFramework()
    return _framework
