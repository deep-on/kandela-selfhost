"""Persistent utilization tracking for Memory Activation (Phase MA-3).

Stores injection events and their outcomes in SQLite for cumulative
statistics — surviving server restarts unlike in-memory tracking.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class InjectionEvent:
    """In-memory representation of a gotcha injection event."""

    memory_ids: list[str]
    injection_type: str  # "pre_tool" | "milestone"
    context: str  # command snippet or milestone marker
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False
    utilized: bool | None = None  # True=followed, False=ignored, None=pending


class UtilizationStore:
    """SQLite-backed store for injection events and utilization stats."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS utilization_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project TEXT NOT NULL,
                        memory_id TEXT NOT NULL,
                        injection_type TEXT NOT NULL,
                        context TEXT,
                        injected_at REAL NOT NULL,
                        resolved_at REAL,
                        utilized INTEGER,
                        created_date TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_util_project_date
                    ON utilization_events(project, created_date)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_util_project_memory
                    ON utilization_events(project, memory_id)
                """)
                conn.commit()
            finally:
                conn.close()

    def record_injection(
        self,
        project: str,
        memory_ids: list[str],
        injection_type: str,
        context: str,
    ) -> None:
        """Record one or more memory injections."""
        now = time.time()
        date_str = time.strftime("%Y-%m-%d", time.localtime(now))
        with self._lock:
            conn = self._get_conn()
            try:
                for mid in memory_ids:
                    conn.execute(
                        """INSERT INTO utilization_events
                           (project, memory_id, injection_type, context,
                            injected_at, created_date)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (project, mid, injection_type, context[:500], now, date_str),
                    )
                conn.commit()
            finally:
                conn.close()

    def resolve_event(
        self,
        project: str,
        memory_id: str,
        utilized: bool,
        max_age_seconds: float = 300,
    ) -> int:
        """Resolve the most recent unresolved event for a memory.

        Returns number of rows updated (0 or 1).
        """
        now = time.time()
        cutoff = now - max_age_seconds
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """UPDATE utilization_events
                       SET resolved_at = ?, utilized = ?
                       WHERE id = (
                           SELECT id FROM utilization_events
                           WHERE project = ? AND memory_id = ?
                             AND resolved_at IS NULL
                             AND injected_at >= ?
                           ORDER BY injected_at DESC LIMIT 1
                       )""",
                    (now, int(utilized), project, memory_id, cutoff),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def expire_old_events(
        self, project: str, max_age_seconds: float = 300,
    ) -> int:
        """Mark old unresolved events as expired (utilized=NULL stays)."""
        cutoff = time.time() - max_age_seconds
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """UPDATE utilization_events
                       SET resolved_at = ?
                       WHERE project = ? AND resolved_at IS NULL
                         AND injected_at < ?""",
                    (time.time(), project, cutoff),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def get_stats(
        self, project: str, days: int | None = 7,
    ) -> dict[str, Any]:
        """Get utilization statistics for a project.

        Returns lifetime stats, recent stats, daily breakdown, and worst gotchas.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                return self._compute_stats(conn, project, days)
            finally:
                conn.close()

    def _compute_stats(
        self, conn: sqlite3.Connection, project: str, days: int | None,
    ) -> dict[str, Any]:
        # Lifetime stats
        row = conn.execute(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN utilized = 1 THEN 1 ELSE 0 END) as success,
                 SUM(CASE WHEN utilized = 0 THEN 1 ELSE 0 END) as failure,
                 SUM(CASE WHEN resolved_at IS NOT NULL AND utilized IS NULL THEN 1 ELSE 0 END) as expired
               FROM utilization_events WHERE project = ? AND resolved_at IS NOT NULL""",
            (project,),
        ).fetchone()

        total = row["total"] or 0
        success = row["success"] or 0
        failure = row["failure"] or 0
        expired = row["expired"] or 0
        resolved = success + failure

        lifetime = {
            "rate": round(success / resolved, 3) if resolved > 0 else None,
            "total": resolved,
            "success": success,
            "failure": failure,
            "expired": expired,
        }

        # Recent N days
        recent: dict[str, Any] = {"rate": None, "total": 0, "success": 0, "failure": 0}
        daily: list[dict[str, Any]] = []

        if days:
            date_cutoff = time.strftime(
                "%Y-%m-%d",
                time.localtime(time.time() - days * 86400),
            )

            row_r = conn.execute(
                """SELECT
                     SUM(CASE WHEN utilized = 1 THEN 1 ELSE 0 END) as success,
                     SUM(CASE WHEN utilized = 0 THEN 1 ELSE 0 END) as failure
                   FROM utilization_events
                   WHERE project = ? AND resolved_at IS NOT NULL
                     AND created_date >= ?""",
                (project, date_cutoff),
            ).fetchone()

            r_success = row_r["success"] or 0
            r_failure = row_r["failure"] or 0
            r_total = r_success + r_failure
            recent = {
                "rate": round(r_success / r_total, 3) if r_total > 0 else None,
                "total": r_total,
                "success": r_success,
                "failure": r_failure,
            }

            # Daily breakdown
            rows_d = conn.execute(
                """SELECT created_date,
                     SUM(CASE WHEN utilized = 1 THEN 1 ELSE 0 END) as success,
                     SUM(CASE WHEN utilized = 0 THEN 1 ELSE 0 END) as failure
                   FROM utilization_events
                   WHERE project = ? AND resolved_at IS NOT NULL
                     AND created_date >= ?
                   GROUP BY created_date
                   ORDER BY created_date DESC""",
                (project, date_cutoff),
            ).fetchall()

            for rd in rows_d:
                d_s = rd["success"] or 0
                d_f = rd["failure"] or 0
                d_t = d_s + d_f
                daily.append({
                    "date": rd["created_date"],
                    "total": d_t,
                    "success": d_s,
                    "failure": d_f,
                    "rate": round(d_s / d_t, 3) if d_t > 0 else None,
                })

        # Worst gotchas (most ignored)
        rows_w = conn.execute(
            """SELECT memory_id,
                 SUM(CASE WHEN utilized = 0 THEN 1 ELSE 0 END) as failure_count,
                 SUM(CASE WHEN utilized = 1 THEN 1 ELSE 0 END) as success_count,
                 MAX(context) as sample_context
               FROM utilization_events
               WHERE project = ? AND resolved_at IS NOT NULL
               GROUP BY memory_id
               HAVING failure_count > 0
               ORDER BY failure_count DESC
               LIMIT 5""",
            (project,),
        ).fetchall()

        worst: list[dict[str, Any]] = []
        for rw in rows_w:
            fc = rw["failure_count"] or 0
            sc = rw["success_count"] or 0
            t = fc + sc
            worst.append({
                "memory_id": rw["memory_id"],
                "context_snippet": (rw["sample_context"] or "")[:100],
                "failure_count": fc,
                "rate": round(sc / t, 3) if t > 0 else 0,
            })

        return {
            "lifetime": lifetime,
            "recent": recent,
            "daily": daily,
            "worst_gotchas": worst,
        }
