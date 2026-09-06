"""
Database initialization and connection management.
Uses Python's built-in sqlite3 module.
"""

import sqlite3
from pathlib import Path
import contextlib
from config import Config


def _ensure_column(conn, table_name: str, column_name: str, column_def: str):
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def init_db():

    """Create all tables if they don't exist (idempotent)."""
    with get_db_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS emails (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id  TEXT UNIQUE NOT NULL,
                subject     TEXT,
                sender      TEXT,
                received_at TEXT,
                body        TEXT,
                category    TEXT,
                confidence  REAL,
                reasoning   TEXT,
                status      TEXT DEFAULT 'pending_review',
                retry_count INTEGER DEFAULT 0,
                last_error  TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS replies (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id    INTEGER REFERENCES emails(id),
                reply_text  TEXT,
                sent_at     TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id    INTEGER REFERENCES emails(id),
                action      TEXT,
                operator    TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number    TEXT UNIQUE NOT NULL,
                customer_email  TEXT NOT NULL,
                product_name    TEXT,
                quantity        INTEGER,
                total_amount    REAL,
                currency        TEXT DEFAULT 'CNY',
                order_status    TEXT DEFAULT 'pending',
                shipping_status TEXT DEFAULT 'not_shipped',
                tracking_number TEXT,
                destination     TEXT,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_orders_number ON orders(order_number);
            CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(customer_email);

            CREATE TABLE IF NOT EXISTS logistics_routes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                origin          TEXT NOT NULL,
                destination     TEXT NOT NULL,
                shipping_method TEXT NOT NULL,
                container_type  TEXT,
                weight_range    TEXT,
                price           REAL NOT NULL,
                currency        TEXT DEFAULT 'USD',
                transit_days    INTEGER,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_logistics_routes_lookup
                ON logistics_routes(origin, destination, shipping_method);
        """)
        _ensure_column(conn, "emails", "retry_count", "INTEGER DEFAULT 0")
        _ensure_column(conn, "emails", "last_error", "TEXT")

        # Phase 2: Rubric-based scoring columns
        _ensure_column(conn, "emails", "classification_rubric_scores", "TEXT")  # JSON
        _ensure_column(conn, "emails", "auto_send_rubric_scores", "TEXT")      # JSON
        _ensure_column(conn, "emails", "rubric_version", "TEXT DEFAULT 'v1.0'")

        # Phase 3: Reply quality validation columns
        _ensure_column(conn, "replies", "reply_validation_scores", "TEXT")     # JSON
        _ensure_column(conn, "replies", "validation_passed", "INTEGER DEFAULT 1")  # Boolean
        _ensure_column(conn, "replies", "validation_issues", "TEXT")           # JSON

        # Phase 5: Privacy & Compliance columns
        _ensure_column(conn, "emails", "consent_status", "TEXT DEFAULT 'unknown'")  # GIVEN, WITHDRAWN, UNKNOWN
        _ensure_column(conn, "emails", "pii_detected", "INTEGER DEFAULT 0")         # Boolean
        _ensure_column(conn, "emails", "pii_types", "TEXT")                         # JSON list of detected PII types

        # Phase 6: Soft delete columns
        _ensure_column(conn, "emails", "is_deleted", "INTEGER DEFAULT 0")
        _ensure_column(conn, "emails", "deleted_at", "TEXT")
        _ensure_column(conn, "emails", "deleted_by", "TEXT")

        # Create index for query performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_deleted ON emails(is_deleted)")

        # Language detection columns
        _ensure_column(conn, "emails", "language", "TEXT DEFAULT 'unknown'")
        _ensure_column(conn, "emails", "language_confidence", "REAL DEFAULT 0.0")

        conn.commit()



@contextlib.contextmanager
def get_db_connection():
    """Context manager that yields a sqlite3 connection and closes it on exit."""
    Path(Config.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(Config.DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
