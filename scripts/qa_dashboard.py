#!/usr/bin/env python3
"""
QA Dashboard — SQLite-based QA monitoring for Report Base.

Records every QA validation result to a local SQLite database,
enabling trend analysis, alerting, and gap detection without
relying on Notion queries.

Usage:
    from qa_dashboard import QADashboard

    db = QADashboard()

    # Record a validation result
    db.record(
        page_id="abc-123",
        title="OKX — $200M STRATEGIC",
        page_type="FUNDRAISING INTEL",
        qa_status="PASS",
        issues="",
        pipeline="funding-intel-brief",
        run_id="run-456",
    )

    # Query dashboard
    db.summary(hours=24)       # Status counts for last 24h
    db.failures(hours=24)      # All FAIL pages in last 24h
    db.stale_warns(hours=48)   # Pages stuck in WARN for >48h
    db.error_trends(days=7)    # Error pattern frequency over 7 days
    db.circuit_breaker_status() # Current state of external API breakers

CLI:
    python3 qa_dashboard.py --summary 24
    python3 qa_dashboard.py --failures 24
    python3 qa_dashboard.py --stale-warns 48
    python3 qa_dashboard.py --trends 7

Deployment:
    Place at: /home/ubuntu/clawd/scripts/qa_dashboard.py
    Database: /home/ubuntu/clawd/data/qa_dashboard.db (auto-created)
"""

import argparse
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("qa_dashboard")

DB_PATH = os.path.join(
    os.getenv("CLAWD_DATA_DIR", os.path.expanduser("/home/ubuntu/clawd/data")),
    "qa_dashboard.db",
)


class QADashboard:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qa_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    page_id TEXT NOT NULL,
                    title TEXT,
                    page_type TEXT,
                    qa_status TEXT NOT NULL,
                    issues TEXT,
                    pipeline TEXT,
                    run_id TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_qa_timestamp
                ON qa_results(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_qa_page_id
                ON qa_results(page_id)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    service TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    latency_ms REAL
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(
        self,
        page_id: str,
        qa_status: str,
        title: str = "",
        page_type: str = "",
        issues: str = "",
        pipeline: str = "",
        run_id: str = "",
    ):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO qa_results (timestamp, page_id, title, page_type, qa_status, issues, pipeline, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), page_id, title, page_type, qa_status, issues, pipeline, run_id),
            )

    def record_api_health(
        self,
        service: str,
        status: str,
        error: str = "",
        latency_ms: float = 0,
    ):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO api_health (timestamp, service, status, error, latency_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), service, status, error, latency_ms),
            )

    def summary(self, hours: int = 24) -> dict:
        """QA status counts for the last N hours."""
        cutoff = time.time() - (hours * 3600)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT qa_status, COUNT(*) as cnt FROM qa_results "
                "WHERE timestamp > ? GROUP BY qa_status",
                (cutoff,),
            ).fetchall()
        return {row["qa_status"]: row["cnt"] for row in rows}

    def failures(self, hours: int = 24) -> list[dict]:
        """All FAIL pages in the last N hours."""
        cutoff = time.time() - (hours * 3600)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM qa_results WHERE qa_status = 'FAIL' AND timestamp > ? "
                "ORDER BY timestamp DESC",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def stale_warns(self, hours: int = 48) -> list[dict]:
        """
        Pages whose most recent QA status is WARN and hasn't been
        re-validated in the given time window.
        """
        cutoff = time.time() - (hours * 3600)
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT page_id, title, qa_status, MAX(timestamp) as last_check, issues
                FROM qa_results
                GROUP BY page_id
                HAVING qa_status = 'WARN' AND last_check < ?
                ORDER BY last_check ASC
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def error_trends(self, days: int = 7) -> list[dict]:
        """Error pattern frequency over the last N days."""
        cutoff = time.time() - (days * 86400)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT issues, COUNT(*) as cnt FROM qa_results "
                "WHERE qa_status = 'FAIL' AND timestamp > ? "
                "GROUP BY issues ORDER BY cnt DESC LIMIT 20",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def api_health_summary(self, hours: int = 24) -> dict:
        """API health summary for the last N hours."""
        cutoff = time.time() - (hours * 3600)
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT service,
                       COUNT(*) as total,
                       SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as ok,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
                       AVG(latency_ms) as avg_latency_ms
                FROM api_health WHERE timestamp > ?
                GROUP BY service
            """, (cutoff,)).fetchall()
        return {row["service"]: dict(row) for row in rows}

    def page_history(self, page_id: str) -> list[dict]:
        """Full QA history for a specific page."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM qa_results WHERE page_id = ? ORDER BY timestamp DESC",
                (page_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def main():
    parser = argparse.ArgumentParser(description="QA Dashboard")
    parser.add_argument("--summary", type=int, metavar="HOURS", help="Status summary for last N hours")
    parser.add_argument("--failures", type=int, metavar="HOURS", help="List failures in last N hours")
    parser.add_argument("--stale-warns", type=int, metavar="HOURS", help="List stale WARN pages")
    parser.add_argument("--trends", type=int, metavar="DAYS", help="Error trends over N days")
    parser.add_argument("--api-health", type=int, metavar="HOURS", help="API health summary")
    parser.add_argument("--page", metavar="PAGE_ID", help="QA history for a page")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = QADashboard()

    if args.summary:
        print(json.dumps(db.summary(args.summary), indent=2))
    elif args.failures:
        for f in db.failures(args.failures):
            print(f"  FAIL: {f['title']} ({f['page_id'][:12]}...) — {f['issues'][:80]}")
    elif args.stale_warns:
        for w in db.stale_warns(args.stale_warns):
            age_h = (time.time() - w["last_check"]) / 3600
            print(f"  WARN ({age_h:.0f}h): {w['title']} — {w['issues'][:60]}")
    elif args.trends:
        for t in db.error_trends(args.trends):
            print(f"  {t['cnt']}x: {t['issues'][:80]}")
    elif args.api_health:
        print(json.dumps(db.api_health_summary(args.api_health), indent=2))
    elif args.page:
        for entry in db.page_history(args.page):
            ts = datetime.fromtimestamp(entry["timestamp"], tz=timezone.utc).isoformat()
            print(f"  {ts} [{entry['qa_status']}] {entry['issues'][:60]}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
