"""Session environment tracking for continuity checks.

Stores environment snapshots (cwd, hostname, client info) each time
auto_recall is called. Enables detection of environment changes between
sessions — CWD drift, host change, client updates, etc.

Uses a lightweight SQLite database alongside ChromaDB.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of environment records to keep per project
MAX_RECORDS_PER_PROJECT = 50

# Long session gap threshold in hours
SESSION_GAP_WARNING_HOURS = 24


@dataclass
class SessionEnvironment:
    """Snapshot of the client environment at auto_recall time."""

    id: int
    project: str
    session_id: str | None
    cwd: str | None
    hostname: str | None
    client_name: str | None
    client_version: str | None
    recalled_at: str  # ISO datetime


class SessionEnvironmentStore:
    """SQLite-backed store for session environment records."""

    def __init__(self, db_path: str) -> None:
        """Initialize with the same base path as ChromaDB.

        Creates ``session_env.db`` inside the given directory.
        """
        db_dir = Path(db_path)
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_file = str(db_dir / "session_env.db")
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_file) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_environments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    session_id TEXT,
                    cwd TEXT,
                    hostname TEXT,
                    client_name TEXT,
                    client_version TEXT,
                    recalled_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_env_project
                ON session_environments(project, recalled_at DESC)
            """)
            conn.commit()

    def save(
        self,
        project: str,
        *,
        session_id: str | None = None,
        cwd: str | None = None,
        hostname: str | None = None,
        client_name: str | None = None,
        client_version: str | None = None,
    ) -> int:
        """Record current environment. Returns the new record ID."""
        # Normalize CWD: trailing slash, path separators
        if cwd:
            cwd = cwd.rstrip("/")
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_file) as conn:
            cur = conn.execute(
                """
                INSERT INTO session_environments
                    (project, session_id, cwd, hostname, client_name, client_version, recalled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (project, session_id, cwd, hostname, client_name, client_version, now),
            )
            record_id = cur.lastrowid or 0
            conn.commit()

        # Prune old records
        self._prune(project)
        return record_id

    def get_last(self, project: str) -> SessionEnvironment | None:
        """Get the most recent environment record for a project."""
        with sqlite3.connect(self._db_file) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM session_environments
                WHERE project = ?
                ORDER BY recalled_at DESC
                LIMIT 1
                """,
                (project,),
            ).fetchone()

        if row is None:
            return None
        return self._row_to_env(row)

    def get_previous(self, project: str, before_id: int) -> SessionEnvironment | None:
        """Get the environment record before the given record ID."""
        with sqlite3.connect(self._db_file) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM session_environments
                WHERE project = ? AND id < ?
                ORDER BY recalled_at DESC
                LIMIT 1
                """,
                (project, before_id),
            ).fetchone()

        if row is None:
            return None
        return self._row_to_env(row)

    def has_session_summary(self, project: str, store: Any) -> bool:
        """Check if the previous session left a summary memory.

        Args:
            project: Project identifier.
            store: MemoryStore instance to check for summary memories.
        """
        from memory_mcp.constants import MemoryType

        try:
            if not store.project_exists(project):
                return True  # New project, no expectation of summary
            summaries = store.get_recent(
                project=project,
                memory_type=MemoryType.SUMMARY,
                n_results=1,
            )
            return len(summaries) > 0
        except Exception:
            return True  # Assume OK if check fails

    def get_other_projects_at_cwd(
        self, cwd: str, current_project: str, limit: int = 5,
    ) -> list[str]:
        """Find other projects that have used the same CWD recently.

        Returns project names (excluding current_project) that have
        session records with the same CWD.  Used for multi-project
        collision detection.
        """
        normalized_cwd = cwd.rstrip("/")
        with sqlite3.connect(self._db_file) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT project FROM session_environments
                WHERE cwd = ? AND project != ?
                ORDER BY recalled_at DESC
                LIMIT ?
                """,
                (normalized_cwd, current_project, limit),
            ).fetchall()
        # Also check without trailing slash
        if not rows and cwd != normalized_cwd:
            with sqlite3.connect(self._db_file) as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT project FROM session_environments
                    WHERE cwd = ? AND project != ?
                    ORDER BY recalled_at DESC
                    LIMIT ?
                    """,
                    (cwd, current_project, limit),
                ).fetchall()
        return [r[0] for r in rows]

    def _prune(self, project: str) -> None:
        """Keep only the most recent MAX_RECORDS_PER_PROJECT records."""
        try:
            with sqlite3.connect(self._db_file) as conn:
                conn.execute(
                    """
                    DELETE FROM session_environments
                    WHERE project = ? AND id NOT IN (
                        SELECT id FROM session_environments
                        WHERE project = ?
                        ORDER BY recalled_at DESC
                        LIMIT ?
                    )
                    """,
                    (project, project, MAX_RECORDS_PER_PROJECT),
                )
                conn.commit()
        except Exception:
            logger.debug("Failed to prune session environments", exc_info=True)

    @staticmethod
    def _row_to_env(row: sqlite3.Row) -> SessionEnvironment:
        return SessionEnvironment(
            id=row["id"],
            project=row["project"],
            session_id=row["session_id"],
            cwd=row["cwd"],
            hostname=row["hostname"],
            client_name=row["client_name"],
            client_version=row["client_version"],
            recalled_at=row["recalled_at"],
        )
